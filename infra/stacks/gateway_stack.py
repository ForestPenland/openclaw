"""CDK Gateway Stack — ECS Fargate deployment for OpenClaw Gateway.

Builds a custom Docker image (Dockerfile.gateway) containing the full
OpenClaw application, AWS CLI, and an entrypoint that syncs workspace
files from S3 before starting the gateway.

CDK builds the image locally and pushes it to an ECR repository
managed by the CDK bootstrap stack.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""

import os

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_dynamodb as dynamodb
from constructs import Construct

# Path to the OpenClaw project root (one level above infra/)
_PROJECT_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)


class GatewayStack(Stack):
    """Deploys the OpenClaw Gateway as a single ECS Fargate task.

    Accepts references to the storage stack's S3 bucket and DynamoDB tables
    so the task role can be granted appropriate permissions.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        workspace_bucket: s3.IBucket,
        memory_table: dynamodb.ITable,
        sessions_table: dynamodb.ITable,
        agents_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- VPC ---
        self.vpc = ec2.Vpc(
            self,
            "GatewayVpc",
            max_azs=2,
            nat_gateways=1,
        )

        # --- CloudWatch Log Group ---
        # Let CloudWatch auto-name the log group to avoid conflicts with
        # orphaned log groups from previous failed deployments.
        self.log_group = logs.LogGroup(
            self,
            "GatewayLogGroup",
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        # --- ECS Cluster ---
        self.cluster = ecs.Cluster(
            self,
            "GatewayCluster",
            vpc=self.vpc,
        )

        # --- Task Definition (1024 CPU, 2048 MiB) ---
        # Bumped from 512/1024 — the gateway + Node.js runtime + S3 sync
        # needs headroom to avoid OOM during workspace assembly.
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "GatewayTaskDef",
            cpu=1024,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # --- Task Role Permissions ---
        task_role = self.task_definition.task_role

        # S3: read/write workspace files
        workspace_bucket.grant_read_write(task_role)
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[workspace_bucket.bucket_arn],
            )
        )

        # Bedrock: model invocation
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        # DynamoDB: access to memory, sessions, and agents tables
        memory_table.grant_read_write_data(task_role)
        sessions_table.grant_read_write_data(task_role)
        agents_table.grant_read_write_data(task_role)

        # AgentCore: runtime operations
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["bedrock-agent-runtime:*"],
                resources=["*"],
            )
        )

        # Secrets Manager: read channel secrets (Telegram bot token, etc.)
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=["arn:aws:secretsmanager:*:*:secret:openclaw/*"],
            )
        )

        # --- Container Image (custom build) ---
        # CDK builds Dockerfile.gateway from the project root, pushes to
        # ECR, and references the image in the task definition.
        #
        # IMPORTANT: exclude infra/ (contains cdk.out which would cause
        # recursive copy → ENAMETOOLONG), .git, node_modules, and other
        # large directories that the .dockerignore already handles but
        # CDK's asset staging does not read.
        gateway_image = ecs.ContainerImage.from_asset(
            _PROJECT_ROOT,
            file="Dockerfile.gateway",
            exclude=[
                "infra",
                ".git",
                ".kiro",
                "node_modules",
                ".pnpm-store",
                "coverage",
                "dist",
                "apps/macos",
                "apps/ios",
                "apps/android",
                "Swabble",
                "test",
                "test-fixtures",
                "tests",
            ],
        )

        # --- Container Definition ---
        self.container = self.task_definition.add_container(
            "GatewayContainer",
            image=gateway_image,
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="openclaw-gateway",
                log_group=self.log_group,
            ),
            environment={
                "WORKSPACE_BUCKET": workspace_bucket.bucket_name,
                "TENANT_ID": "default-tenant",
                "AGENT_ID": "default-agent",
                "PROVIDER": "bedrock",
                "TELEGRAM_SECRET_NAME": "openclaw/telegram-bot-token",
                "BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0",
                "OPENCLAW_ALLOW_INSECURE_PRIVATE_WS": "1",
            },
            essential=True,
            health_check=ecs.HealthCheck(
                command=[
                    "CMD-SHELL",
                    'node -e "fetch(\'http://127.0.0.1:18789/healthz\').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"',
                ],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        self.container.add_port_mappings(
            ecs.PortMapping(container_port=18789, protocol=ecs.Protocol.TCP)
        )

        # --- ECS Service (desired count 1, auto-restart on crash/unhealthy) ---
        self.service = ecs.FargateService(
            self,
            "GatewayService",
            cluster=self.cluster,
            task_definition=self.task_definition,
            desired_count=1,
            assign_public_ip=True,
            enable_execute_command=True,
        )
