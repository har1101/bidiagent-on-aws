#!/usr/bin/env python3
"""
AgentCore Runtimeを呼び出すスクリプト

使用方法:
    python scripts/invoke_agent.py "ラスベガスの現在時刻を教えて"

環境変数:
    AGENT_RUNTIME_ARN: AgentCore RuntimeのARN
"""
import boto3
import json
import sys
import os
from dotenv import load_dotenv


def invoke_agent(
    agent_runtime_arn: str,
    prompt: str,
    user_id: str = "test-user",
    qualifier: str = "DEFAULT",
    region: str = "us-east-1",
) -> str:
    """
    AgentCore Runtimeを呼び出す

    Args:
        agent_runtime_arn: RuntimeのARN
        prompt: ユーザーからのプロンプト
        user_id: ユーザー識別子（必須）
        qualifier: エンドポイント名（デフォルト: "DEFAULT"）
        region: AWSリージョン

    Returns:
        エージェントからの応答テキスト
    """
    client = boto3.client("bedrock-agentcore", region_name=region)

    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        qualifier=qualifier,
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        contentType="application/json",
        runtimeUserId=user_id,
    )

    # StreamingBodyからレスポンスを読み取り
    content = []
    for chunk in response.get("response", []):
        content.append(chunk.decode("utf-8"))

    # JSONとしてパースして結果を返す
    result = "".join(content)
    try:
        parsed = json.loads(result)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return result


def main():
    load_dotenv()

    agent_arn = os.environ.get("AGENT_RUNTIME_ARN")
    if not agent_arn:
        print("エラー: 環境変数 AGENT_RUNTIME_ARN が設定されていません")
        print("使用方法: AGENT_RUNTIME_ARN=arn:aws:... python scripts/invoke_agent.py 'プロンプト'")
        sys.exit(1)

    # コマンドライン引数からプロンプトを取得、なければデフォルト
    if len(sys.argv) > 1:
        prompt = " ".join(sys.argv[1:])
    else:
        prompt = "ラスベガスの現在時刻を教えて"

    print(f"プロンプト: {prompt}")
    print("エージェントを呼び出し中...")
    print("-" * 50)

    try:
        result = invoke_agent(agent_arn, prompt)
        print(result)
    except Exception as e:
        print(f"エラー: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
