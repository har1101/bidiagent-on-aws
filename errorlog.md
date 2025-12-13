# BidiAgent on AWS - 実装ドキュメント

## 概要

BidiAgent（双方向ストリーミングエージェント）をBedrockAgentCoreApp上でWebSocket経由で動作させる実装。

---

## 用語解説

### Strands Agents SDK

| 用語 | 説明 |
|------|------|
| **Strands Agents** | AWSが提供するオープンソースのAIエージェントSDK。Bedrockモデルと連携してエージェントを構築できる |
| **BidiAgent** | Bidirectional（双方向）Agent。リアルタイムの音声・テキストストリーミング会話用のエージェント。**Experimental機能** |
| **BidiNovaSonicModel** | Amazon Nova Sonic（音声モデル）用のBidiAgent対応モデルラッパー |
| **BidiInput / BidiOutput** | 双方向ストリーミングの入出力チャネル。音声やテキストのイベントを送受信する |
| **BidiAudioIO** | ローカルのマイク・スピーカーを使用するI/O（コンテナ環境では使用不可） |

### AWS Bedrock AgentCore

| 用語 | 説明 |
|------|------|
| **AgentCore Runtime** | 任意のOSSエージェント（Strands, LangChain等）をサーバレスでホスティングするAWSサービス |
| **BedrockAgentCoreApp** | AgentCore Runtime用のPythonアプリケーションフレームワーク |
| **@app.websocket** | WebSocketエンドポイント（`/ws`）を定義するデコレータ。port 8080固定 |
| **RequestContext** | WebSocketハンドラに渡されるコンテキスト（session_id, request_headers等） |

### 音声フォーマット用語

| 用語 | 説明 |
|------|------|
| **PCM** | Pulse Code Modulation（パルス符号変調）。非圧縮のデジタル音声形式。WAVファイルの中身もPCM |
| **sample_rate** | サンプリングレート。1秒間に何回音声をサンプリングするか。16000 = 16kHz（電話品質相当） |
| **channels** | チャンネル数。1=モノラル、2=ステレオ |
| **bit depth** | ビット深度。各サンプルの精度。16bit = -32768〜32767の範囲で音量を表現 |
| **base64** | バイナリデータをASCII文字列に変換するエンコード方式。JSONで音声データを送るために必要 |

---

## 全体アーキテクチャ

### 構成図

```
┌─────────────────┐     WebSocket      ┌─────────────────────────────────┐
│  クライアント    │ ◄───────────────► │  BedrockAgentCoreApp            │
│  (PyAudio)      │   JSON Events      │  └── BidiAgent                  │
│                 │                    │      └── BidiNovaSonicModel     │
│  マイク → 音声送信                   │          └── Nova Sonic API     │
│  スピーカー ← 音声受信               │                                 │
└─────────────────┘                    └─────────────────────────────────┘
```

### それぞれの役割

#### Strands Agents / BidiAgent
- BidiAgentは音声・テキストをリアルタイムにやりとりするためのエージェント
- BedrockのNova Sonicなどの双方向ストリーミングモデルと組み合わせて音声アシスタントを作れる
- 入出力はBidiInput/BidiOutputというI/Oチャネル経由で行う
- ローカル用には`BidiAudioIO`（マイク・スピーカー）、`BidiTextIO`（ターミナル）がある
- **WebSocket経由の場合は`websocket.receive_json`/`websocket.send_json`を直接使用**

#### AgentCore Runtime
- 任意のOSSエージェントを安全にホスティングするためのサーバレスランタイム
- WebSocketモードでは、コンテナ側にport 8080の`/ws`エンドポイントがある前提
- `BedrockAgentCoreApp`の`@app.websocket`デコレータを使うと、この条件に合わせて`/ws`を自動で生成

#### なぜWebSocket I/Oが必要か
ローカル用のサンプル：
```python
agent = BidiAgent(model=BidiNovaSonicModel())
audio_io = BidiAudioIO()
await agent.run(inputs=[audio_io.input()], outputs=[audio_io.output()])
```
これは「コンテナ内のマイク・スピーカー」を前提としており、**AgentCoreのコンテナにはマイクがない**ため使用不可。

そこで：
- BidiAgent自体はサーバ側（AgentCoreコンテナ）に置く
- I/OはWebSocketに差し替え
- クライアントがマイク入力を取り、BidiAudioInputEvent等のJSONをWebSocketで送信する

### サーバー実装（推奨パターン）

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.websockets import WebSocket, WebSocketDisconnect

from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel
from strands.experimental.bidi.tools import stop_conversation

app = BedrockAgentCoreApp()

@app.websocket
async def websocket_handler(websocket: WebSocket, context):
    await websocket.accept()

    model = BidiNovaSonicModel(
        model_id='amazon.nova-2-sonic-v1:0',
        provider_config={"audio": {"voice": "tiffany"}},
    )

    agent = BidiAgent(
        model=model,
        tools=[...],
        system_prompt="You are a helpful assistant.",
    )

    try:
        # Strandsドキュメントの推奨パターン
        await agent.run(
            inputs=[websocket.receive_json],
            outputs=[websocket.send_json],
        )
    except WebSocketDisconnect:
        pass
    finally:
        await agent.stop()

if __name__ == "__main__":
    app.run()
```

### 重要ポイント

1. **`agent.run()`パターンを使用**
   - `inputs=[websocket.receive_json]` - WebSocketから直接JSON受信
   - `outputs=[websocket.send_json]` - WebSocketへ直接JSON送信
   - カスタムI/Oクラスは不要

2. **`@app.websocket`デコレータのシグネチャ**
   ```python
   async def handler(websocket: WebSocket, context):
   ```

3. **モデルID**
   - Nova Sonic: `amazon.nova-2-sonic-v1:0`

---

## イベント形式（Strands Bidi Events）

双方向ストリーミングでは、BidiTextInputEvent・BidiAudioInputEvent等のイベントをJSON形式で送受信する。

### クライアント → サーバー（入力イベント）

#### BidiAudioInputEvent（音声入力）

```json
{
    "type": "bidi_audio_input",
    "audio": "<base64 encoded PCM>",
    "format": "pcm",
    "sample_rate": 16000,
    "channels": 1
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `type` | string | イベント種別。必ず`"bidi_audio_input"` |
| `audio` | string | **base64エンコードされたPCM音声データ**。生のバイト列をbase64でエンコードした文字列 |
| `format` | string | 音声フォーマット。`"pcm"`, `"wav"`, `"opus"`, `"mp3"`のいずれか |
| `sample_rate` | number | サンプリングレート（Hz）。`16000`, `24000`, `48000`のいずれか |
| `channels` | number | チャンネル数。`1`=モノラル、`2`=ステレオ |

**音声データの流れ:**
```
マイク → PCMバイト列 → base64エンコード → JSON文字列 → WebSocket送信
         (16bit/16kHz)   (文字列に変換)     (audio フィールド)
```

**Pythonでの実装例:**
```python
import base64

# PyAudioから取得したPCMバイト列
audio_bytes = stream.read(512)  # 512フレーム分のPCMデータ

# base64エンコードしてJSONに含める
message = {
    "type": "bidi_audio_input",
    "audio": base64.b64encode(audio_bytes).decode("utf-8"),
    "format": "pcm",
    "sample_rate": 16000,
    "channels": 1
}
await websocket.send(json.dumps(message))
```

#### BidiTextInputEvent（テキスト入力）

```json
{
    "type": "bidi_text_input",
    "text": "こんにちは",
    "role": "user"
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `type` | string | イベント種別。必ず`"bidi_text_input"` |
| `text` | string | 入力テキスト |
| `role` | string | 発話者。`"user"`固定 |

### サーバー → クライアント（出力イベント）

#### BidiAudioStreamEvent（音声出力）

```json
{
    "type": "bidi_audio_stream",
    "audio": "<base64 encoded PCM>",
    "format": "pcm",
    "sample_rate": 16000,
    "channels": 1
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `type` | string | イベント種別。`"bidi_audio_stream"` |
| `audio` | string | base64エンコードされた音声データ |
| `format` | string | 音声フォーマット |
| `sample_rate` | number | サンプリングレート（Hz） |
| `channels` | number | チャンネル数 |

**音声再生の流れ:**
```
WebSocket受信 → JSON解析 → base64デコード → PCMバイト列 → スピーカー再生
                            (audio フィールド)   (16bit/16kHz)
```

#### BidiTranscriptStreamEvent（トランスクリプト）

音声の文字起こし結果。ユーザーの発話とアシスタントの応答両方が流れてくる。

```json
{
    "type": "bidi_transcript_stream",
    "text": "こんにちは",
    "role": "user|assistant",
    "is_final": true,
    "delta": {"text": "..."},
    "current_transcript": "..."
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| `type` | string | イベント種別。`"bidi_transcript_stream"` |
| `text` | string | トランスクリプトテキスト |
| `role` | string | 発話者。`"user"`または`"assistant"` |
| `is_final` | boolean | 確定したトランスクリプトかどうか。`false`は中間結果 |
| `delta` | object | 差分テキスト |
| `current_transcript` | string | これまでの累積トランスクリプト |

#### BidiConnectionStartEvent（接続開始）

WebSocket接続が確立し、モデルとの通信が開始されたことを示す。

```json
{
    "type": "bidi_connection_start",
    "connection_id": "conn_abc123",
    "model": "amazon.nova-2-sonic-v1:0"
}
```

#### BidiResponseStartEvent / BidiResponseCompleteEvent

モデルの応答開始/終了を示す。

```json
{"type": "bidi_response_start", "response_id": "resp_xyz"}
{"type": "bidi_response_complete", "response_id": "resp_xyz", "stop_reason": "complete"}
```

`stop_reason`の値:
- `"complete"`: 正常終了
- `"interrupted"`: ユーザーが割り込んだ（発話した）
- `"tool_use"`: ツール実行中
- `"error"`: エラー発生

#### BidiUsageEvent（使用量）

トークン使用量。定期的に送信される。

```json
{
    "type": "bidi_usage",
    "inputTokens": 150,
    "outputTokens": 75,
    "totalTokens": 225
}
```

#### BidiErrorEvent（エラー）

```json
{
    "type": "bidi_error",
    "message": "Error message",
    "code": "ErrorCode"
}
```

#### ToolUseStreamEvent（ツール使用）

エージェントがツールを呼び出す際に送信される。

```json
{
    "type": "tool_use_stream",
    "current_tool_use": {
        "toolUseId": "tool_123",
        "name": "calculator",
        "input": {"expression": "2+2"}
    }
}
```

---

## 音声フォーマット

| 項目 | 値 |
|------|-----|
| フォーマット | PCM |
| サンプルレート | 16000 Hz |
| チャンネル | 1（モノラル） |
| ビット深度 | 16bit（paInt16） |
| チャンクサイズ | 512 frames |

---

## ファイル構成

```
bidiagent-on-aws/
├── main.py                          # メインサーバー（AgentCore Runtime用）
├── design.md                        # 設計ドキュメント
├── errorlog.md                      # このファイル
├── requirements.txt                 # 依存パッケージ
├── cdk/                             # CDKデプロイ用
│   └── bidiagent/
│       ├── Dockerfile               # AgentCore用Dockerfile
│       └── requirements.txt         # コンテナ用依存パッケージ
└── test/
    ├── websocket_agent_client.py    # ローカルテスト用クライアント（PyAudio）
    ├── simple_ws_server.py          # ローカルテストサーバー（BedrockAgentCoreApp）
    └── agentcore_client.py          # AgentCore Runtime接続用クライアント（本番用）
```

---

## 依存パッケージ

```
strands-agents[bidi-all]   # BidiAgent + Nova Sonic
bedrock-agentcore          # AgentCore Runtime SDK
strands-agents-tools       # ツール（calculator, http_request等）
pyaudio                    # クライアント側音声I/O
```

---

## ローカルテスト方法

```bash
# ターミナル1: サーバー起動
uv run test/simple_ws_server.py

# ターミナル2: クライアント接続
uv run test/websocket_agent_client.py
```

---

## AgentCore Runtimeへのデプロイ

### プロジェクト構成

```
your_project_directory/
├── main.py              # エントリポイント（上記のサーバー実装）
└── requirements.txt     # 依存パッケージ
```

### AgentCore Starter Toolkitのインストール

```bash
pip install bedrock-agentcore-starter-toolkit
```

### エージェントの設定とデプロイ

```bash
# エントリポイントを登録
agentcore configure --entrypoint main.py

# ローカルコンテナでテスト（Docker/Finch/Podmanが必要）
agentcore launch --local

# 本番用にAgentCore Runtimeへデプロイ
agentcore launch
```

デプロイ完了後、`agentRuntimeArn`が返される：
```
arn:aws:bedrock-agentcore:us-west-2:<account-id>:runtime/your-agent-name-xxxxxx
```

### 注意事項

- コンテナは**ARM64 / port 8080**前提
- DockerfileでARCH設定: `--platform=linux/arm64`
- IAMロール（AgentRuntimeRole）にBedrock Nova Sonicへのアクセス権限が必要
- 音声フォーマット設定（sample_rate, channels等）はクライアントとサーバーで整合を取る

---

## クライアントからの接続（本番環境）

AgentCoreのWebSocket APIはSigV4署名付きURLで接続する。

### 音声クライアント（test/agentcore_client.py）

```bash
# 環境変数でARNを設定
export AGENT_ARN="arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/your-agent-name"

# 音声モード（デフォルト）- マイクで話しかけて音声で返答
python test/agentcore_client.py

# テキストモード（デバッグ用）
python test/agentcore_client.py --text

# リージョン指定
python test/agentcore_client.py --region us-west-2

# ARNを直接指定
python test/agentcore_client.py --arn "arn:aws:bedrock-agentcore:..."
```

### Pythonコード例

```python
import asyncio
import json
import os

import websockets
from bedrock_agentcore.runtime import AgentRuntimeClient

async def main():
    runtime_arn = os.environ["AGENT_ARN"]  # デプロイ時に得たARN
    client = AgentRuntimeClient(region="ap-northeast-1")

    # SigV4署名付きWebSocket URLを生成
    ws_url, headers = client.generate_ws_connection(runtime_arn=runtime_arn)

    async with websockets.connect(ws_url, extra_headers=headers) as ws:
        # テキスト入力の例
        await ws.send(json.dumps({
            "type": "bidi_text_input",
            "text": "こんにちは、自己紹介して",
            "role": "user"
        }))

        while True:
            msg = await ws.recv()
            event = json.loads(msg)
            print(event)

            if event.get("type") == "bidi_transcript_stream" and event.get("is_final"):
                break

if __name__ == "__main__":
    asyncio.run(main())
```

### Webフロントエンドからの接続

バックエンドで`generate_presigned_url()`を呼んで署名付きWebSocket URLを発行し、ブラウザ側では`new WebSocket(presigned_url)`で接続できる。

---

## 解決済みエラー履歴

### エラー1: 音声データタイムアウト
- **原因**: Nova Sonicはテキストではなく音声データを期待
- **解決**: PyAudioでマイク入力を取得し、PCM音声を送信

### エラー2: BidiAudioInputEvent必須パラメータ不足
- **原因**: `format`, `sample_rate`, `channels`が必須
- **解決**: 全必須パラメータをJSONに含める

### エラー3: agent.run()直後のキャンセル
- **原因**: カスタムI/Oクラスの実装が不適切
- **解決**: `websocket.receive_json`/`websocket.send_json`を直接使用

### エラー4: EC.decode_der_signature_to_padded_pair AttributeError
- **エラー内容**:
  ```
  AttributeError: type object 'EC' has no attribute 'decode_der_signature_to_padded_pair'
  ```
- **原因**: `BidiAudioIO`/`BidiTextIO`をインポートすると、AWS SSO認証時に`awscrt`ライブラリの互換性問題が発生
- **解決**:
   - 不要な`BidiAudioIO`/`BidiTextIO`のインポートを削除する

```python
# 間違い（BidiAudioIOをインポートするとエラー）
from strands.experimental.bidi.io import BidiAudioIO, BidiTextIO  # ← これが問題
```

---

## 参考リンク

- [Strands Agents - BidiAgent](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/experimental/bidirectional-streaming/agent/)
- [Strands Agents - Events](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/experimental/bidirectional-streaming/events/)
- [Strands Agents - I/O Channels](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/experimental/bidirectional-streaming/io/)
- [Strands Agents - Nova Sonic](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/experimental/bidirectional-streaming/models/nova_sonic/)
- [AgentCore Runtime](https://aws.github.io/bedrock-agentcore-starter-toolkit/)
