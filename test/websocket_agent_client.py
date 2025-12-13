import asyncio
import websockets
import json
import base64
import sys
import threading
import queue

# =============================================================================
# ローカルAgentCore RuntimeへのWebSocket接続テストクライアント
# マイクから音声を取得してWebSocket経由で送信
# =============================================================================

# PyAudioのインポート（音声入出力用）
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    print("[Warning] PyAudio not installed. Audio features disabled.")
    print("Install with: pip install pyaudio")


# オーディオ設定（Nova Sonicの要件に合わせる）
SAMPLE_RATE = 16000  # 16kHz
CHANNELS = 1         # モノラル
CHUNK_SIZE = 512     # フレームサイズ
FORMAT = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None  # 16bit PCM


class AudioPlayer:
    """受信した音声データを再生するクラス"""

    def __init__(self):
        if not PYAUDIO_AVAILABLE:
            return

        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            output=True,
            frames_per_buffer=CHUNK_SIZE
        )
        self._running = True

    def play(self, audio_bytes: bytes):
        """音声データを再生"""
        if not PYAUDIO_AVAILABLE or not self._running:
            return
        try:
            self.stream.write(audio_bytes)
        except Exception as e:
            print(f"[AudioPlayer] Error: {e}")

    def stop(self):
        """再生を停止"""
        self._running = False
        if PYAUDIO_AVAILABLE:
            try:
                self.stream.stop_stream()
                self.stream.close()
                self.pa.terminate()
            except Exception:
                pass


class AudioRecorder:
    """マイクから音声を録音するクラス"""

    def __init__(self):
        if not PYAUDIO_AVAILABLE:
            return

        self.pa = pyaudio.PyAudio()
        self.audio_queue = queue.Queue()
        self._running = False
        self._stream = None

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """PyAudioのコールバック（別スレッドで実行）"""
        if self._running:
            self.audio_queue.put(in_data)
        return (None, pyaudio.paContinue)

    def start(self):
        """録音を開始"""
        if not PYAUDIO_AVAILABLE:
            return

        self._running = True
        self._stream = self.pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
            stream_callback=self._audio_callback
        )
        self._stream.start_stream()
        print("[AudioRecorder] Started recording")

    def stop(self):
        """録音を停止"""
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                pass
        if PYAUDIO_AVAILABLE:
            try:
                self.pa.terminate()
            except Exception:
                pass
        print("[AudioRecorder] Stopped recording")

    def get_audio_chunk(self) -> bytes | None:
        """キューから音声チャンクを取得（ノンブロッキング）"""
        try:
            return self.audio_queue.get_nowait()
        except queue.Empty:
            return None


async def audio_session():
    """マイク入力を使った音声対話セッション"""
    if not PYAUDIO_AVAILABLE:
        print("[Error] PyAudio is required for audio session.")
        print("Install with: pip install pyaudio")
        return

    uri = "ws://localhost:8080/ws"

    print("=" * 60)
    print("WebSocket Agent Client - Audio Mode")
    print("=" * 60)
    print(f"Connecting to: {uri}")
    print("Speak into your microphone to interact with the agent.")
    print("Press Ctrl+C to disconnect.")
    print("=" * 60)

    recorder = AudioRecorder()
    player = AudioPlayer()

    try:
        async with websockets.connect(uri) as websocket:
            print("[Connected] WebSocket connection established\n")

            # 録音を開始
            recorder.start()

            # 送信タスクと受信タスクを並行実行
            send_task = asyncio.create_task(send_audio(websocket, recorder))
            receive_task = asyncio.create_task(receive_messages(websocket, player))

            # どちらかが終了するまで待機
            done, pending = await asyncio.wait(
                [send_task, receive_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            # 残りのタスクをキャンセル
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    except websockets.exceptions.ConnectionClosed as e:
        print(f"\n[Connection closed] {e}")
    except ConnectionRefusedError:
        print(f"\n[Error] Connection refused. Is the server running at {uri}?")
    except KeyboardInterrupt:
        print("\n[Interrupted] Disconnecting...")
    except Exception as e:
        print(f"\n[Error] {type(e).__name__}: {e}")
    finally:
        recorder.stop()
        player.stop()
        print("[Disconnected]")


async def send_audio(websocket, recorder: AudioRecorder):
    """マイクからの音声をWebSocketに送信"""
    try:
        while True:
            # 音声チャンクを取得
            audio_chunk = recorder.get_audio_chunk()

            if audio_chunk:
                # BidiAudioInputEvent形式で送信
                # Strandsドキュメントに従い、format, sample_rate, channelsを含める
                message = {
                    "type": "bidi_audio_input",
                    "audio": base64.b64encode(audio_chunk).decode("utf-8"),
                    "format": "pcm",
                    "sample_rate": SAMPLE_RATE,
                    "channels": CHANNELS
                }
                await websocket.send(json.dumps(message))

            # 少し待機（CPU負荷軽減）
            await asyncio.sleep(0.01)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[Send Error] {e}")
        raise


async def receive_messages(websocket, player: AudioPlayer):
    """WebSocketからメッセージを受信して処理

    Strandsの出力イベント形式に対応:
    - bidi_audio_stream: 音声出力
    - bidi_transcript_stream: トランスクリプト
    - bidi_connection_start: 接続開始
    - bidi_response_start/complete: レスポンス開始/終了
    - bidi_error: エラー
    """
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")

                # 音声ストリーム (Strands BidiAudioStreamEvent)
                if msg_type == "bidi_audio_stream":
                    audio_data = data.get("audio", "")
                    # 音声形式情報を表示（初回のみ）
                    if not hasattr(receive_messages, '_audio_format_shown'):
                        receive_messages._audio_format_shown = True
                        print(f"[Audio Format] format={data.get('format')}, sample_rate={data.get('sample_rate')}, channels={data.get('channels')}")
                    if audio_data:
                        audio_bytes = base64.b64decode(audio_data)
                        # print(f"[Audio] Received {len(audio_bytes)} bytes")  # デバッグ
                        player.play(audio_bytes)

                # 旧形式互換
                elif msg_type == "audio":
                    audio_bytes = base64.b64decode(data.get("data", ""))
                    print(f"[Audio] Received (old format) {len(audio_bytes)} bytes")  # デバッグ
                    player.play(audio_bytes)

                # トランスクリプト (Strands BidiTranscriptStreamEvent)
                elif msg_type == "bidi_transcript_stream":
                    role = data.get("role", "")
                    text = data.get("text", "")
                    is_final = data.get("is_final", False)

                    if is_final:
                        prefix = "Agent" if role == "assistant" else "You"
                        print(f"[{prefix}] {text}")

                # 旧形式互換
                elif msg_type == "transcript":
                    role = data.get("role", "")
                    text = data.get("text", "")
                    is_final = data.get("is_final", False)

                    if is_final:
                        prefix = "Agent" if role == "assistant" else "You"
                        print(f"[{prefix}] {text}")

                # 接続開始 (Strands BidiConnectionStartEvent)
                elif msg_type == "bidi_connection_start":
                    model = data.get("model", "unknown")
                    print(f"[Connected] Model: {model}")

                # レスポンス開始/終了
                elif msg_type == "bidi_response_start":
                    print("[Agent] Responding...")

                elif msg_type == "bidi_response_complete":
                    reason = data.get("stop_reason", "")
                    if reason == "interrupted":
                        print("[Agent] (interrupted)")

                # エラー (Strands BidiErrorEvent)
                elif msg_type == "bidi_error":
                    print(f"[Error] {data.get('message', 'Unknown error')}")

                # 旧形式互換
                elif msg_type == "error":
                    print(f"[Error] {data.get('message', 'Unknown error')}")

                # ツール使用
                elif msg_type == "tool_use_stream":
                    tool = data.get("current_tool_use", {})
                    print(f"[Tool] Using: {tool.get('name', 'unknown')}")

                # その他のイベント（デバッグ用）
                else:
                    print(f"[Event] {msg_type}")

            except json.JSONDecodeError:
                print(f"[Receive] Invalid JSON: {message[:100]}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[Receive Error] {e}")
        raise


async def text_session():
    """テキスト入力セッション（音声なし、デバッグ用）"""
    uri = "ws://localhost:8080/ws"

    print("=" * 60)
    print("WebSocket Agent Client - Text Mode (Debug)")
    print("=" * 60)
    print(f"Connecting to: {uri}")
    print("Type your message and press Enter to send.")
    print("Type 'quit' or 'exit' to disconnect.")
    print("=" * 60)

    try:
        async with websockets.connect(uri) as websocket:
            print("[Connected] WebSocket connection established\n")

            # 受信タスクを開始
            receive_task = asyncio.create_task(receive_text_messages(websocket))

            while True:
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("You: ")
                    )
                except EOFError:
                    break

                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("\n[Disconnecting...]")
                    break

                if not user_input.strip():
                    continue

                # テキストメッセージを送信（BidiInputEvent形式）
                message = {"type": "bidi_text_input", "text": user_input}
                await websocket.send(json.dumps(message, ensure_ascii=False))

            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass

    except websockets.exceptions.ConnectionClosed as e:
        print(f"\n[Connection closed] {e}")
    except ConnectionRefusedError:
        print(f"\n[Error] Connection refused. Is the server running at {uri}?")
    except Exception as e:
        print(f"\n[Error] {type(e).__name__}: {e}")


async def receive_text_messages(websocket):
    """WebSocketからテキストメッセージを受信"""
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                print(f"<<< {json.dumps(data, ensure_ascii=False, indent=2)}")
            except json.JSONDecodeError:
                print(f"<<< {message}")
    except asyncio.CancelledError:
        pass


# =============================================================================
# 旧実装（コメントアウト）
# =============================================================================

# async def local_websocket():
#     uri = "ws://localhost:8080/ws"
#
#     try:
#         async with websockets.connect(uri) as websocket:
#             await websocket.send(json.dumps({"inputText": "Hello WebSocket!"}))
#             response = await websocket.recv()
#             print(f"Received: {response}")
#     except Exception as e:
#         print(f"Connection failed: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--text":
        # テキストモード（デバッグ用）
        asyncio.run(text_session())
    else:
        # 音声モード（デフォルト）
        asyncio.run(audio_session())
