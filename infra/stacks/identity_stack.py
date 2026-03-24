"""CDK Identity Stack — Cognito user pool and Secrets Manager secrets for OpenClaw.

Requirements: 8.1, 8.3, 8.6
"""

from aws_cdk import (
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class IdentityStack(Stack):
    """Creates Cognito user pool and Secrets Manager secrets for the OpenClaw platform.

    - Cognito user pool with invite-only sign-up and email-based sign-in (Req 8.1)
    - Cognito app client for Telegram channel with USER_PASSWORD_AUTH flow (Req 8.6)
    - Secrets Manager secrets under ``openclaw/`` namespace (Req 8.3)
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- Cognito User Pool ---
        # Invite-only sign-up (self_sign_up_enabled=False), email as sign-in alias
        self.user_pool = cognito.UserPool(
            self,
            "OpenClawUserPool",
            user_pool_name="openclaw-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(required=True, mutable=True),
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- Cognito App Client for Telegram ---
        # USER_PASSWORD_AUTH flow for Telegram channel
        self.telegram_app_client = self.user_pool.add_client(
            "TelegramAppClient",
            user_pool_client_name="openclaw-telegram",
            auth_flows=cognito.AuthFlow(
                user_password=True,
            ),
            generate_secret=False,
        )

        # --- Secrets Manager Secrets ---
        # All secrets under openclaw/ namespace (Req 8.3)
        self.telegram_bot_token_secret = secretsmanager.Secret(
            self,
            "TelegramBotTokenSecret",
            secret_name="openclaw/telegram-bot-token",
            description="Telegram bot token for OpenClaw agent",
        )

        self.slack_bot_token_secret = secretsmanager.Secret(
            self,
            "SlackBotTokenSecret",
            secret_name="openclaw/slack-bot-token",
            description="Slack bot token for OpenClaw agent",
        )

        self.github_token_secret = secretsmanager.Secret(
            self,
            "GitHubTokenSecret",
            secret_name="openclaw/github-token",
            description="GitHub token for OpenClaw agent",
        )
