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

        # ── Task Role Permissions ─────────────────────────────────
        # The task role is the base identity for an autonomous agent.
        # Broad service access is granted here; the permission boundary
        # on agent-created roles is the hard security ceiling.
        task_role = self.task_definition.task_role
        _ACCOUNT = Stack.of(self).account

        # S3: workspace bucket (CDK grant) + broad S3 for dynamic buckets
        workspace_bucket.grant_read_write(task_role)

        # DynamoDB: core tables (CDK grant)
        memory_table.grant_read_write_data(task_role)
        sessions_table.grant_read_write_data(task_role)
        agents_table.grant_read_write_data(task_role)

        # ── Broad service access for autonomous operation ────────
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AgentServiceAccess",
                actions=[
                    # S3 (dynamic bucket creation + management)
                    "s3:*",
                    # Bedrock (model invocation + discovery)
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ListFoundationModels",
                    "bedrock:GetFoundationModel",
                    "bedrock-agent-runtime:*",
                    "bedrock-agentcore:*",
                    "bedrock-agentcore-control:*",
                    # AWS Marketplace (third-party model access)
                    "aws-marketplace:ViewSubscriptions",
                    "aws-marketplace:Subscribe",
                    "aws-marketplace:Unsubscribe",
                    # Secrets Manager (full CRUD for agent-managed secrets)
                    "secretsmanager:CreateSecret",
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                    "secretsmanager:UpdateSecret",
                    "secretsmanager:DeleteSecret",
                    "secretsmanager:DescribeSecret",
                    "secretsmanager:ListSecrets",
                    # CodeBuild (CI/CD delegation)
                    "codebuild:StartBuild",
                    "codebuild:StopBuild",
                    "codebuild:BatchGetBuilds",
                    "codebuild:ListProjects",
                    "codebuild:BatchGetProjects",
                    "codebuild:CreateProject",
                    "codebuild:UpdateProject",
                    # ECR (container image management)
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                    "ecr:CreateRepository",
                    "ecr:DescribeRepositories",
                    "ecr:DeleteRepository",
                    # CloudFormation (infrastructure awareness)
                    "cloudformation:ListStacks",
                    "cloudformation:DescribeStacks",
                    "cloudformation:DescribeStackResources",
                    "cloudformation:GetTemplate",
                    "cloudformation:ListStackResources",
                    # Cognito (identity management for Gateway tools)
                    "cognito-idp:CreateUserPoolClient",
                    "cognito-idp:DescribeUserPoolClient",
                    "cognito-idp:DeleteUserPoolClient",
                    "cognito-idp:ListUserPoolClients",
                    "cognito-idp:CreateResourceServer",
                    "cognito-idp:DescribeResourceServer",
                    "cognito-idp:DeleteResourceServer",
                    "cognito-idp:UpdateUserPoolClient",
                    # CloudWatch Logs
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                    # SSM Parameter Store
                    "ssm:GetParameter",
                    "ssm:GetParameters",
                    "ssm:PutParameter",
                    "ssm:DeleteParameter",
                    "ssm:DescribeParameters",
                    # Lambda (tool creation + management)
                    "lambda:CreateFunction",
                    "lambda:UpdateFunctionCode",
                    "lambda:UpdateFunctionConfiguration",
                    "lambda:InvokeFunction",
                    "lambda:GetFunction",
                    "lambda:ListFunctions",
                    "lambda:DeleteFunction",
                    "lambda:AddPermission",
                    "lambda:RemovePermission",
                    # DynamoDB (dynamic table creation + full item ops)
                    "dynamodb:*",
                    # EC2 (read-only awareness)
                    "ec2:Describe*",
                    # ECS (read-only awareness)
                    "ecs:DescribeTasks",
                    "ecs:DescribeServices",
                    "ecs:ListTasks",
                    "ecs:ListServices",
                    "ecs:DescribeTaskDefinition",
                    # SNS (notifications)
                    "sns:Publish",
                    "sns:ListTopics",
                ],
                resources=["*"],
            )
        )

        # ── IAM: role factory (scoped to agent-task-* prefix) ────
        # CreateRole requires the boundary condition — agent cannot
        # create unbounded roles.
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowAgentTaskRoleCreate",
                actions=["iam:CreateRole"],
                resources=[f"arn:aws:iam::{_ACCOUNT}:role/agent-task-*"],
                conditions={
                    "StringEquals": {
                        "iam:PermissionsBoundary": f"arn:aws:iam::{_ACCOUNT}:policy/agent-permission-boundary"
                    }
                },
            )
        )

        # Manage existing agent-task-* roles
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowAgentTaskRoleManage",
                actions=[
                    "iam:DeleteRole",
                    "iam:PutRolePolicy",
                    "iam:GetRolePolicy",
                    "iam:DeleteRolePolicy",
                    "iam:AttachRolePolicy",
                    "iam:DetachRolePolicy",
                    "iam:TagRole",
                    "iam:GetRole",
                    "iam:ListRolePolicies",
                    "iam:ListAttachedRolePolicies",
                    "iam:UpdateAssumeRolePolicy",
                    "iam:PutRolePermissionsBoundary",
                    "iam:CreatePolicy",
                ],
                resources=[
                    f"arn:aws:iam::{_ACCOUNT}:role/agent-task-*",
                    f"arn:aws:iam::{_ACCOUNT}:policy/agent-task-*",
                ],
            )
        )

        # PassRole: allow passing ANY role in the account to AWS services.
        # The agent needs to hand execution roles to CodeBuild, Lambda,
        # ECS, Bedrock, etc. — these roles aren't always agent-task-*.
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowPassRoleToServices",
                actions=["iam:PassRole"],
                resources=[f"arn:aws:iam::{_ACCOUNT}:role/*"],
                conditions={
                    "StringLike": {
                        "iam:PassedToService": [
                            "codebuild.amazonaws.com",
                            "ecs-tasks.amazonaws.com",
                            "lambda.amazonaws.com",
                            "bedrock.amazonaws.com",
                            "bedrock-agentcore.amazonaws.com",
                            "events.amazonaws.com",
                            "states.amazonaws.com",
                        ]
                    }
                },
            )
        )

        # STS: assume agent-task-* roles for elevated permissions
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowAssumeAgentTaskRoles",
                actions=["sts:AssumeRole"],
                resources=[f"arn:aws:iam::{_ACCOUNT}:role/agent-task-*"],
            )
        )

        # STS: GetCallerIdentity on * (not resource-scoped)
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowSTSIdentity",
                actions=["sts:GetCallerIdentity"],
                resources=["*"],
            )
        )

        # IAM: read-only discovery + service-linked role creation
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowIAMDiscovery",
                actions=[
                    "iam:ListRoles",
                    "iam:ListPolicies",
                    "iam:GetPolicy",
                    "iam:GetPolicyVersion",
                    "iam:ListInstanceProfiles",
                    "iam:CreateServiceLinkedRole",
                ],
                resources=["*"],
            )
        )

        # CodeBuild: create and manage build projects for delegated builds
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowCodeBuild",
                actions=[
                    "codebuild:CreateProject",
                    "codebuild:DeleteProject",
                    "codebuild:UpdateProject",
                    "codebuild:StartBuild",
                    "codebuild:StopBuild",
                    "codebuild:BatchGetBuilds",
                    "codebuild:BatchGetProjects",
                    "codebuild:ListBuildsForProject",
                ],
                resources=["*"],
            )
        )

        # ECR: manage container images for agent-built services
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowECR",
                actions=[
                    "ecr:CreateRepository",
                    "ecr:DeleteRepository",
                    "ecr:DescribeRepositories",
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchGetImage",
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:BatchCheckLayerAvailability",
                ],
                resources=["*"],
            )
        )

        # IAM PassRole: allow passing agent-task-* roles to AWS services
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowPassRoleToServices",
                actions=["iam:PassRole"],
                resources=[
                    f"arn:aws:iam::{Stack.of(self).account}:role/agent-task-*"
                ],
                conditions={
                    "StringLike": {
                        "iam:PassedToService": [
                            "codebuild.amazonaws.com",
                            "ecs-tasks.amazonaws.com",
                            "lambda.amazonaws.com",
                            "bedrock.amazonaws.com",
                            "events.amazonaws.com",
                            "states.amazonaws.com",
                        ]
                    }
                },
            )
        )

        # AgentCore: control plane operations (create/manage runtimes)
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowAgentCoreControl",
                actions=[
                    "bedrock-agentcore:*",
                ],
                resources=["*"],
            )
        )

        # CloudWatch Logs: read build logs and create log groups
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchLogs",
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                resources=["*"],
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
            # Give the SIGTERM handler time to flush sessions to S3
            stop_timeout=Duration.seconds(120),
            environment={
                "WORKSPACE_BUCKET": workspace_bucket.bucket_name,
                "TENANT_ID": "default-tenant",
                "AGENT_ID": "default-agent",
                "PROVIDER": "bedrock",
                "TELEGRAM_SECRET_NAME": "openclaw/telegram-bot-token",
                "AGENTCORE_GATEWAY_SECRET_NAME": "openclaw/agentcore-gateway-credentials",
                "BEDROCK_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
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
