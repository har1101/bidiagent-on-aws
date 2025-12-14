# AgentCore Runtime WebSocket クライアント 完全解説

このドキュメントでは、AWS Bedrock AgentCore Runtime にデプロイした音声エージェントと通信するクライアントの実装を、初学者でも理解できるよう詳細に解説します。

---

## 目次

1. [このクライアントは何をするのか](#1-このクライアントは何をするのか)
2. [全体アーキテクチャ](#2-全体アーキテクチャ)
3. [使用ライブラリの解説](#3-使用ライブラリの解説)
4. [音声データの基礎知識](#4-音声データの基礎知識)
5. [コード詳細解説](#5-コード詳細解説)
6. [非同期処理の仕組み](#6-非同期処理の仕組み)
7. [イベント形式の詳細](#7-イベント形式の詳細)
8. [トラブルシューティング](#8-トラブルシューティング)

---

## 1. このクライアントは何をするのか

### 一言で説明すると

**あなたのマイクの声をAIエージェントに送り、AIの返答を音声でスピーカーから再生する**プログラムです。

### 電話に例えると

```
あなた（クライアント）          電話回線（WebSocket）          AIエージェント（サーバー）
      📱 ─────────────────────────────────────────────────────────── 🤖

   マイクで話す                    声を送信                     声を受け取る
      ↓                              →                            ↓
   録音する                                                    理解する
      ↓                                                           ↓
   データに変換                                                 返答を生成
      ↓                              ←                            ↓
   スピーカーで聞く               返答を受信                    声に変換
```

普通の電話と違うのは、相手がAIであること、そしてインターネット（WebSocket）を通じてやり取りすることです。

---

## 2. 全体アーキテクチャ

### システム構成図

```
┌─────────────────────────────────────────────────────────────────────┐
│  あなたのPC（クライアント）                                          │
│                                                                     │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐         │
│  │   マイク    │ ───→ │ AudioRecorder│ ───→ │   Queue     │         │
│  │   🎤        │      │  (録音係)    │      │ (待ち行列)  │         │
│  └─────────────┘      └─────────────┘      └──────┬──────┘         │
│                                                    │                │
│                                                    ↓                │
│                                            ┌─────────────┐         │
│                                            │ send_audio  │         │
│                                            │ (送信係)    │         │
│                                            └──────┬──────┘         │
│                                                    │                │
└────────────────────────────────────────────────────┼────────────────┘
                                                     │
                                                     │ WebSocket
                                                     │ (双方向通信)
                                                     ↓
┌─────────────────────────────────────────────────────────────────────┐
│  AWS AgentCore Runtime（サーバー）                                   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    BidiAgent + Nova Sonic                    │   │
│  │                                                              │   │
│  │   音声入力 → 音声認識(STT) → AI処理 → 音声合成(TTS) → 音声出力  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
                                                     │
                                                     │ WebSocket
                                                     │
┌────────────────────────────────────────────────────┼────────────────┐
│                                                    ↓                │
│                                            ┌─────────────┐         │
│                                            │receive_msgs │         │
│                                            │ (受信係)    │         │
│                                            └──────┬──────┘         │
│                                                    │                │
│                                                    ↓                │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐         │
│  │ スピーカー  │ ←─── │ AudioPlayer │ ←─── │  Base64    │         │
│  │   🔊        │      │  (再生係)   │      │  デコード   │         │
│  └─────────────┘      └─────────────┘      └─────────────┘         │
│                                                                     │
│  あなたのPC（クライアント）                                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 主要コンポーネントの役割

| コンポーネント | 役割 | 例え |
|--------------|------|-----|
| AudioRecorder | マイクから音声を録音 | 録音係 |
| Queue | 録音した音声を一時保管 | 郵便受け |
| send_audio | 音声データをサーバーに送信 | 配達員 |
| WebSocket | サーバーとの双方向通信 | 電話回線 |
| receive_messages | サーバーからのデータを受信 | 受付係 |
| AudioPlayer | 受信した音声をスピーカーで再生 | 再生係 |

---

## 3. 使用ライブラリの解説

### インポート部分

```python
import asyncio          # 非同期処理（後述）
import websockets       # WebSocket通信
import json             # JSONデータの変換
import base64           # バイナリ↔テキスト変換
import sys              # システム操作
import os               # 環境変数の取得
import queue            # スレッド間のデータ受け渡し

from bedrock_agentcore.runtime import AgentCoreRuntimeClient  # AWS認証
import pyaudio          # 音声入出力
```

### 各ライブラリの解説

#### asyncio（非同期処理）

**普通のプログラム（同期処理）の問題点：**
```
1. マイクから録音 ← 終わるまで待つ
2. サーバーに送信 ← 終わるまで待つ
3. 返答を受信    ← 終わるまで待つ
4. スピーカーで再生 ← 終わるまで待つ
5. 1に戻る
```

これだと、録音中は受信できない、受信中は録音できない...となってしまいます。

**非同期処理なら：**
```
録音 ─────────────────────────────→
     送信 ────────────────────────→
          受信 ───────────────────→
               再生 ──────────────→
```

複数の作業を「同時っぽく」進められます。実際には高速に切り替えているだけですが、人間には同時に動いているように見えます。

#### websockets（WebSocket通信）

**HTTPとの違い：**

```
【HTTP（普通のWebアクセス）】
クライアント: 「データください」 → サーバー
クライアント ← 「はいどうぞ」    : サーバー
（接続終了）

クライアント: 「また欲しい」    → サーバー
クライアント ← 「はいどうぞ」    : サーバー
（接続終了）

毎回「接続→リクエスト→レスポンス→切断」を繰り返す
```

```
【WebSocket】
クライアント: 「繋ぎっぱなしにしよう」 → サーバー
クライアント ←→ サーバー （接続維持）
クライアント: 「データ1」 →
クライアント ← 「返答1」
クライアント: 「データ2」 →
クライアント ← 「返答2」
...いつでも双方向に送受信可能...
クライアント: 「終わり」 →
（接続終了）
```

音声通話のようなリアルタイム通信にはWebSocketが最適です。

#### base64（エンコーディング）

**なぜ必要か：**

音声データは**バイナリデータ**（0と1の羅列）です。
```
音声データ: 0x48 0x65 0x6c 0x6c 0x6f ...（人間には読めない）
```

JSONは**テキスト**しか扱えません。
```json
{"audio": ここにバイナリは入れられない}
```

Base64は、バイナリデータをテキスト（A-Z, a-z, 0-9, +, /）に変換します：
```
バイナリ: 0x48 0x65 0x6c 0x6c 0x6f
   ↓ Base64エンコード
テキスト: "SGVsbG8="
   ↓ JSONに入れられる！
{"audio": "SGVsbG8="}
```

**例え話：**
荷物（バイナリ）を郵便で送りたいが、手紙（テキスト）しか送れない郵便局。
荷物を写真に撮って（Base64エンコード）手紙に貼り付けて送る、というイメージ。

#### pyaudio（音声入出力）

PCのマイクとスピーカーを制御するライブラリです。

- **入力（録音）**: マイクからの音声をプログラムで扱えるデータに変換
- **出力（再生）**: プログラムのデータをスピーカーから音として出力

---

## 4. 音声データの基礎知識

### 音声とは「空気の振動」

音は空気の振動です。この振動を電気信号に変換したものが「アナログ音声信号」です。

```
空気の振動 → マイク → 電気信号（アナログ）
                           ↑
                    波の形をしている
```

### デジタル化の仕組み

コンピュータはアナログ信号を直接扱えません。デジタル化（数値化）が必要です。

```
アナログ信号                    デジタル信号
    ~~~                         ■ ■ ■
   ~   ~                      ■       ■
  ~     ~    ─────────→     ■           ■
           「サンプリング」
           （一定間隔で値を取る）
```

### サンプルレート（Sample Rate）= 16000 Hz

**定義**: 1秒間に何回、音の値を記録するか

```
1秒間の音声波形:
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

サンプルレート 16000Hz の場合:
1秒間に16000回、値を記録する

・・・・・・・・・・・・・・・・・・・・・・（16000個の点）
```

**例え話：**
パラパラ漫画を想像してください。1秒間に何枚の絵を描くかがサンプルレートです。
- 16000Hz = 1秒間に16000枚の絵
- 数が多いほど滑らかだが、データ量も増える

**なぜ16kHz？**
- 電話品質: 8kHz
- FM放送: 22kHz
- CD音質: 44.1kHz
- **音声認識: 16kHz**（人の声の認識に十分な品質）

Nova Sonicは16kHzを要求します。

### チャンネル（Channels）= 1（モノラル）

**定義**: 音声の経路の数

```
モノラル (1ch):    ●──────→ 🔊
                   1つの音源

ステレオ (2ch):    ●──────→ 🔊左
                   ●──────→ 🔊右
                   2つの音源（左右）
```

音声認識ではモノラルで十分です。ステレオにするとデータ量が2倍になるだけ。

### ビット深度（Bit Depth）= 16bit

**定義**: 1回のサンプリングで、音の大きさを何段階で記録するか

```
8bit  = 2^8  = 256段階
16bit = 2^16 = 65536段階  ← 今回使用
24bit = 2^24 = 16777216段階
```

**例え話：**
絵の具の色数と同じ。8色より65536色の方が、細かい色の違いを表現できる。
音で言えば、「ささやき声」と「大声」の間を何段階で表現できるか。

### PCM（Pulse Code Modulation）

**定義**: デジタル音声の生データ形式

「圧縮していない、そのままの数値の羅列」のこと。

```
MP3, AACなど: 圧縮されている（データ量小、処理必要）
PCM:         圧縮なし（データ量大、そのまま使える）
```

Nova SonicはPCM形式を要求します。

### チャンクサイズ（Chunk Size）= 512

**定義**: 一度に処理する音声データの単位（フレーム数）

```
16kHz, 512フレームの場合:
512 ÷ 16000 = 0.032秒 = 32ミリ秒分の音声

つまり、32ミリ秒ごとに音声データを送信する
```

**例え話：**
手紙を1文字ずつ送るか、1ページずつ送るかの違い。
- 小さすぎる: 送信回数が増えて効率が悪い
- 大きすぎる: 遅延が発生する（溜まるまで待つから）
- 512は良いバランス

### データサイズの計算

```
1秒間のデータサイズ:
= サンプルレート × チャンネル数 × (ビット深度 ÷ 8)
= 16000 × 1 × (16 ÷ 8)
= 16000 × 1 × 2
= 32000バイト
= 約32KB/秒
```

1分間の通話で約1.9MB、10分で約19MBのデータが送受信されます。

---

## 5. コード詳細解説

### 5.1 オーディオ設定

```python
# オーディオ設定
# Nova Sonicは入出力ともに16kHz
SAMPLE_RATE = 16000        # 16kHz
INPUT_SAMPLE_RATE = SAMPLE_RATE
OUTPUT_SAMPLE_RATE = SAMPLE_RATE
CHANNELS = 1               # モノラル
CHUNK_SIZE = 512           # フレームサイズ
FORMAT = pyaudio.paInt16   # 16bit PCM
```

| 設定 | 値 | 意味 |
|-----|-----|-----|
| SAMPLE_RATE | 16000 | 1秒間に16000回サンプリング |
| CHANNELS | 1 | モノラル（1チャンネル） |
| CHUNK_SIZE | 512 | 一度に512フレーム処理（約32ms分） |
| FORMAT | paInt16 | 16ビット整数でサンプル値を表現 |

**重要**: サーバー（Nova Sonic）とクライアントでこれらの設定が一致していないと、
- 音が速くなる/遅くなる
- 音が高くなる/低くなる
- ノイズだらけになる

といった問題が発生します。

### 5.2 AudioPlayer クラス（音声再生・割り込み対応）

```python
class AudioPlayer:
    """受信した音声データを再生するクラス（非同期再生対応）

    別スレッドで音声を再生することで、メインの受信処理をブロックしない。
    割り込み時にはキューをクリアして即座に再生を停止できる。
    """

    def __init__(self, sample_rate: int = OUTPUT_SAMPLE_RATE):
        ...
        self._audio_queue = queue.Queue()  # 再生キュー
        self._playback_thread = None       # 再生スレッド
```

#### なぜ別スレッドで再生するのか

**問題: ブロッキング再生**

```python
# ダメなパターン（以前の実装）
def play(self, audio_bytes: bytes):
    self.stream.write(audio_bytes)  # ← これがブロッキング！
```

`stream.write()`は音声の再生が終わるまで処理を返しません。

```
問題の流れ:
サーバー → 音声データ1 → play() で再生中（32ms待ち）
サーバー → 音声データ2 → play() で再生中（32ms待ち）
サーバー → 割り込みイベント → 待たされる！
　　　　　　　　　　　　　　　→ 音声1,2の再生が終わるまで処理されない
```

**解決: 非同期再生（別スレッド）**

```python
# 良いパターン（現在の実装）
def play(self, audio_bytes: bytes):
    self._audio_queue.put(audio_bytes)  # キューに入れるだけ（即座に戻る）
```

```
改善後の流れ:
メインスレッド:
  サーバー → 音声データ1 → キューに追加（即座）
  サーバー → 音声データ2 → キューに追加（即座）
  サーバー → 割り込みイベント → すぐ処理できる！→ clear()でキューを空に

再生スレッド（別スレッド）:
  キューから取り出し → 再生 → キューから取り出し → 再生...
  （メインスレッドとは独立して動く）
```

**例え話：**
レストランの厨房を想像してください。

- **ブロッキング**: ウェイターが料理を客席に運び終わるまで、次の注文を受けられない
- **非同期**: ウェイターは注文を厨房に渡すだけ（即座に戻る）。料理は厨房スタッフが別途作る

#### start メソッド

```python
def start(self):
    """再生スレッドを開始"""
    self._playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
    self._playback_thread.start()
```

別スレッドで `_playback_loop` を開始します。
`daemon=True` にすることで、メインプログラム終了時に自動で終了します。

#### _playback_loop メソッド

```python
def _playback_loop(self):
    """別スレッドで音声を再生"""
    while self._running:
        try:
            audio_bytes = self._audio_queue.get(timeout=0.1)
            if audio_bytes is None:  # 終了シグナル
                break
            self.stream.write(audio_bytes)
        except queue.Empty:
            continue
```

**処理の流れ:**
1. キューからデータを取得（最大0.1秒待つ）
2. データがあれば再生
3. `None` を受け取ったら終了
4. 1に戻る

#### play メソッド（ノンブロッキング）

```python
def play(self, audio_bytes: bytes):
    """音声データをキューに追加（ノンブロッキング）"""
    self._audio_queue.put(audio_bytes)
```

キューに追加するだけなので、即座に処理が戻ります。
実際の再生は別スレッドが担当します。

#### clear メソッド（割り込み対応）

```python
def clear(self):
    """割り込み時にキューをクリアして再生を即座に停止"""
    while not self._audio_queue.empty():
        try:
            self._audio_queue.get_nowait()
        except queue.Empty:
            break
```

**割り込みとは:**
ユーザーがAIの発話中に話しかけると、AIの発話が中断される機能です。

```
AI: 「今日の天気は晴れで気温は──」
ユーザー: 「ストップ」（割り込み）
AI: （発話中断）「はい、何でしょうか？」
```

**clear()の動作:**
```
キュー: [音声3] [音声4] [音声5] ← まだ再生されていないデータ

割り込みイベント受信！

clear() 実行
　↓
キュー: [] ← 空になる（音声3,4,5は再生されない）
```

#### stop メソッド

```python
def stop(self):
    """再生を停止"""
    self._running = False
    self._audio_queue.put(None)  # 終了シグナル
    if self._playback_thread and self._playback_thread.is_alive():
        self._playback_thread.join(timeout=1.0)  # スレッド終了を待つ
    ...
```

`None` をキューに入れることで、再生スレッドに「終了してね」と伝えます。

### 5.3 AudioRecorder クラス（音声録音）

```python
class AudioRecorder:
    """マイクから音声を録音するクラス"""

    def __init__(self):
        if not PYAUDIO_AVAILABLE:
            return

        self.pa = pyaudio.PyAudio()
        self.audio_queue = queue.Queue()  # 音声データの待ち行列
        self._running = False
        self._stream = None
```

#### Queue（キュー）とは

```
          追加                              取り出し
新データ → [D] [C] [B] [A] → 古いデータから取り出す

First In, First Out（FIFO）: 先に入れたものが先に出る
```

**例え話：**
コンビニのレジの列。先に並んだ人が先に会計する。
録音した音声も、先に録音したものから順に送信する。

#### コールバック関数

```python
def _audio_callback(self, in_data, frame_count, time_info, status):
    """PyAudioのコールバック（別スレッドで実行）"""
    if self._running:
        self.audio_queue.put(in_data)  # 録音データをキューに追加
    return (None, pyaudio.paContinue)  # 録音を続ける
```

**コールバックとは：**

「○○が起きたら、この関数を呼んでね」という仕組み。

```
通常の処理:
プログラム: 「録音して」
マイク:     「...録音中...」
プログラム: 「待ってる...」（ブロックされる）
マイク:     「できた！」
プログラム: 「やっと続きができる」

コールバック:
プログラム: 「録音して。できたらこの関数呼んで」
マイク:     「了解」
プログラム: 「他の仕事しよう」（ブロックされない）
マイク:     「できた！関数呼ぶね」→ _audio_callback実行
```

**引数の意味：**
- `in_data`: 録音された音声データ（バイト列）
- `frame_count`: 録音されたフレーム数
- `time_info`: タイミング情報（通常は使わない）
- `status`: ステータスフラグ（エラー検出用）

**戻り値：**
- `(None, pyaudio.paContinue)`: 出力データなし、録音続行

#### start メソッド

```python
def start(self):
    """録音を開始"""
    if not PYAUDIO_AVAILABLE:
        return

    self._running = True
    self._stream = self.pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=INPUT_SAMPLE_RATE,
        input=True,                        # 入力（録音）モード
        frames_per_buffer=CHUNK_SIZE,
        stream_callback=self._audio_callback  # コールバック関数を登録
    )
    self._stream.start_stream()  # 録音開始
```

`input=True` で録音モード、`stream_callback` でコールバック関数を登録。

#### get_audio_chunk メソッド

```python
def get_audio_chunk(self) -> bytes | None:
    """キューから音声チャンクを取得（ノンブロッキング）"""
    try:
        return self.audio_queue.get_nowait()  # 待たずに取得
    except queue.Empty:
        return None  # キューが空なら None を返す
```

**ブロッキング vs ノンブロッキング：**

```
ブロッキング（get()）:
「データください」→ データがないと待ち続ける

ノンブロッキング（get_nowait()）:
「データください」→ ないなら「ない」とすぐ返事
```

ノンブロッキングにすることで、他の処理（受信など）を妨げない。

### 5.4 WebSocket 接続の確立

```python
def get_websocket_connection(region: str, runtime_arn: str):
    """AgentCore RuntimeへのWebSocket接続情報を取得"""
    client = AgentCoreRuntimeClient(region=region)
    ws_url, headers = client.generate_ws_connection(runtime_arn=runtime_arn)
    return ws_url, headers
```

#### SigV4 認証とは

AWSのサービスにアクセスするには認証が必要です。
`AgentCoreRuntimeClient.generate_ws_connection()` は：

1. AWSの認証情報（アクセスキー等）を使って
2. リクエストに「署名」を付与し
3. 署名付きのWebSocket URLを生成する

```
通常のURL:
wss://example.com/ws

SigV4署名付きURL:
wss://example.com/ws?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=...&X-Amz-Signature=abc123...
```

この署名があることで、AWSは「正規のユーザーからのリクエストだ」と判断できる。

### 5.5 メインの音声セッション

```python
async def audio_session(region: str, runtime_arn: str):
    """マイク入力を使った音声対話セッション"""

    # ... 省略（初期化処理）...

    recorder = AudioRecorder()
    player = AudioPlayer()

    try:
        async with websockets.connect(
            ws_url,
            additional_headers=headers,  # SigV4認証ヘッダー
            open_timeout=60,             # 接続タイムアウト60秒
            close_timeout=10,            # 切断タイムアウト10秒
        ) as websocket:

            recorder.start()  # 録音開始

            # 送信タスクと受信タスクを並行実行
            send_task = asyncio.create_task(send_audio(websocket, recorder))
            receive_task = asyncio.create_task(receive_messages(websocket, player))

            # どちらかが終了するまで待機
            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED
            )
```

#### async with の意味

```python
async with websockets.connect(...) as websocket:
    # この中でwebsocketを使う
# ここに来ると自動的に接続が閉じられる
```

`with` 文は「後片付けを自動でやってくれる」仕組み。
接続を開いて、ブロックを抜けると自動で閉じる。

#### asyncio.create_task

```python
send_task = asyncio.create_task(send_audio(...))
receive_task = asyncio.create_task(receive_messages(...))
```

2つの処理を「同時に」開始する。

```
create_task前:
send_audio → 終わるまで待つ → receive_messages

create_task後:
send_audio    ──────────────────────→
receive_messages ──────────────────→
（両方同時に動く）
```

#### asyncio.wait

```python
done, pending = await asyncio.wait(
    [send_task, receive_task],
    return_when=asyncio.FIRST_COMPLETED
)
```

「どちらかが終わるまで待つ」という意味。

- `done`: 終了したタスクの集合
- `pending`: まだ実行中のタスクの集合

例えば、ユーザーがCtrl+Cを押すと receive_task がエラーで終了し、
その時点で wait から抜けて、残りの send_task をキャンセルする。

### 5.6 音声送信処理

```python
async def send_audio(websocket, recorder: AudioRecorder):
    """マイクからの音声をWebSocketに送信"""
    try:
        while True:
            # キューから音声チャンクを取得
            audio_chunk = recorder.get_audio_chunk()

            if audio_chunk:
                # BidiAudioInputEvent形式で送信
                message = {
                    "type": "bidi_audio_input",
                    "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                    "format": "pcm",
                    "sample_rate": INPUT_SAMPLE_RATE,
                    "channels": CHANNELS
                }
                await websocket.send(json.dumps(message))

            # 少し待機（CPU負荷軽減）
            await asyncio.sleep(0.01)
```

#### 処理の流れ

```
1. recorder.get_audio_chunk()
   キューから録音データを取得

2. base64.b64encode(audio_chunk)
   バイナリ → テキスト変換

3. .decode("utf-8")
   バイト文字列 → 文字列に変換

4. json.dumps(message)
   辞書 → JSON文字列に変換

5. websocket.send(...)
   サーバーに送信
```

#### await asyncio.sleep(0.01)

10ミリ秒待機。これがないと：
- CPUが100%になる（無限ループを全速力で回すため）
- 他のタスク（受信）に処理が回らない

「ちょっと休憩して、他の人にも順番を譲る」というイメージ。

### 5.7 音声受信処理

```python
async def receive_messages(websocket, player: AudioPlayer):
    """WebSocketからメッセージを受信して処理"""
    try:
        async for message in websocket:  # メッセージが来るたびにループ
            data = json.loads(message)   # JSON → 辞書
            msg_type = data.get("type", "")

            # 音声ストリーム
            if msg_type == "bidi_audio_stream":
                audio_data = data.get("audio", "")
                if audio_data:
                    audio_bytes = base64.b64decode(audio_data)  # テキスト → バイナリ
                    player.play(audio_bytes)  # 再生

            # トランスクリプト（文字起こし）
            elif msg_type == "bidi_transcript_stream":
                role = data.get("role", "")
                text = data.get("text", "")
                is_final = data.get("is_final", False)
                if is_final:
                    prefix = "Agent" if role == "assistant" else "You"
                    print(f"[{prefix}] {text}")

            # レスポンス完了（割り込み検知）
            elif msg_type == "bidi_response_complete":
                reason = data.get("stop_reason", "")
                if reason == "interrupted":
                    print("[Agent] (interrupted)")
                    player.clear()  # 未再生の音声をクリア
```

#### async for message in websocket

WebSocketからメッセージが届くたびに、ループが1回実行される。

```
サーバー → メッセージ1 → ループ1回目
サーバー → メッセージ2 → ループ2回目
サーバー → メッセージ3 → ループ3回目
...
```

メッセージがない間は「待機」状態になり、CPUを消費しない。

#### 割り込み（Interruption）の処理

ユーザーがAIの発話中に話しかけると、サーバーから `bidi_response_complete` イベントが
`stop_reason: "interrupted"` 付きで送られてきます。

```
タイムライン:

AI発話中:
  サーバー → 音声データ1 → キューに追加
  サーバー → 音声データ2 → キューに追加
  サーバー → 音声データ3 → キューに追加
　　　　　　　　　　　　　　↓
ユーザー割り込み:
  サーバー → bidi_response_complete (interrupted) → player.clear() 実行
　　　　　　　　　　　　　　↓
  キュー: [音声2] [音声3] → [] （クリアされる）
　　　　　　　　　　　　　　↓
新しい応答:
  サーバー → 新しい音声データ → キューに追加 → 再生
```

**player.clear() が重要な理由:**

これがないと、ユーザーが割り込んでも、キューに溜まった古い音声が
全部再生されてしまい、自然な会話になりません。

---

## 6. 非同期処理の仕組み

### なぜ非同期処理が必要か

音声通話では、以下が「同時に」起きる必要がある：

1. マイクから録音（連続的に）
2. 録音データをサーバーに送信（連続的に）
3. サーバーからの返答を受信（いつ来るかわからない）
4. 受信した音声を再生（連続的に）

これを普通の「順番に処理」で書くと：

```python
# ダメな例
while True:
    audio = record()      # 録音（100ms）
    send(audio)           # 送信（50ms）
    response = receive()  # 受信（待ち時間不定）
    play(response)        # 再生（100ms）
```

問題点：
- `receive()` でサーバーの返答を待っている間、録音ができない
- 会話が途切れ途切れになる

### async/await の基本

```python
async def my_function():     # 非同期関数の定義
    result = await some_io() # I/O処理を「待つ」（でも他の処理は進む）
    return result
```

`await` は「この処理が終わるまで待つけど、その間に他のタスクを実行していいよ」という意味。

```
従来のwait:
タスクA: 処理中... 待機... 待機... 待機... 再開
タスクB: 　　　　　　　　　　　　　　　　　　　開始（Aが終わるまで始められない）

asyncのawait:
タスクA: 処理中... await（待機）
タスクB: 　　　　　　　　開始... 処理中... await
タスクA: 　　　　　　　　　　　　　　　　　再開... 完了
タスクB: 　　　　　　　　　　　　　　　　　　　　　再開... 完了
```

### このクライアントでの非同期処理

```python
# 2つのタスクを作成
send_task = asyncio.create_task(send_audio(...))      # 送信タスク
receive_task = asyncio.create_task(receive_messages(...))  # 受信タスク

# 両方を「同時に」実行開始
await asyncio.wait([send_task, receive_task], ...)
```

```
send_audio:
  while True:
    chunk = get_chunk()        # すぐ終わる
    await websocket.send(...)  # 送信中は他のタスクに譲る
    await asyncio.sleep(0.01)  # 少し待つ（他のタスクに譲る）

receive_messages:
  async for message in websocket:  # メッセージ待ち（他のタスクに譲る）
    player.play(...)               # 再生（すぐ終わる）
```

`await` の瞬間に「他のタスクに処理を譲る」ので、
send と receive が交互に（実質同時に）動く。

---

## 7. イベント形式の詳細

### クライアント → サーバー（入力イベント）

#### BidiAudioInputEvent（音声入力）

```json
{
  "type": "bidi_audio_input",
  "audio": "SGVsbG8gV29ybGQ=",
  "format": "pcm",
  "sample_rate": 16000,
  "channels": 1
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| type | string | イベントタイプ（固定値） |
| audio | string | Base64エンコードされた音声データ |
| format | string | 音声形式（"pcm"） |
| sample_rate | number | サンプルレート（16000） |
| channels | number | チャンネル数（1） |

#### BidiTextInputEvent（テキスト入力）

```json
{
  "type": "bidi_text_input",
  "text": "こんにちは",
  "role": "user"
}
```

### サーバー → クライアント（出力イベント）

#### BidiAudioStreamEvent（音声出力）

```json
{
  "type": "bidi_audio_stream",
  "audio": "SGVsbG8gV29ybGQ=",
  "format": "pcm",
  "sample_rate": 16000,
  "channels": 1
}
```

#### BidiTranscriptStreamEvent（文字起こし）

```json
{
  "type": "bidi_transcript_stream",
  "role": "user",
  "text": "こんにちは",
  "is_final": true
}
```

| フィールド | 型 | 説明 |
|-----------|-----|------|
| role | string | 話者（"user" または "assistant"） |
| text | string | 認識/生成されたテキスト |
| is_final | boolean | 確定したかどうか |

`is_final: false` の間は途中経過（まだ変わる可能性がある）。
`is_final: true` になったら確定。

#### その他のイベント

| イベント | 意味 |
|---------|------|
| bidi_connection_start | 接続が確立された |
| bidi_response_start | AIが返答を開始した |
| bidi_response_complete | AIの返答が完了した |
| bidi_error | エラーが発生した |
| bidi_usage | トークン使用量（課金情報） |
| tool_use_stream | AIがツールを使用した |

---

## 8. トラブルシューティング

### 音声が聞こえない

**原因1: サンプルレートの不一致**

```
サーバー: 16kHz で送信
クライアント: 24kHz で再生
→ 音が高く、早口になる

サーバー: 24kHz で送信
クライアント: 16kHz で再生
→ 音が低く、スローになる
```

**解決策**: `[Audio Format]` のログを確認し、OUTPUT_SAMPLE_RATE を合わせる

**原因2: スピーカーがミュート**

PCのスピーカー設定を確認

### 音声が途切れる

**原因**: バッファサイズが小さい

**解決策**: `frames_per_buffer` を大きくする

```python
self.stream = self.pa.open(
    ...
    frames_per_buffer=CHUNK_SIZE * 4,  # 大きくする
)
```

### 接続がタイムアウトする

**原因1**: AgentCore Runtime がまだ起動していない

**解決策**: デプロイ状態を確認
```bash
aws bedrock-agentcore get-runtime --runtime-arn "$AGENT_ARN" --query 'status'
```

**原因2**: ネットワークの問題（VPN、ファイアウォール等）

### 認証エラー

**原因**: AWS認証情報が設定されていない

**解決策**:
```bash
aws configure
# または
export AWS_PROFILE=your-profile
```

---

## 付録: 用語集

| 用語 | 読み方 | 意味 |
|-----|-------|------|
| WebSocket | ウェブソケット | 双方向リアルタイム通信プロトコル |
| PCM | ピーシーエム | 非圧縮の音声データ形式 |
| Sample Rate | サンプルレート | 1秒間のサンプリング回数 |
| Interruption | インタラプション | AI発話中にユーザーが話しかけて中断すること |
| Threading | スレッディング | 複数の処理を並列実行する仕組み |
| Daemon Thread | デーモンスレッド | メインプログラム終了時に自動終了するスレッド |
| Mono | モノラル | 1チャンネルの音声 |
| Stereo | ステレオ | 2チャンネル（左右）の音声 |
| Base64 | ベースろくじゅうよん | バイナリをテキストに変換する方式 |
| Callback | コールバック | 後から呼び出される関数 |
| Queue | キュー | 先入れ先出しのデータ構造 |
| Async | アシンク | 非同期処理 |
| Await | アウェイト | 非同期処理の完了を待つ |
| SigV4 | シグブイフォー | AWSの署名認証方式 |
| ARN | エーアールエヌ | AWSリソースの一意な識別子 |
| STT | エスティーティー | Speech-to-Text（音声認識） |
| TTS | ティーティーエス | Text-to-Speech（音声合成） |
| Bidi | バイダイ | Bidirectional（双方向）の略 |
| Nova Sonic | ノバソニック | AWSの音声AI基盤モデル |

---

## まとめ

このクライアントは、以下の要素で構成されています：

1. **音声入力**: PyAudio + AudioRecorder でマイクから録音
2. **データ変換**: Base64 + JSON で音声データをテキスト化
3. **通信**: WebSocket でリアルタイム双方向通信
4. **認証**: SigV4 で AWS サービスに安全にアクセス
5. **非同期処理**: asyncio で送受信を同時実行
6. **音声出力**: AudioPlayer でスピーカーから再生

これらが連携することで、AIエージェントとの自然な音声会話が実現できます。
