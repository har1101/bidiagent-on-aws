import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Platform } from 'aws-cdk-lib/aws-ecr-assets';
import { ContainerImageBuild } from 'deploy-time-build';
import * as agentcore from '@aws-cdk/aws-bedrock-agentcore-alpha';

export class BidiStrandsAgentcoreStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // 「deploy-time-build」というL3 Constructを使ってCodeBuildプロジェクト構築~buildキックまで自動的に実施
		const agentcoreRuntimeImage = new ContainerImageBuild(this, 'BidiAgentImage', {
			directory: './bidiagent',
			platform: Platform.LINUX_ARM64,
		});

    // AgentCore Runtime(L2 Construct)
		const runtime = new agentcore.Runtime(this, 'BidiAgentCoreRuntime', {
			runtimeName: 'bidi_strands_agent',
			agentRuntimeArtifact: agentcore.AgentRuntimeArtifact.fromEcrRepository(
				agentcoreRuntimeImage.repository,
				agentcoreRuntimeImage.imageTag
			),
			description: 'Bidi Strands Agent'
		});

    // Bedrock 基盤モデル・Inference Profile へのアクセス権限
		runtime.role.addToPrincipalPolicy(new iam.PolicyStatement({
			actions: [
				'bedrock:InvokeModel',
				'bedrock:InvokeModelWithResponseStream',
			],
			resources: [
				// 基盤モデル（東京・大阪）
				'arn:aws:bedrock:ap-northeast-1::foundation-model/*',
				'arn:aws:bedrock:ap-northeast-3::foundation-model/*',
				// Inference Profile（東京・大阪）
				`arn:aws:bedrock:ap-northeast-1:${this.account}:inference-profile/*`,
				`arn:aws:bedrock:ap-northeast-3:${this.account}:inference-profile/*`,
			],
		}));

  }
}