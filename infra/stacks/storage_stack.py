"""CDK Storage Stack — S3 buckets and DynamoDB tables for OpenClaw.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6
"""

from aws_cdk import (
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_s3 as s3
from constructs import Construct


class StorageStack(Stack):
    """Creates S3 buckets and DynamoDB tables for the OpenClaw platform."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- S3 Buckets ---

        # Versioned workspace bucket: {tenantId}/{agentId}/{filename}
        self.workspace_bucket = s3.Bucket(
            self,
            "WorkspaceBucket",
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Skills registry bucket
        self.skills_bucket = s3.Bucket(
            self,
            "SkillsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Artifacts bucket
        self.artifacts_bucket = s3.Bucket(
            self,
            "ArtifactsBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # --- DynamoDB Tables ---

        # openclaw-memory: PK=agent_id(S), SK=file_key(S)
        self.memory_table = dynamodb.Table(
            self,
            "MemoryTable",
            table_name="openclaw-memory",
            partition_key=dynamodb.Attribute(
                name="agent_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="file_key", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # openclaw-sessions: PK=agent_id(S), SK=session_id(S), TTL=ttl
        self.sessions_table = dynamodb.Table(
            self,
            "SessionsTable",
            table_name="openclaw-sessions",
            partition_key=dynamodb.Attribute(
                name="agent_id", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="session_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )

        # openclaw-agents: PK=agent_id(S)
        self.agents_table = dynamodb.Table(
            self,
            "AgentsTable",
            table_name="openclaw-agents",
            partition_key=dynamodb.Attribute(
                name="agent_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # openclaw-dedup: PK=webhook_id(S), TTL=ttl
        self.dedup_table = dynamodb.Table(
            self,
            "DedupTable",
            table_name="openclaw-dedup",
            partition_key=dynamodb.Attribute(
                name="webhook_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )

        # openclaw-connections: PK=connection_id(S), TTL=ttl
        self.connections_table = dynamodb.Table(
            self,
            "ConnectionsTable",
            table_name="openclaw-connections",
            partition_key=dynamodb.Attribute(
                name="connection_id", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )
