# BidiAgent on AWS - AgentCore Runtime デプロイ設計書

## 概要

Strands AgentsのBidiAgent（双方向ストリーミングエージェント）をAmazon Bedrock AgentCore Runtimeにデプロイするための設計ドキュメント。

---

## 調査結果

### 1. AgentCore Runtime の基本要件

#### 必須エンドポイント

| エンドポイント | メソッド | 用途 |
|--------------|---------|------|
| `/invocations` | POST | エージェント呼び出し（標準） |
| `/ping` | GET | ヘルスチェック |
| `/ws` | WebSocket | 双方向ストリーミング |

#### 必須パッケージ（requirements.txt）

```txt
bedrock-agentcore
strands-agents
strands-agents-tools
```

---

### 2. デコレータの種類と用途

#### `@app.entrypoint` - 標準的なリクエスト/レスポンス

```python
@app.entrypoint
def invoke(payload, context):
    """POST /invocations で呼び出される"""
    user_message = payload.get("prompt", "Hello")
    return {"result": result}
```

#### `@app.websocket` - WebSocket双方向ストリーミング

```python
@app.websocket
async def handler(websocket, context):
    """GET /ws で呼び出される"""
    await websocket.accept()
    # WebSocket処理
```

**重要:** `@app.websocket` デコレータを使用する場合、関数は以下の引数を受け取る必要がある：
- `websocket` - Starlette WebSocketオブジェクト
- `context` - RequestContext（session_id, headers等を含む）

---

### 3. BidiAgent の特性

#### 設計上の考慮事項

| 項目 | 説明 |
|------|------|
| **ステータス** | Experimental（実験的機能） |
| **用途** | リアルタイム双方向ストリーミング会話 |
| **対応モデル** | Nova Sonic（音声対応） |
| **標準I/O** | `BidiAudioIO`（マイク/スピーカー）、`BidiTextIO`（コンソール） |

#### Agent vs BidiAgent

| 項目 | Agent | BidiAgent |
|------|-------|-----------|
| パターン | リクエスト/レスポンス | 双方向ストリーミング |
| 接続 | 都度接続 | 永続接続 |
| 操作単位 | メッセージ | イベント |
| ツール実行 | 順次・ブロッキング | 並行・ノンブロッキング |

---

### 4. WebSocket ハンドラの正しい実装

#### 問題点（修正前）

```python
@app.websocket
async def main() -> None:  # 引数がない
    ...
```

#### 正しい実装

```python
@app.websocket
async def websocket_handler(websocket: WebSocket, context):  # 2つの引数
    await websocket.accept()
    ...
```

---

### 5. AgentCore Runtime へのデプロイ手順

#### Step 1: CLI ツールのインストール

```bash
pip install bedrock-agentcore-starter-toolkit
```

#### Step 2: 設定

```bash
agentcore configure --entrypoint main.py --non-interactive
```

#### Step 3: ローカルテスト（オプション、Docker必要）

```bash
agentcore launch --local
```

#### Step 4: デプロイ

```bash
agentcore launch
```

#### Step 5: 動作確認

```bash
agentcore invoke '{"prompt": "Hello"}'
agentcore status
```

---

### 6. 現時点での制約と注意点

#### 制約

1. **公式サンプルの不在**: BidiAgent + WebSocket を AgentCore Runtime にデプロイする公式サンプルは現時点で存在しない

2. **カスタムI/Oの必要性**: `BidiAudioIO` はローカルのマイク/スピーカーを使用する設計のため、AgentCore Runtime上ではクライアントからWebSocket経由で音声データを受け渡すカスタムI/Oアダプタが必要になる可能性がある

3. **実験的機能**: BidiAgentは実験的機能として位置づけられており、将来のバージョンで変更される可能性がある

#### WebSocket接続の認証

AgentCore Runtimeは以下の認証方式をサポート：

- **SigV4署名**: AWS認証情報を使用
- **OAuth**: Bearer トークンを使用
- **Presigned URL**: フロントエンドクライアント向け

```python
from bedrock_agentcore.runtime import AgentCoreRuntimeClient

client = AgentCoreRuntimeClient(region='us-west-2')

# SigV4認証
ws_url, headers = client.generate_ws_connection(
    runtime_arn='arn:aws:bedrock-agentcore:us-west-2:123:runtime/my-runtime',
    endpoint_name='DEFAULT'
)

# Presigned URL（フロントエンド向け）
presigned_url = client.generate_presigned_url(
    runtime_arn='arn:aws:bedrock-agentcore:us-west-2:123:runtime/my-runtime',
    expires=300
)
```

---

### 7. 実装方針

#### 方針A: WebSocketハンドラ内でBidiAgentを動作させる

クライアントからのWebSocket接続を受け付け、BidiAgentのイベントを中継する。

**メリット:**
- リアルタイム双方向通信が可能
- BidiAgentの機能をフル活用

**デメリット:**
- カスタムI/Oアダプタの実装が必要
- 複雑性が高い

#### 方針B: 標準エントリポイントでストリーミングレスポンスを使用

`@app.entrypoint` で `agent.stream_async()` を使用する。

**メリット:**
- 公式サポートされている
- 実装がシンプル

**デメリット:**
- リアルタイム音声I/Oには対応しない
- 双方向ではなく片方向ストリーミング

---

### 8. 参考リソース

- [Strands Agents - BidiAgent Documentation](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/experimental/bidirectional-streaming/agent/index.md)
- [AgentCore Runtime API Reference](https://aws.github.io/bedrock-agentcore-starter-toolkit/api-reference/runtime.md)
- [Strands Agents - Deploy to AgentCore](https://strandsagents.com/latest/documentation/docs/user-guide/deploy/deploy_to_bedrock_agentcore/python/index.md)
- [AgentCore Runtime Deployment Guide](https://aws.github.io/bedrock-agentcore-starter-toolkit/mcp/agentcore_runtime_deployment.md)

---

## 次のステップ

1. WebSocketハンドラの正しいシグネチャで実装を修正
2. ローカルでの動作確認
3. カスタムI/Oアダプタの検討（音声データのWebSocket経由での送受信）
4. AgentCore Runtimeへのデプロイとテスト
