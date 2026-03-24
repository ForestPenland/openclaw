"""CDK Gateway Stack — ECS Fargate deployment for OpenClaw Gateway.

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5
"""

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
        self.log_group = logs.LogGroup(
            self,
            "GatewayLogGroup",
            log_group_name="/ecs/openclaw-gateway",
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        # --- ECS Cluster ---
        self.cluster = ecs.Cluster(
            self,
            "GatewayCluster",
            vpc=self.vpc,
        )

        # --- Task Definition (512 CPU, 1024 MiB) ---
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "GatewayTaskDef",
            cpu=512,
            memory_limit_mib=1024,
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

        # --- Container Definition ---
        # The container entrypoint installs the AWS CLI and curl (missing
        # from node:22-slim), syncs workspace files from S3, then starts
        # the OpenClaw Gateway process (Requirement 5.3).
        #
        # NOTE: node:22-slim is Debian-based but ships without aws-cli or
        # curl.  We install them at startup so the S3 sync and health
        # check work.  For production, consider building a custom image
        # with these baked in to avoid the ~15 s install overhead.
        startup_script = (
            "apt-get update -qq && apt-get install -y -qq curl unzip > /dev/null"
            " && curl -sL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscli.zip"
            " && unzip -q /tmp/awscli.zip -d /tmp"
            " && /tmp/aws/install"
            " && rm -rf /tmp/awscli.zip /tmp/aws"
            " && aws s3 sync s3://$WORKSPACE_BUCKET/ /workspace/"
            " && node /app/gateway.js"
        )

        self.container = self.task_definition.add_container(
            "GatewayContainer",
            image=ecs.ContainerImage.from_registry("public.ecr.aws/docker/library/node:22-slim"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="openclaw-gateway",
                log_group=self.log_group,
            ),
            environment={
                "WORKSPACE_BUCKET": workspace_bucket.bucket_name,
            },
            command=["sh", "-c", startup_script],
            essential=True,
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:18789/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(120),
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
