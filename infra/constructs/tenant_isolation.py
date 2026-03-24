"""CDK Tenant Isolation Construct — per-tenant IAM boundaries and S3 prefix scoping.

Requirements: 18.1, 18.2, 18.3
"""

from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from constructs import Construct


class TenantIsolation(Construct):
    """Creates per-tenant IAM policies that restrict S3 access to the tenant's
    key prefix, scope Secrets Manager access to ``openclaw/{tenantId}/``, and
    track a separate AgentCore Memory store per tenant.

    Properties:
        tenant_id: The tenant identifier this construct isolates.
        policy: The IAM managed policy enforcing tenant boundaries.
        memory_store_id: The AgentCore Memory store ID assigned to this tenant.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        tenant_id: str,
        workspace_bucket: s3.IBucket,
        memory_store_id: str,
    ) -> None:
        super().__init__(scope, construct_id)

        self._tenant_id = tenant_id
        self._memory_store_id = memory_store_id

        # --- Per-tenant IAM Policy ---
        self._policy = iam.ManagedPolicy(
            self,
            "TenantPolicy",
            managed_policy_name=f"openclaw-tenant-{tenant_id}",
            statements=[
                # S3: restrict to tenant's key prefix (Req 18.1)
                iam.PolicyStatement(
                    sid="AllowS3TenantPrefix",
                    effect=iam.Effect.ALLOW,
                    actions=["s3:GetObject", "s3:PutObject"],
                    resources=[
                        f"{workspace_bucket.bucket_arn}/{tenant_id}/*",
                    ],
                ),
                iam.PolicyStatement(
                    sid="AllowS3ListTenantPrefix",
                    effect=iam.Effect.ALLOW,
                    actions=["s3:ListBucket"],
                    resources=[workspace_bucket.bucket_arn],
                    conditions={
                        "StringLike": {
                            "s3:prefix": [f"{tenant_id}/*"],
                        },
                    },
                ),
                # Secrets Manager: scope to openclaw/{tenantId}/ namespace (Req 18.3)
                iam.PolicyStatement(
                    sid="AllowSecretsManagerTenantNamespace",
                    effect=iam.Effect.ALLOW,
                    actions=["secretsmanager:GetSecretValue"],
                    resources=[
                        f"arn:aws:secretsmanager:*:*:secret:openclaw/{tenant_id}/*",
                    ],
                ),
            ],
        )

    @property
    def tenant_id(self) -> str:
        """The tenant identifier."""
        return self._tenant_id

    @property
    def policy(self) -> iam.ManagedPolicy:
        """The IAM managed policy enforcing tenant isolation boundaries."""
        return self._policy

    @property
    def memory_store_id(self) -> str:
        """The AgentCore Memory store ID for this tenant (Req 18.2)."""
        return self._memory_store_id
