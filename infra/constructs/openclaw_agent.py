"""CDK OpenClawAgent Construct — reusable per-agent ECS task, workspace prefix, and memory store.

Requirements: 16.2
"""

from aws_cdk import Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct


class OpenClawAgent(Construct):
    """Encapsulates one agent's ECS Fargate task, workspace bucket prefix,
    and memory store configuration.

    Instantiate once per agent. Each instance creates its own Fargate task
    definition, container, and ECS service.

    Properties:
        service: The ECS Fargate service running this agent.
        task_definition: The Fargate task definition for this agent.
        workspace_prefix: The S3 key prefix ``{tenantId}/{agentId}/``.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        agent_id: str,
        tenant_id: str,
        workspace_bucket: s3.IBucket,
        memory_store_id: str,
        cluster: ecs.ICluster,
        vpc: ec2.IVpc,
        log_group: logs.ILogGroup,
    ) -> None:
        super().__init__(scope, construct_id)

        self._workspace_prefix = f"{tenant_id}/{agent_id}/"

        # --- Task Definition (512 CPU, 1024 MiB) ---
        self._task_definition = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=512,
            memory_limit_mib=1024,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.X86_64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # --- Task Role Permissions ---
        task_role = self._task_definition.task_role

        workspace_bucket.grant_read_write(task_role)
        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[workspace_bucket.bucket_arn],
            )
        )

        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )

        task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=["bedrock-agent-runtime:*"],
                resources=["*"],
            )
        )

        # --- Container Definition ---
        self._container = self._task_definition.add_container(
            "AgentContainer",
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/docker/library/node:22-slim"
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix=f"openclaw-agent-{agent_id}",
                log_group=log_group,
            ),
            environment={
                "AGENT_ID": agent_id,
                "TENANT_ID": tenant_id,
                "WORKSPACE_BUCKET": workspace_bucket.bucket_name,
                "MEMORY_STORE_ID": memory_store_id,
                "WORKSPACE_PREFIX": self._workspace_prefix,
            },
            command=[
                "sh",
                "-c",
                "aws s3 sync s3://$WORKSPACE_BUCKET/$WORKSPACE_PREFIX /workspace/ && node /app/gateway.js",
            ],
            essential=True,
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:18789/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        self._container.add_port_mappings(
            ecs.PortMapping(container_port=18789, protocol=ecs.Protocol.TCP)
        )

        # --- ECS Service (desired count 1) ---
        self._service = ecs.FargateService(
            self,
            "Service",
            cluster=cluster,
            task_definition=self._task_definition,
            desired_count=1,
            assign_public_ip=True,
            enable_execute_command=True,
        )

    @property
    def service(self) -> ecs.FargateService:
        """The ECS Fargate service running this agent."""
        return self._service

    @property
    def task_definition(self) -> ecs.FargateTaskDefinition:
        """The Fargate task definition for this agent."""
        return self._task_definition

    @property
    def workspace_prefix(self) -> str:
        """The S3 key prefix: ``{tenantId}/{agentId}/``."""
        return self._workspace_prefix
