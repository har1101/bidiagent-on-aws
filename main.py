import asyncio

from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.io import BidiAudioIO, BidiTextIO
from strands.experimental.bidi.models import BidiNovaSonicModel
from strands.experimental.bidi.tools import stop_conversation

from strands_tools import http_request, calculator

# 双方向ストリーミングでは @app.websocket を用いる
from bedrock_agentcore import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.websocket
async def main() -> None:
    # モデルはNova 2 Sonicを使う
    model = BidiNovaSonicModel(
        model_id='amazon.nova-2-sonic-v1:0',
        provider_config={"audio": {"voice": "tiffany",}},
    )

    # stop_conversation toolは、ユーザーがエージェントの実行を口頭で停止できるようにします。
    agent = BidiAgent(
        model=model,
        tools=[calculator, http_request, stop_conversation],
        system_prompt="You are a helpful assistant that can use the http_request tool to search and get some information. Speak Japanese.",
    )

    audio_io = BidiAudioIO()
    text_io = BidiTextIO()

    try: 
        # 明示的にやめるまで無限にセッションは続く
        await agent.run(
            inputs=[audio_io.input()],
            outputs=[audio_io.output(), text_io.output()] # 音声と文字両方でアウトプット
        )
    except asyncio.CancelledError:
        print("\nConversation cancelled by user")
    finally:
        # stop()メソッドは、必ずrun()メソッドのループを抜け出した後で実施する
        await agent.stop()

if __name__ == "__main__":
    asyncio.run(main())
