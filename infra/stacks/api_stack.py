"""CDK API Stack — HTTP API Gateway, WebSocket API Gateway, and webhook Lambda for OpenClaw.

Creates:
- HTTP API Gateway with ``POST /webhook/{proxy+}`` route forwarding to a webhook Lambda
- WebSocket API Gateway (placeholder, wired to Gateway service later)
- SQS queue for webhook message forwarding
- Webhook Lambda with SQS, DynamoDB dedup, and Secrets Manager permissions
- API Gateway throttling at 100 requests/second per route

Requirements: 6.1, 6.2, 6.5
"""

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_secretsmanager as secretsmanager
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class ApiStack(Stack):
    """Creates API Gateway endpoints and webhook processing infrastructure.

    Accepts references from the storage stack (dedup table) and identity stack
    (secrets) so the webhook Lambda can be granted appropriate permissions.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        dedup_table: dynamodb.ITable,
        telegram_bot_token_secret: secretsmanager.ISecret,
        slack_bot_token_secret: secretsmanager.ISecret,
        github_token_secret: secretsmanager.ISecret,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SQS Queue for webhook message forwarding ---
        self.webhook_queue = sqs.Queue(
            self,
            "WebhookQueue",
            queue_name="openclaw-webhook-queue",
            visibility_timeout=Duration.seconds(60),
            retention_period=Duration.days(4),
        )

        # --- Webhook Lambda Handler ---
        self.webhook_lambda = _lambda.Function(
            self,
            "WebhookHandler",
            function_name="openclaw-webhook-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="webhook_handler.handler",
            code=_lambda.Code.from_asset("../lambdas"),
            timeout=Duration.seconds(30),
            memory_size=256,
            environment={
                "DEDUP_TABLE_NAME": dedup_table.table_name,
                "SQS_QUEUE_URL": self.webhook_queue.queue_url,
            },
        )

        # Grant Lambda permissions: DynamoDB read/write on dedup table
        dedup_table.grant_read_write_data(self.webhook_lambda)

        # Grant Lambda permissions: SQS send message
        self.webhook_queue.grant_send_messages(self.webhook_lambda)

        # Grant Lambda permissions: Secrets Manager read for all platform secrets
        telegram_bot_token_secret.grant_read(self.webhook_lambda)
        slack_bot_token_secret.grant_read(self.webhook_lambda)
        github_token_secret.grant_read(self.webhook_lambda)

        # --- HTTP API Gateway ---
        self.http_api = apigwv2.CfnApi(
            self,
            "HttpApi",
            name="openclaw-http-api",
            protocol_type="HTTP",
        )

        # Lambda integration for webhook route
        webhook_integration = apigwv2.CfnIntegration(
            self,
            "WebhookIntegration",
            api_id=self.http_api.ref,
            integration_type="AWS_PROXY",
            integration_uri=self.webhook_lambda.function_arn,
            payload_format_version="2.0",
        )

        # POST /webhook/{proxy+} route
        apigwv2.CfnRoute(
            self,
            "WebhookRoute",
            api_id=self.http_api.ref,
            route_key="POST /webhook/{proxy+}",
            target=f"integrations/{webhook_integration.ref}",
        )

        # Default stage with throttling: 100 req/s per route
        self.http_stage = apigwv2.CfnStage(
            self,
            "HttpDefaultStage",
            api_id=self.http_api.ref,
            stage_name="$default",
            auto_deploy=True,
            default_route_settings=apigwv2.CfnStage.RouteSettingsProperty(
                throttling_burst_limit=100,
                throttling_rate_limit=100,
            ),
        )

        # Grant API Gateway permission to invoke the webhook Lambda
        self.webhook_lambda.add_permission(
            "ApiGatewayInvoke",
            principal=iam.ServicePrincipal("apigateway.amazonaws.com"),
            source_arn=f"arn:aws:execute-api:{self.region}:{self.account}:{self.http_api.ref}/*",
        )

        # --- WebSocket API Gateway (placeholder) ---
        self.websocket_api = apigwv2.CfnApi(
            self,
            "WebSocketApi",
            name="openclaw-websocket-api",
            protocol_type="WEBSOCKET",
            route_selection_expression="$request.body.action",
        )

        # WebSocket stage with auto-deploy
        self.websocket_stage = apigwv2.CfnStage(
            self,
            "WebSocketDefaultStage",
            api_id=self.websocket_api.ref,
            stage_name="production",
            auto_deploy=True,
            default_route_settings=apigwv2.CfnStage.RouteSettingsProperty(
                throttling_burst_limit=100,
                throttling_rate_limit=100,
            ),
        )

        # --- Exported properties ---
        self.api_endpoint = (
            f"https://{self.http_api.ref}.execute-api.{self.region}.amazonaws.com"
        )
        self.websocket_endpoint = (
            f"wss://{self.websocket_api.ref}.execute-api.{self.region}.amazonaws.com/production"
        )
