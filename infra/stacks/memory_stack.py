"""CDK Memory Stack — AgentCore Memory store configuration and access policies.

Since AgentCore Memory does not yet have native CDK L2 constructs, this stack
creates SSM parameters for the memory store configuration and an IAM policy
for AgentCore Memory access.  The actual memory store is created at runtime
via the AgentCoreMemoryAdapter.create_store() method.

Requirements: 3.4, 4.1
"""

from aws_cdk import (
    CfnOutput,
    Stack,
)
from aws_cdk import aws_iam as iam
from aws_cdk import aws_ssm as ssm
from constructs import Construct


class MemoryStack(Stack):
    """Configures AgentCore Memory store metadata and access policies.

    Resources created:
    - SSM Parameter ``/openclaw/memory-store-id`` (placeholder, populated at deploy time)
    - IAM Managed Policy granting AgentCore Memory operations
    - Stack outputs for cross-stack references
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SSM Parameter for Memory Store ID ---
        # Populated during first deployment or via the AgentCoreMemoryAdapter.
        self.memory_store_id_param = ssm.StringParameter(
            self,
            "MemoryStoreIdParam",
            parameter_name="/openclaw/memory-store-id",
            string_value="PLACEHOLDER",
            description="AgentCore Memory store ID — updated after store creation",
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
                        "bedrock:InvokeAgent",
                        "bedrock-agent-runtime:InvokeAgent",
                        "bedrock-agent-runtime:Retrieve",
                        "bedrock-agent-runtime:RetrieveAndGenerate",
                    ],
                    resources=["*"],
                ),
                # AgentCore Memory management (create / describe stores)
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agent:CreateMemory",
                        "bedrock-agent:GetMemory",
                        "bedrock-agent:ListMemories",
                        "bedrock-agent:DeleteMemory",
                    ],
                    resources=["*"],
                ),
                # SSM read access for the memory store ID parameter
                iam.PolicyStatement(
                    actions=[
                        "ssm:GetParameter",
                        "ssm:GetParameters",
                    ],
                    resources=[self.memory_store_id_param.parameter_arn],
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
