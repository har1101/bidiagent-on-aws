"""
BidiAgent on AWS - WebSocket Voice Agent Server

BedrockAgentCoreApp + BidiAgent + Nova Sonic を使用した
WebSocket経由の双方向音声ストリーミングエージェント
"""
import os
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.websockets import WebSocket, WebSocketDisconnect

from strands.experimental.bidi.agent import BidiAgent
from strands.experimental.bidi.models.nova_sonic import BidiNovaSonicModel
from strands.experimental.bidi.tools import stop_conversation

from strands_tools import http_request, calculator

model_id = "amazon.nova-2-sonic-v1:0"

# BedrockAgentCoreApp を使用
app = BedrockAgentCoreApp()

@app.websocket
async def websocket_handler(websocket: WebSocket, context):
    """
    AgentCore Runtime から /ws に来た WebSocket 接続を受け、
    Strands の BidiAgent(Nova Sonic)にブリッジする。

    クライアントは BidiAudioInputEvent / BidiTextInputEvent 形式の
    JSONイベントを送信し、BidiAudioStreamEvent / BidiTranscriptStreamEvent
    等のイベントをJSONで受信する。

    Args:
        websocket: Starlette WebSocketオブジェクト
        context: RequestContext (session_id, request_headers等を含む)
    """
    await websocket.accept()
    print("[Server] WebSocket connected")
    print(f"[Server] Context: {context}")

    # Nova Sonic モデルの設定
    # Note: Nova Sonicはus-east-1, us-west-2, ap-northeast-1等で利用可能
    print("[Server] Creating model...")
    model = BidiNovaSonicModel(
        model_id=model_id,
        provider_config={
            "audio": {
                "voice": "tiffany",  # 利用可能: "tiffany", "matthew", "ruth"
            }
        },
    )
    print("[Server] Model created")

    # BidiAgent の設定
    # stop_conversation toolはユーザーが口頭でエージェントを停止できるようにする
    print("[Server] Creating agent...")
    agent = BidiAgent(
        model=model,
        tools=[calculator, http_request, stop_conversation],
        system_prompt="You are a helpful assistant. Speak Japanese.",
    )
    print("[Server] Agent created")

    try:
        print("[Server] Starting agent.run()...")
        # WebSocketのreceive_json/send_jsonを直接I/Oとして使用
        await agent.run(
            inputs=[websocket.receive_json],
            outputs=[websocket.send_json],
        )
        print("[Server] agent.run() completed")

    except WebSocketDisconnect:
        print("[Server] Client disconnected")
    except Exception as e:
        print(f"[Server] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[Server] Cleanup...")
        try:
            await agent.stop()
        except Exception as e:
            print(f"[Server] Stop error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
        print("[Server] Done")


if __name__ == "__main__":
    print("Starting WebSocket server with BedrockAgentCoreApp on port 8080...")
    app.run()
