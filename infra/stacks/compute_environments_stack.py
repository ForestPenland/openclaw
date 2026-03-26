"""CDK Compute Environments Stack — DynamoDB tables, cleanup Lambda,
permission boundary, and SNS notifications for dynamic execution environments.

Creates the infrastructure needed by the Role Manager, Lifecycle Manager,
Tool Registry, and Cost Ledger.

Requirements: 7.3, 7.4, 7.7, 7.10, 8.1, 8.3, 9.3, 10.2
"""

from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from constructs import Construct


class ComputeEnvironmentsStack(Stack):
    """Infrastructure for dynamic execution environments.

    - DynamoDB tables for environment tracking, role tracking, tool registry, cost ledger
    - Permission boundary IAM managed policy
    - Cleanup Lambda on dual EventBridge schedules
    - SNS topic for lifecycle notifications
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── DynamoDB Tables ─────────────────────────────────────

        self.environments_table = dynamodb.Table(
            self,
            "EnvironmentsTable",
            table_name="openclaw-environments",
            partition_key=dynamodb.Attribute(
                name="environmentId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.agent_roles_table = dynamodb.Table(
            self,
            "AgentRolesTable",
            table_name="openclaw-agent-roles",
            partition_key=dynamodb.Attribute(
                name="roleName", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.tool_registry_table = dynamodb.Table(
            self,
            "ToolRegistryTable",
            table_name="openclaw-tool-registry",
            partition_key=dynamodb.Attribute(
                name="toolName", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        self.cost_ledger_table = dynamodb.Table(
            self,
            "CostLedgerTable",
            table_name="openclaw-cost-ledger",
            partition_key=dynamodb.Attribute(
                name="date", type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="environmentId", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ── Permission Boundary ─────────────────────────────────
        # Hard security ceiling for all agent-created (agent-task-*) roles.
        # Strategy: allow all AWS service operations within this account,
        # deny cross-account access and privilege escalation. Least-privilege
        # is enforced at the individual role policy level, not here.

        _ACCOUNT = Stack.of(self).account
        _BOUNDARY_ARN = f"arn:aws:iam::{_ACCOUNT}:policy/agent-permission-boundary"

        self.permission_boundary = iam.ManagedPolicy(
            self,
            "AgentPermissionBoundary",
            managed_policy_name="agent-permission-boundary",
            statements=[
                # ── Allow all actions on resources in this account ───
                iam.PolicyStatement(
                    sid="AllowAllInAccount",
                    effect=iam.Effect.ALLOW,
                    actions=["*"],
                    resources=["*"],
                    conditions={
                        "StringEquals": {
                            "aws:ResourceAccount": _ACCOUNT,
                        }
                    },
                ),
                # Global/non-regional actions that don't support
                # aws:ResourceAccount (IAM list, STS identity, S3 list, etc.)
                iam.PolicyStatement(
                    sid="AllowGlobalActions",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "iam:ListRoles",
                        "iam:ListPolicies",
                        "iam:GetPolicy",
                        "iam:GetPolicyVersion",
                        "iam:ListInstanceProfiles",
                        "iam:CreateServiceLinkedRole",
                        "sts:GetCallerIdentity",
                        "sts:GetSessionToken",
                        "s3:ListAllMyBuckets",
                        "s3:GetBucketLocation",
                        "bedrock:ListFoundationModels",
                        "bedrock:GetFoundationModel",
                    ],
                    resources=["*"],
                ),
                # ── Cross-account firewall ───────────────────────
                # Block assuming any role outside this account.
                iam.PolicyStatement(
                    sid="DenyCrossAccountAssume",
                    effect=iam.Effect.DENY,
                    actions=["sts:AssumeRole", "sts:AssumeRoleWithSAML", "sts:AssumeRoleWithWebIdentity"],
                    not_resources=[f"arn:aws:iam::{_ACCOUNT}:role/*"],
                ),
                # ── Boundary tamper protection ───────────────────
                # Cannot modify/delete the boundary policy itself.
                iam.PolicyStatement(
                    sid="DenyBoundaryTamper",
                    effect=iam.Effect.DENY,
                    actions=[
                        "iam:DeletePolicy",
                        "iam:DeletePolicyVersion",
                        "iam:CreatePolicyVersion",
                        "iam:SetDefaultPolicyVersion",
                    ],
                    resources=[_BOUNDARY_ARN],
                ),
                # Cannot remove or change the boundary on agent-task-* roles.
                iam.PolicyStatement(
                    sid="DenyBoundaryRemoval",
                    effect=iam.Effect.DENY,
                    actions=[
                        "iam:DeleteRolePermissionsBoundary",
                        "iam:PutRolePermissionsBoundary",
                    ],
                    resources=[f"arn:aws:iam::{_ACCOUNT}:role/agent-task-*"],
                ),
                # ── Privilege escalation prevention ──────────────
                # No long-lived credentials (users, access keys, login profiles).
                iam.PolicyStatement(
                    sid="DenyLongLivedCredentials",
                    effect=iam.Effect.DENY,
                    actions=[
                        "iam:CreateUser",
                        "iam:CreateAccessKey",
                        "iam:CreateLoginProfile",
                        "iam:UpdateLoginProfile",
                        "iam:CreateSAMLProvider",
                        "iam:UpdateSAMLProvider",
                        "iam:CreateOpenIDConnectProvider",
                    ],
                    resources=["*"],
                ),
                # ── Account/billing lockout ──────────────────
                # Allow cost visibility (CE, CUR, Budgets) but deny
                # billing payment and account modification.
                iam.PolicyStatement(
                    sid="DenyAccountAndBilling",
                    effect=iam.Effect.DENY,
                    actions=[
                        "organizations:*",
                        "account:*",
                        "aws-portal:ModifyBilling",
                        "aws-portal:ModifyPaymentMethods",
                        "aws-portal:ModifyAccount",
                    ],
                    resources=["*"],
                ),
            ],
        )

        # ── SNS Topic ───────────────────────────────────────────

        self.notifications_topic = sns.Topic(
            self,
            "LifecycleNotifications",
            topic_name="openclaw-environment-lifecycle",
        )

        # ── Cleanup Lambda ──────────────────────────────────────

        cleanup_fn = lambda_.Function(
            self,
            "CleanupFunction",
            function_name="openclaw-environment-cleanup",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline(_CLEANUP_LAMBDA_CODE),
            timeout=Duration.minutes(5),
            memory_size=256,
            environment={
                "ENVIRONMENTS_TABLE": self.environments_table.table_name,
                "AGENT_ROLES_TABLE": self.agent_roles_table.table_name,
                "SNS_TOPIC_ARN": self.notifications_topic.topic_arn,
            },
            log_retention=logs.RetentionDays.TWO_WEEKS,
        )

        # Grant Lambda access to DynamoDB tables
        self.environments_table.grant_read_write_data(cleanup_fn)
        self.agent_roles_table.grant_read_write_data(cleanup_fn)
        self.notifications_topic.grant_publish(cleanup_fn)

        # Grant Lambda IAM permissions for role cleanup
        cleanup_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "iam:DeleteRole",
                    "iam:DeleteRolePolicy",
                    "iam:ListRolePolicies",
                    "iam:ListAttachedRolePolicies",
                    "iam:DetachRolePolicy",
                ],
                resources=[
                    f"arn:aws:iam::{Stack.of(self).account}:role/agent-task-*"
                ],
            )
        )

        # Grant Lambda permissions to terminate compute resources
        cleanup_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:TerminateInstances",
                    "ec2:DescribeInstances",
                    "ecs:StopTask",
                    "ecs:DescribeTasks",
                    "codebuild:StopBuild",
                    "codebuild:BatchGetBuilds",
                ],
                resources=["*"],
            )
        )

        # ── EventBridge Schedules ───────────────────────────────

        # Every 30 minutes: environment cleanup
        events.Rule(
            self,
            "EnvironmentCleanupSchedule",
            schedule=events.Schedule.rate(Duration.minutes(30)),
            targets=[
                targets.LambdaFunction(
                    cleanup_fn,
                    event=events.RuleTargetInput.from_object(
                        {"action": "cleanup_environments"}
                    ),
                )
            ],
        )

        # Every 6 hours: role cleanup
        events.Rule(
            self,
            "RoleCleanupSchedule",
            schedule=events.Schedule.rate(Duration.hours(6)),
            targets=[
                targets.LambdaFunction(
                    cleanup_fn,
                    event=events.RuleTargetInput.from_object(
                        {"action": "cleanup_roles"}
                    ),
                )
            ],
        )


# ── Inline Lambda Code ──────────────────────────────────────────

_CLEANUP_LAMBDA_CODE = """
import json
import os
import time

import boto3

dynamodb = boto3.resource("dynamodb")
iam = boto3.client("iam")
sns = boto3.client("sns")

ENVIRONMENTS_TABLE = os.environ["ENVIRONMENTS_TABLE"]
AGENT_ROLES_TABLE = os.environ["AGENT_ROLES_TABLE"]
SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]

ROLE_EXPIRY_HOURS = 24


def handler(event, context):
    action = event.get("action", "cleanup_environments")

    if action == "cleanup_environments":
        return cleanup_environments()
    elif action == "cleanup_roles":
        return cleanup_roles()
    else:
        return {"error": f"Unknown action: {action}"}


def cleanup_environments():
    table = dynamodb.Table(ENVIRONMENTS_TABLE)
    now_ms = int(time.time() * 1000)
    terminated = 0

    # Scan for active environments that exceeded their max lifetime
    response = table.scan(
        FilterExpression="#s = :active",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":active": "active"},
    )

    for item in response.get("Items", []):
        created_at = int(item.get("createdAt", 0))
        max_lifetime_min = int(item.get("maxLifetimeMinutes", 240))
        max_lifetime_ms = max_lifetime_min * 60 * 1000

        if now_ms - created_at > max_lifetime_ms:
            env_id = item["environmentId"]
            backend = item.get("backend", "unknown")

            # Mark as terminated in DynamoDB
            table.update_item(
                Key={"environmentId": env_id},
                UpdateExpression="SET #s = :terminated",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":terminated": "terminated"},
            )

            # Send SNS notification
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"Environment auto-terminated: {env_id}",
                Message=json.dumps({
                    "environmentId": env_id,
                    "backend": backend,
                    "reason": "exceeded max lifetime",
                }),
            )
            terminated += 1

    return {"action": "cleanup_environments", "terminated": terminated}


def cleanup_roles():
    table = dynamodb.Table(AGENT_ROLES_TABLE)
    now_ms = int(time.time() * 1000)
    expiry_ms = ROLE_EXPIRY_HOURS * 3600 * 1000
    deleted = 0

    response = table.scan(
        FilterExpression="#s = :active",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":active": "active"},
    )

    for item in response.get("Items", []):
        created_at = int(item.get("createdAt", 0))

        if now_ms - created_at > expiry_ms:
            role_name = item["roleName"]

            try:
                # Delete inline policies
                policies = iam.list_role_policies(RoleName=role_name)
                for policy_name in policies.get("PolicyNames", []):
                    iam.delete_role_policy(
                        RoleName=role_name, PolicyName=policy_name
                    )

                # Detach managed policies
                attached = iam.list_attached_role_policies(RoleName=role_name)
                for policy in attached.get("AttachedPolicies", []):
                    iam.detach_role_policy(
                        RoleName=role_name, PolicyArn=policy["PolicyArn"]
                    )

                # Delete the role
                iam.delete_role(RoleName=role_name)

                # Update DynamoDB
                table.update_item(
                    Key={"roleName": role_name},
                    UpdateExpression="SET #s = :deleted",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":deleted": "deleted"},
                )
                deleted += 1

            except Exception as e:
                print(f"Failed to delete role {role_name}: {e}")

    return {"action": "cleanup_roles", "deleted": deleted}
"""
