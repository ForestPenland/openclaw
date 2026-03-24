# Skill: Identity and Authentication

**Purpose:** How the AWS-enhanced OpenClaw system manages agent identity, user authentication, and credential handling — extending OpenClaw's native DM-pairing auth model with Cognito user pools, AgentCore Identity for OAuth, and Secrets Manager for credential storage.

---

## Hybrid Approach: OpenClaw Auth vs. AWS

OpenClaw's default security model is simple: unknown senders receive a pairing code; trusted users are whitelisted. This is fine for a single-user local agent but needs upgrading for cloud deployment.

| Feature | OpenClaw Native | AWS Extension | Migration Needed |
|---------|----------------|---------------|-----------------|
| User auth | DM pairing code | Cognito user pool | Yes — map Telegram IDs to Cognito users |
| Trusted users | Config allowlist | Cognito + API Gateway JWT authorizer | Yes — migrate allowlist to Cognito |
| API credentials | Hardcoded in config | Secrets Manager | Yes — move tokens out of config |
| Agent credentials | N/A | IAM roles (ECS task role, Lambda role) | New — required for cloud |
| Outbound OAuth | Manual config | AgentCore Identity credential provider | Optional — simplifies multi-service auth |

**What stays the same:**
- The Telegram DM pairing *behavior* is preserved — user still goes through a verification step on first contact
- You're just backing it with Cognito instead of a flat config allowlist

**Critical security rules (never change these):**
1. Telegram bot tokens → Secrets Manager only. Never in workspace files, never in environment variables directly.
2. Slack webhook URLs → Secrets Manager only.
3. IAM roles → principle of least privilege. Builder agent role has permission boundary.
4. All inbound webhooks → verify signatures (Telegram: HMAC-SHA256 of token; Slack: `X-Slack-Signature` header).

---

## AWS Services Used

- **Amazon Bedrock AgentCore Identity** — agent identity, OAuth credential management
- **Amazon Cognito** — user pool for human authentication
- **AWS IAM** — agent execution roles and permissions
- **AWS Secrets Manager** — credential storage for external services
- **AWS Systems Manager Parameter Store** — non-secret configuration
- **Amazon API Gateway** — JWT authorization on incoming requests

---

## Key APIs and Operations

### AgentCore Identity Client

```python
import boto3

identity_client = boto3.client(
    "bedrock-agentcore-identity",
    region_name="us-east-1"
)
```

### Setting Up OAuth Credential Provider

Register external service credentials for agents to use:

```python
def register_github_credential_provider() -> str:
    """
    Register GitHub as an OAuth credential provider for the agent.
    This allows agents to authenticate with GitHub using OAuth tokens.
    """
    response = identity_client.create_oauth2_credential_provider(
        name="github-credential-provider",
        credentialProviderVendor="CustomOIDC",
        oauth2ProviderConfigInput={
            "customOAuthProviderConfig": {
                "oauthDiscoveryUrl": "https://token.actions.githubusercontent.com/.well-known/openid-configuration",
                "clientId": os.environ["GITHUB_APP_CLIENT_ID"],
                "clientSecretArn": "arn:aws:secretsmanager:us-east-1:123456789:secret:openclaw/github/client-secret",
                "scopes": ["repo", "workflow", "read:org"]
            }
        }
    )
    return response["credentialProvider"]["credentialProviderArn"]


def register_stripe_credential_provider() -> str:
    """Register Stripe API key as a credential provider."""
    response = identity_client.create_api_key_credential_provider(
        name="stripe-credential-provider",
        apiKeyConfig={
            "apiKeySecretArn": "arn:aws:secretsmanager:us-east-1:123456789:secret:openclaw/stripe/api-key"
        }
    )
    return response["credentialProvider"]["credentialProviderArn"]


def register_slack_credential_provider() -> str:
    """Register Slack bot token as a credential provider."""
    response = identity_client.create_api_key_credential_provider(
        name="slack-credential-provider",
        apiKeyConfig={
            "apiKeySecretArn": "arn:aws:secretsmanager:us-east-1:123456789:secret:openclaw/slack/bot-token",
            "apiKeyPrefix": "Bearer "
        }
    )
    return response["credentialProvider"]["credentialProviderArn"]
```

### Getting Credentials for Outbound Tool Calls

When an agent needs to call GitHub, Stripe, Slack, etc.:

```python
def get_github_token(workload_access_token: str) -> str:
    """Get GitHub access token for agent tool calls."""
    response = identity_client.get_token_for_resource(
        workloadAccessToken=workload_access_token,
        resourceScope="github:repo:write",
        credentialProviderArn=os.environ["GITHUB_CREDENTIAL_PROVIDER_ARN"]
    )
    return response["accessToken"]


def get_stripe_key(workload_access_token: str) -> str:
    """Get Stripe API key for agent tool calls."""
    response = identity_client.get_token_for_resource(
        workloadAccessToken=workload_access_token,
        resourceScope="stripe:payments:read",
        credentialProviderArn=os.environ["STRIPE_CREDENTIAL_PROVIDER_ARN"]
    )
    return response["accessToken"]
```

### Inbound Authentication (Protecting the Agent)

Configure JWT validation for incoming requests to AgentCore Runtime:

```python
def configure_runtime_authorizer(runtime_arn: str, cognito_user_pool_id: str):
    """
    Configure JWT authorization for the AgentCore Runtime.
    Only authenticated users can invoke the agent.
    """
    agentcore = boto3.client("bedrock-agentcore")

    agentcore.update_agent_runtime(
        agentRuntimeArn=runtime_arn,
        authorizerConfiguration={
            "customJwtAuthorizer": {
                "discoveryUrl": f"https://cognito-idp.us-east-1.amazonaws.com/{cognito_user_pool_id}/.well-known/openid-configuration",
                "allowedAudiences": ["openclaw-agent-runtime"],
                "allowedClients": ["openclaw-web", "openclaw-mobile"],
                "allowedScopes": ["agent:invoke"]
            }
        }
    )
```

### Cognito User Authentication (Human Users)

```python
def authenticate_telegram_user(telegram_user_id: str, telegram_username: str) -> dict:
    """
    Map a Telegram user to a Cognito user.
    For OpenClaw's primary use case (single operator), this is mostly identity mapping.
    For multi-user deployments, this enforces who can access the agent.
    """
    cognito = boto3.client("cognito-idp")

    # Check if user exists
    try:
        user = cognito.admin_get_user(
            UserPoolId=os.environ["COGNITO_USER_POOL_ID"],
            Username=f"telegram-{telegram_user_id}"
        )
        return {
            "authenticated": True,
            "user_id": f"telegram-{telegram_user_id}",
            "username": telegram_username
        }
    except cognito.exceptions.UserNotFoundException:
        # For single-operator mode, check against allowed user list
        allowed_telegram_ids = os.environ.get("ALLOWED_TELEGRAM_IDS", "").split(",")
        if str(telegram_user_id) in allowed_telegram_ids:
            return {
                "authenticated": True,
                "user_id": f"telegram-{telegram_user_id}",
                "username": telegram_username,
                "note": "operator-level access"
            }
        return {"authenticated": False, "reason": "User not authorized"}


def get_jwt_for_agent_invocation(user_id: str) -> str:
    """
    Get a JWT token for invoking the AgentCore Runtime.
    In production, this comes from the user's Cognito session.
    For server-side invocation, use M2M client credentials.
    """
    cognito = boto3.client("cognito-idp")

    # Use client credentials flow for server-to-server calls
    response = cognito.initiate_auth(
        AuthFlow="CLIENT_CREDENTIALS",
        AuthParameters={
            "CLIENT_ID": os.environ["COGNITO_CLIENT_ID"],
            "CLIENT_SECRET": os.environ["COGNITO_CLIENT_SECRET"]
        },
        ClientId=os.environ["COGNITO_CLIENT_ID"]
    )

    return response["AuthenticationResult"]["AccessToken"]
```

---

## Implementation Patterns

### Pattern 1: IAM Role Architecture

The system uses multiple IAM roles with least-privilege permissions:

```python
# In CDK: core_stack.py

from aws_cdk import aws_iam as iam

# 1. Agent Runtime Execution Role
# Used by AgentCore Runtime to execute agent code
agent_runtime_role = iam.Role(
    self, "AgentRuntimeRole",
    role_name="openclaw-agent-runtime-role",
    assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
    description="Role for OpenClaw agent execution in AgentCore Runtime"
)

# Bedrock model access
agent_runtime_role.add_to_policy(iam.PolicyStatement(
    effect=iam.Effect.ALLOW,
    actions=["bedrock:InvokeModel", "bedrock:Converse", "bedrock:ConverseStream"],
    resources=["arn:aws:bedrock:*::foundation-model/anthropic.claude-*",
                "arn:aws:bedrock:*::foundation-model/amazon.nova-*"]
))

# AgentCore services
agent_runtime_role.add_to_policy(iam.PolicyStatement(
    effect=iam.Effect.ALLOW,
    actions=[
        "bedrock-agentcore:IngestConversationEvents",
        "bedrock-agentcore:RetrieveMemoryRecords",
        "bedrock-agentcore:InvokeGateway",
        "bedrock-agentcore:GetToken"
    ],
    resources=["*"]
))

# DynamoDB workspace access
agent_runtime_role.add_to_policy(iam.PolicyStatement(
    effect=iam.Effect.ALLOW,
    actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"],
    resources=[
        f"arn:aws:dynamodb:{self.region}:{self.account}:table/openclaw-workspace",
        f"arn:aws:dynamodb:{self.region}:{self.account}:table/openclaw-sessions"
    ]
))

# 2. Lambda Execution Role
# Used by all agent-deployed Lambda functions
lambda_execution_role = iam.Role(
    self, "LambdaExecutionRole",
    role_name="openclaw-lambda-execution-role",
    assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
    managed_policies=[
        iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
    ]
)

# Allow access to agent-deployed DynamoDB tables only
lambda_execution_role.add_to_policy(iam.PolicyStatement(
    effect=iam.Effect.ALLOW,
    actions=["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query", "dynamodb:Scan"],
    resources=[f"arn:aws:dynamodb:*:{self.account}:table/agent-*"]
))

# Allow reading secrets in agent namespace
lambda_execution_role.add_to_policy(iam.PolicyStatement(
    effect=iam.Effect.ALLOW,
    actions=["secretsmanager:GetSecretValue"],
    resources=[f"arn:aws:secretsmanager:*:{self.account}:secret:openclaw/agent-*"]
))
```

### Pattern 2: Secrets Management

```python
import boto3
from functools import lru_cache

secrets_client = boto3.client("secretsmanager")

@lru_cache(maxsize=50)
def get_secret(secret_name: str) -> str:
    """
    Retrieve a secret from Secrets Manager.
    Cached to avoid repeated API calls within a Lambda execution.
    """
    response = secrets_client.get_secret_value(SecretId=secret_name)
    return response["SecretString"]


def store_secret(secret_name: str, secret_value: str, description: str = "") -> str:
    """Store a new secret for an agent-deployed service."""
    # Only allow storing in agent namespace
    if not secret_name.startswith("openclaw/agent-") and not secret_name.startswith("openclaw/"):
        raise ValueError(f"Secrets must be in 'openclaw/' namespace, got: {secret_name}")

    try:
        secrets_client.create_secret(
            Name=secret_name,
            SecretString=secret_value,
            Description=description,
            Tags=[{"Key": "managed-by", "Value": "openclaw-agent"}]
        )
    except secrets_client.exceptions.ResourceExistsException:
        secrets_client.update_secret(
            SecretId=secret_name,
            SecretString=secret_value
        )

    return secret_name


# Usage pattern for agents
SECRETS = {
    "TELEGRAM_BOT_TOKEN": "openclaw/telegram/bot-token",
    "GITHUB_TOKEN": "openclaw/github/personal-token",
    "STRIPE_API_KEY": "openclaw/stripe/api-key",
    "STRIPE_WEBHOOK_SECRET": "openclaw/stripe/webhook-secret",
    "SLACK_BOT_TOKEN": "openclaw/slack/bot-token",
    "OPENAI_API_KEY": "openclaw/openai/api-key"  # For embeddings if needed
}

def get_telegram_token() -> str:
    return get_secret(SECRETS["TELEGRAM_BOT_TOKEN"])

def get_stripe_key() -> str:
    return get_secret(SECRETS["STRIPE_API_KEY"])
```

### Pattern 3: Telegram User Authorization

For single-operator deployments (the primary use case):

```python
AUTHORIZED_TELEGRAM_IDS = set(
    os.environ.get("AUTHORIZED_TELEGRAM_IDS", "").split(",")
)

def is_authorized_telegram_user(telegram_user_id: int) -> bool:
    """Check if a Telegram user is authorized to use this agent."""
    return str(telegram_user_id) in AUTHORIZED_TELEGRAM_IDS


def handle_telegram_message(event, context):
    """Main Telegram webhook handler with authorization check."""
    body = json.loads(event.get("body", "{}"))
    message = body.get("message", {})

    user_id = message.get("from", {}).get("id")
    username = message.get("from", {}).get("username", "unknown")

    # Authorization check
    if not is_authorized_telegram_user(user_id):
        # Send pairing code flow (OpenClaw-style)
        pairing_code = generate_pairing_code(user_id)
        send_telegram_message(
            chat_id=message["chat"]["id"],
            text=f"🔐 Authorization required. Your pairing code is: `{pairing_code}`\n"
                 f"Contact the agent administrator to authorize your access."
        )
        return {"statusCode": 200}

    # Authorized user — process message
    return process_authorized_message(user_id, username, message)
```

### Pattern 4: Webhook Signature Verification

```python
import hmac
import hashlib


def verify_github_webhook(payload_body: bytes, signature: str) -> bool:
    """Verify GitHub webhook signature."""
    secret = get_secret("openclaw/github/webhook-secret").encode()
    expected = "sha256=" + hmac.new(secret, payload_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_stripe_webhook(payload_body: bytes, signature: str) -> bool:
    """Verify Stripe webhook signature."""
    import stripe
    stripe.api_key = get_stripe_key()
    webhook_secret = get_secret("openclaw/stripe/webhook-secret")

    try:
        stripe.Webhook.construct_event(payload_body, signature, webhook_secret)
        return True
    except stripe.error.SignatureVerificationError:
        return False


def verify_telegram_update(update: dict) -> bool:
    """
    Verify that an update actually came from Telegram.
    For production: validate the bot token hash in the Telegram header.
    """
    # Telegram sends all updates to the registered webhook URL
    # The URL itself is secret (only Telegram knows it)
    # Additional verification: check the bot token hash
    bot_token = get_telegram_token()
    token_hash = hashlib.sha256(bot_token.encode()).hexdigest()

    # Optionally: verify custom header if configured in Telegram webhook setup
    return True  # Telegram URLs are inherently authenticated by URL secrecy
```

---

## Example Code Snippets

### Complete Auth Initialization (Run on Lambda Cold Start)

```python
import os
import boto3
from functools import lru_cache

class AuthManager:
    """Centralized authentication manager for OpenClaw agents."""

    def __init__(self):
        self._secrets_client = boto3.client("secretsmanager")
        self._identity_client = boto3.client("bedrock-agentcore-identity")
        self._cache = {}

    def get_secret(self, path: str) -> str:
        if path not in self._cache:
            self._cache[path] = self._secrets_client.get_secret_value(
                SecretId=path
            )["SecretString"]
        return self._cache[path]

    @property
    def telegram_token(self) -> str:
        return self.get_secret("openclaw/telegram/bot-token")

    @property
    def stripe_key(self) -> str:
        return self.get_secret("openclaw/stripe/api-key")

    @property
    def github_token(self) -> str:
        return self.get_secret("openclaw/github/personal-token")

    @property
    def slack_token(self) -> str:
        return self.get_secret("openclaw/slack/bot-token")

    def is_authorized_user(self, telegram_user_id: int) -> bool:
        allowed_ids = self.get_secret("openclaw/authorized-users").split(",")
        return str(telegram_user_id) in [uid.strip() for uid in allowed_ids]


# Singleton — initialized once per Lambda execution environment
auth = AuthManager()
```

### API Gateway Authorizer Lambda

```python
def token_authorizer(event, context):
    """
    Lambda authorizer for API Gateway.
    Validates JWT tokens from Cognito.
    """
    token = event.get("authorizationToken", "").replace("Bearer ", "")
    method_arn = event["methodArn"]

    try:
        # Verify JWT with Cognito
        import jose.jwt as jwt

        # Get Cognito public keys
        jwks_url = f"https://cognito-idp.{os.environ['AWS_REGION']}.amazonaws.com/{os.environ['COGNITO_USER_POOL_ID']}/.well-known/jwks.json"
        # ... JWT verification logic ...

        return generate_policy("user", "Allow", method_arn)
    except Exception as e:
        return generate_policy("user", "Deny", method_arn)


def generate_policy(principal_id: str, effect: str, resource: str) -> dict:
    return {
        "principalId": principal_id,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{
                "Action": "execute-api:Invoke",
                "Effect": effect,
                "Resource": resource
            }]
        }
    }
```

---

## Gotchas & Best Practices

### Never Hardcode Credentials
Every credential must be in Secrets Manager. No exceptions. Not environment variables, not config files, not code. Pattern:
```python
# ❌ BAD
STRIPE_KEY = "sk_live_xxxxx"

# ❌ ALSO BAD
STRIPE_KEY = os.environ["STRIPE_KEY"]  # Still in Lambda env vars (visible in console)

# ✅ GOOD
STRIPE_KEY = boto3.client("secretsmanager").get_secret_value(
    SecretId="openclaw/stripe/api-key"
)["SecretString"]
```

### IAM Roles, Not Keys
The agent should never use AWS access keys (AKID/SAK). Always use IAM roles:
- Lambda → Lambda execution role
- AgentCore Runtime → Runtime execution role
- Local development → Profile-based credentials or SSO

### Secret Rotation
Configure automatic rotation for Stripe API keys, GitHub tokens, etc. in Secrets Manager. The agent code should handle `SecretCurrentlyRotating` gracefully by retrying once after a 1-second delay.

### Authorized Users List in Secrets Manager
Store the authorized Telegram user ID list in Secrets Manager (not environment variables) so it can be updated without redeploying:
```
Secret Name: openclaw/authorized-users
Secret Value: 123456789,987654321,112233445
```

### Token Expiry and Refresh
OAuth tokens (GitHub, Slack) expire. AgentCore Identity handles refresh automatically when you use `get_token_for_resource()`. For direct Secrets Manager integration, implement token refresh logic in a scheduled Lambda.

### Multi-Tenant Separation
If extending to multi-tenant (multiple operators), use separate Cognito user pools or groups per tenant. Never share memory stores, workspace tables, or secrets namespaces between tenants.

### Telegram Bot Token Security
The Telegram bot token is the primary security boundary. Treat it like a private key:
- Store only in Secrets Manager
- Never log it
- If compromised, revoke via BotFather immediately
- Optionally use Telegram's IP allowlisting for webhook endpoints