"""
WebSocketサーバー(BedrockAgentCoreApp版)
agent.run(inputs=[websocket.receive_json], outputs=[websocket.send_json])パターンを使用
"""
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from starlette.websockets import WebSocket, WebSocketDisconnect

from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel
from strands.experimental.bidi.tools import stop_conversation

from strands_tools import http_request, calculator

# BedrockAgentCoreApp を使用
app = BedrockAgentCoreApp()


@app.websocket
async def websocket_handler(websocket: WebSocket, context):
    """
    AgentCore Runtime から /ws に来た WebSocket 接続を受け、
    Strands の BidiAgent(Nova Sonic)にブリッジする。
    """
    await websocket.accept()
    print("[Server] WebSocket connected")
    print(f"[Server] Context: {context}")

    print("[Server] Creating model...")
    model = BidiNovaSonicModel(
        model_id='amazon.nova-2-sonic-v1:0',
        provider_config={"audio": {"voice": "tiffany"}},
    )
    print("[Server] Model created")

    print("[Server] Creating agent...")
    agent = BidiAgent(
        model=model,
        tools=[calculator, http_request, stop_conversation],
        system_prompt="You are a helpful assistant. Speak Japanese.",
    )
    print("[Server] Agent created")

    try:
        print("[Server] Starting agent.run()...")
        # Strandsドキュメントの推奨パターン
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
