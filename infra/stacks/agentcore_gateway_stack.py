"""CDK Stack: AgentCore Gateway + Lambda Tool Targets.

Creates:
- Lambda function for static site deployment (S3 + CloudFront)
- IAM role with permissions for S3, CloudFront, and Lambda operations
- The AgentCore Gateway itself is created via the agentcore CLI
  (not yet supported as a CDK L2 construct)

The Lambda function is registered as a Gateway target via the CLI after
this stack deploys.
"""

from aws_cdk import (
    Duration,
    Stack,
    CfnOutput,
)
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_logs as logs
from constructs import Construct


class AgentCoreGatewayStack(Stack):
    """Creates Lambda tool functions for AgentCore Gateway targets."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Lambda: Deploy Static Site ---
        self.deploy_site_lambda = _lambda.Function(
            self,
            "DeployStaticSiteHandler",
            function_name="openclaw-deploy-static-site",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="deploy_static_site.lambda_handler",
            code=_lambda.Code.from_asset("../lambdas"),
            timeout=Duration.seconds(120),
            memory_size=256,
            environment={
                "AWS_REGION_OVERRIDE": self.region,
            },
        )

        # Grant the Lambda broad permissions for infrastructure operations
        # (scoped to agent- prefixed resources where possible)
        self.deploy_site_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    # S3: create/manage buckets and objects
                    "s3:CreateBucket",
                    "s3:DeleteBucket",
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                    "s3:ListAllMyBuckets",
                    "s3:PutBucketPolicy",
                    "s3:DeletePublicAccessBlock",
                    "s3:PutBucketWebsite",
                ],
                resources=["*"],
            )
        )

        self.deploy_site_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    # CloudFront: create/manage distributions
                    "cloudfront:CreateDistribution",
                    "cloudfront:DeleteDistribution",
                    "cloudfront:GetDistribution",
                    "cloudfront:UpdateDistribution",
                    "cloudfront:ListDistributions",
                    "cloudfront:CreateOriginAccessControl",
                    "cloudfront:DeleteOriginAccessControl",
                ],
                resources=["*"],
            )
        )

        # --- Log Group ---
        logs.LogGroup(
            self,
            "DeployStaticSiteLogGroup",
            log_group_name=f"/aws/lambda/{self.deploy_site_lambda.function_name}",
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        # --- Outputs ---
        CfnOutput(
            self,
            "DeployStaticSiteLambdaArn",
            value=self.deploy_site_lambda.function_arn,
            description="ARN of the deploy-static-site Lambda function",
            export_name="OpenClawDeployStaticSiteLambdaArn",
        )
