"""CDK Memory Stack — AgentCore Memory configuration and MemoryAgent deployment.

Creates SSM parameters for memory store configuration, IAM policies for
AgentCore Memory access, and the MemoryAgent ECR repository for the
FastMCP-based memory server that runs on AgentCore Runtime.

The actual AgentCore Memory store and Runtime are created by the agent
at first boot using the agentcore-agent-builder skill. This stack provides
the foundational resources they depend on.

Requirements: 3.4, 4.1
"""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class MemoryStack(Stack):
    """Configures AgentCore Memory metadata, access policies, and MemoryAgent infra.

    Resources created:
    - SSM Parameter ``/openclaw/memory-store-id`` (populated after first agent boot)
    - SSM Parameter ``/openclaw/memory-semantic-strategy-id``
    - SSM Parameter ``/openclaw/memory-prefs-strategy-id``
    - IAM Managed Policy granting AgentCore Memory operations
    - ECR Repository for the MemoryAgent container image
    - Stack outputs for cross-stack references
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SSM Parameters for Memory Store Configuration ---
        # These are populated by the agent during first boot via the
        # agentcore-agent-builder skill. The agent creates the memory
        # store, discovers the strategy IDs, and writes them here.
        self.memory_store_id_param = ssm.StringParameter(
            self,
            "MemoryStoreIdParam",
            parameter_name="/openclaw/memory-store-id",
            string_value="PENDING_AGENT_INIT",
            description="AgentCore Memory store ID — populated by agent at first boot",
        )

        self.semantic_strategy_param = ssm.StringParameter(
            self,
            "SemanticStrategyParam",
            parameter_name="/openclaw/memory-semantic-strategy-id",
            string_value="PENDING_AGENT_INIT",
            description="AgentCore Memory semantic facts strategy ID",
        )

        self.prefs_strategy_param = ssm.StringParameter(
            self,
            "PrefsStrategyParam",
            parameter_name="/openclaw/memory-prefs-strategy-id",
            string_value="PENDING_AGENT_INIT",
            description="AgentCore Memory user preferences strategy ID",
        )

        # --- ECR Repository for MemoryAgent ---
        # The agent builds and pushes the MemoryAgent container image
        # via CodeBuild. Source code lives in agents/memory-agent/.
        self.memory_agent_repo = ecr.Repository(
            self,
            "MemoryAgentRepo",
            repository_name="memory-agent",
            removal_policy=RemovalPolicy.RETAIN,
            image_scan_on_push=True,
        )

        # --- IAM Managed Policy for AgentCore Memory access ---
        self.memory_access_policy = iam.ManagedPolicy(
            self,
            "AgentCoreMemoryAccessPolicy",
            managed_policy_name="openclaw-agentcore-memory-access",
            statements=[
                # AgentCore Memory runtime operations (ingest / retrieve)
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:ListMemoryRecords",
                    ],
                    resources=["*"],
                ),
                # AgentCore Memory management (create / describe stores)
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agentcore:CreateMemory",
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:ListMemories",
                        "bedrock-agentcore:DeleteMemory",
                    ],
                    resources=["*"],
                ),
                # AgentCore control plane (for agent-builder skill)
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agentcore-control:CreateAgentRuntime",
                        "bedrock-agentcore-control:UpdateAgentRuntime",
                        "bedrock-agentcore-control:GetAgentRuntime",
                        "bedrock-agentcore-control:CreateGatewayTarget",
                        "bedrock-agentcore-control:GetGatewayTarget",
                        "bedrock-agentcore-control:ListGatewayTargets",
                        "bedrock-agentcore-control:CreateOauth2CredentialProvider",
                    ],
                    resources=["*"],
                ),
                # SSM read/write for memory store parameters
                iam.PolicyStatement(
                    actions=[
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                        "ssm:PutParameter",
                    ],
                    resources=[
                        self.memory_store_id_param.parameter_arn,
                        self.semantic_strategy_param.parameter_arn,
                        self.prefs_strategy_param.parameter_arn,
                    ],
                ),
            ],
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "MemoryStoreIdParamName",
            value=self.memory_store_id_param.parameter_name,
            description="SSM parameter name holding the AgentCore Memory store ID",
            export_name="OpenClawMemoryStoreIdParam",
        )

        CfnOutput(
            self,
            "MemoryAccessPolicyArn",
            value=self.memory_access_policy.managed_policy_arn,
            description="ARN of the IAM policy granting AgentCore Memory access",
            export_name="OpenClawMemoryAccessPolicyArn",
        )

        CfnOutput(
            self,
            "MemoryAgentRepoUri",
            value=self.memory_agent_repo.repository_uri,
            description="ECR repository URI for the MemoryAgent container image",
            export_name="OpenClawMemoryAgentRepoUri",
        )
