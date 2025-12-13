"""
AgentCore Runtime WebSocket Client

AgentCore Runtimeにデプロイされた音声エージェントに接続するクライアント。
SigV4認証を使用してWebSocket接続を確立し、音声でやり取りする。

使用方法:
    # 環境変数でARNを設定
    export AGENT_ARN="arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/your-agent-name"

    # 音声モード（デフォルト）
    python test/agentcore_client.py

    # テキストモード（デバッグ用）
    python test/agentcore_client.py --text

    # リージョン指定
    python test/agentcore_client.py --region us-west-2
"""
import asyncio
import websockets
import json
import base64
import sys
import os
import queue

# AgentCore Runtime SDK
from bedrock_agentcore.runtime import AgentCoreRuntimeClient

# PyAudioのインポート（音声入出力用）
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    print("[Warning] PyAudio not installed. Audio features disabled.")
    print("Install with: pip install pyaudio")


# オーディオ設定
# Nova Sonicは入出力ともに16kHz
SAMPLE_RATE = 16000  # 16kHz
INPUT_SAMPLE_RATE = SAMPLE_RATE
OUTPUT_SAMPLE_RATE = SAMPLE_RATE
CHANNELS = 1         # モノラル
CHUNK_SIZE = 512     # フレームサイズ
FORMAT = pyaudio.paInt16 if PYAUDIO_AVAILABLE else None  # 16bit PCM


class AudioPlayer:
    """受信した音声データを再生するクラス"""

    def __init__(self, sample_rate: int = OUTPUT_SAMPLE_RATE):
        if not PYAUDIO_AVAILABLE:
            return

        self.pa = pyaudio.PyAudio()
        self._sample_rate = sample_rate
        self.stream = self.pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=sample_rate,
            output=True,
            frames_per_buffer=CHUNK_SIZE * 2  # 出力は大きめのバッファ
        )
        self._running = True
        print(f"[AudioPlayer] Initialized with sample_rate={sample_rate}")

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
            rate=INPUT_SAMPLE_RATE,
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


def get_websocket_connection(region: str, runtime_arn: str):
    """
    AgentCore RuntimeへのWebSocket接続情報を取得

    Args:
        region: AWSリージョン
        runtime_arn: AgentCore RuntimeのARN

    Returns:
        (ws_url, headers): WebSocket URLと認証ヘッダー
    """
    client = AgentCoreRuntimeClient(region=region)
    ws_url, headers = client.generate_ws_connection(runtime_arn=runtime_arn)
    return ws_url, headers


async def audio_session(region: str, runtime_arn: str):
    """マイク入力を使った音声対話セッション"""
    if not PYAUDIO_AVAILABLE:
        print("[Error] PyAudio is required for audio session.")
        print("Install with: pip install pyaudio")
        return

    print("=" * 60)
    print("AgentCore Runtime Client - Audio Mode")
    print("=" * 60)
    #print(f"Runtime ARN: {runtime_arn}")
    print(f"Region: {region}")
    print("Speak into your microphone to interact with the agent.")
    print("Press Ctrl+C to disconnect.")
    print("=" * 60)

    # WebSocket接続情報を取得
    print("\n[Connecting] Generating WebSocket connection...")
    try:
        ws_url, headers = get_websocket_connection(region, runtime_arn)
        print(f"[Connecting] WebSocket URL obtained")
        # print(f"[Debug] URL: {ws_url[:80]}...")
    except Exception as e:
        print(f"[Error] Failed to generate WebSocket connection: {e}")
        return

    recorder = AudioRecorder()
    player = AudioPlayer()

    try:
        print("[Connecting] Establishing WebSocket connection (timeout: 60s)...")
        async with websockets.connect(
            ws_url,
            additional_headers=headers,
            open_timeout=60,
            close_timeout=10,
        ) as websocket:
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
        print(f"\n[Error] Connection refused.")
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
                        player.play(audio_bytes)

                # トランスクリプト (Strands BidiTranscriptStreamEvent)
                elif msg_type == "bidi_transcript_stream":
                    role = data.get("role", "")
                    text = data.get("text", "")
                    is_final = data.get("is_final", False)

                    if is_final:
                        prefix = "Agent" if role == "assistant" else "You"
                        print(f"[{prefix}] {text}")

                # 接続開始 (Strands BidiConnectionStartEvent)
                elif msg_type == "bidi_connection_start":
                    model = data.get("model", "unknown")
                    print(f"[Model Connected] {model}")

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

                # ツール使用
                elif msg_type == "tool_use_stream":
                    tool = data.get("current_tool_use", {})
                    print(f"[Tool] Using: {tool.get('name', 'unknown')}")

                # 使用量（無視）
                elif msg_type == "bidi_usage":
                    pass

                # その他のイベント
                else:
                    print(f"[Event] {msg_type}")

            except json.JSONDecodeError:
                print(f"[Receive] Invalid JSON: {message[:100]}")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[Receive Error] {e}")
        raise


async def text_session(region: str, runtime_arn: str):
    """テキスト入力セッション（音声なし、デバッグ用）"""
    print("=" * 60)
    print("AgentCore Runtime Client - Text Mode (Debug)")
    print("=" * 60)
    print(f"Runtime ARN: {runtime_arn}")
    print(f"Region: {region}")
    print("Type your message and press Enter to send.")
    print("Type 'quit' or 'exit' to disconnect.")
    print("=" * 60)

    # WebSocket接続情報を取得
    print("\n[Connecting] Generating WebSocket connection...")
    try:
        ws_url, headers = get_websocket_connection(region, runtime_arn)
        print(f"[Connecting] WebSocket URL obtained")
    except Exception as e:
        print(f"[Error] Failed to generate WebSocket connection: {e}")
        return

    try:
        async with websockets.connect(ws_url, additional_headers=headers) as websocket:
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
                message = {
                    "type": "bidi_text_input",
                    "text": user_input,
                    "role": "user"
                }
                await websocket.send(json.dumps(message, ensure_ascii=False))

            receive_task.cancel()
            try:
                await receive_task
            except asyncio.CancelledError:
                pass

    except websockets.exceptions.ConnectionClosed as e:
        print(f"\n[Connection closed] {e}")
    except ConnectionRefusedError:
        print(f"\n[Error] Connection refused.")
    except Exception as e:
        print(f"\n[Error] {type(e).__name__}: {e}")


async def receive_text_messages(websocket):
    """WebSocketからテキストメッセージを受信（デバッグ用）"""
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")

                # 使用量イベントは省略
                if msg_type == "bidi_usage":
                    continue

                print(f"<<< {json.dumps(data, ensure_ascii=False, indent=2)}")
            except json.JSONDecodeError:
                print(f"<<< {message}")
    except asyncio.CancelledError:
        pass


def main():
    import argparse

    parser = argparse.ArgumentParser(description="AgentCore Runtime WebSocket Client")
    parser.add_argument("--text", action="store_true", help="Use text mode instead of audio")
    parser.add_argument("--region", default="ap-northeast-1", help="AWS region (default: ap-northeast-1)")
    parser.add_argument("--arn", help="Agent Runtime ARN (or set AGENT_ARN env var)")
    args = parser.parse_args()

    # Runtime ARNを取得
    runtime_arn = args.arn or os.environ.get("AGENT_ARN")
    if not runtime_arn:
        print("[Error] Agent Runtime ARN is required.")
        print("Set AGENT_ARN environment variable or use --arn option.")
        print("\nExample:")
        print('  export AGENT_ARN="arn:aws:bedrock-agentcore:ap-northeast-1:123456789012:runtime/your-agent"')
        print("  python test/agentcore_client.py")
        sys.exit(1)

    region = args.region

    if args.text:
        asyncio.run(text_session(region, runtime_arn))
    else:
        asyncio.run(audio_session(region, runtime_arn))


if __name__ == "__main__":
    main()
