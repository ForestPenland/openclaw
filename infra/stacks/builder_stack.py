"""CDK Builder Stack — Builder agent IAM role with Permission Boundary.

Requirements: 10.6
"""

from aws_cdk import (
    CfnOutput,
    Stack,
)
from aws_cdk import aws_iam as iam
from constructs import Construct


class BuilderStack(Stack):
    """Creates the Builder agent IAM role with a Permission Boundary.

    The Permission Boundary denies IAM, Organizations, Account, and Billing
    actions to prevent privilege escalation by the Builder sub-agent.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Permission Boundary ---
        # Deny sensitive service actions (Req 10.6)
        self.permission_boundary = iam.ManagedPolicy(
            self,
            "BuilderPermissionBoundary",
            managed_policy_name="openclaw-builder-permission-boundary",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["*"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.DENY,
                    actions=[
                        "iam:*",
                        "organizations:*",
                        "account:*",
                        "aws-portal:*",
                        "budgets:*",
                        "ce:*",
                        "cur:*",
                    ],
                    resources=["*"],
                ),
            ],
        )

        # --- Builder Agent IAM Role ---
        self.builder_role = iam.Role(
            self,
            "BuilderAgentRole",
            role_name="openclaw-builder-agent",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("lambda.amazonaws.com"),
                iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            ),
            permissions_boundary=self.permission_boundary,
        )

        # CloudFormation: deploy stacks
        self.builder_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "cloudformation:CreateStack",
                    "cloudformation:UpdateStack",
                    "cloudformation:DeleteStack",
                    "cloudformation:DescribeStacks",
                    "cloudformation:DescribeStackEvents",
                    "cloudformation:GetTemplate",
                    "cloudformation:ListStackResources",
                ],
                resources=["*"],
            )
        )

        # Lambda: create/update functions
        self.builder_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "lambda:CreateFunction",
                    "lambda:UpdateFunctionCode",
                    "lambda:UpdateFunctionConfiguration",
                    "lambda:GetFunction",
                    "lambda:ListVersionsByFunction",
                    "lambda:PublishVersion",
                    "lambda:CreateAlias",
                    "lambda:UpdateAlias",
                ],
                resources=["arn:aws:lambda:*:*:function:agent-*"],
            )
        )

        # S3: store artifacts
        self.builder_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:ListBucket",
                ],
                resources=["*"],
            )
        )

        # Bedrock: model invocation
        self.builder_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        # CloudWatch Logs
        self.builder_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=["*"],
            )
        )

        # --- Stack Output ---
        CfnOutput(
            self,
            "BuilderRoleArn",
            value=self.builder_role.role_arn,
            description="ARN of the Builder agent IAM role",
            export_name="OpenClawBuilderRoleArn",
        )
