"""CDK Scheduler Stack — EventBridge rules and Lambda functions for OpenClaw scheduling.

Creates:
- Heartbeat Lambda (30-minute rate schedule)
- Memory Consolidation Lambda (nightly cron: 0 2 * * ? * UTC)
- EventBridge Scheduler schedule group for agent-created schedules
- SNS topic for heartbeat/consolidation alerts
- IAM role for EventBridge Scheduler to invoke Lambda targets

Requirements: 7.1, 7.5
"""

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as _lambda
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_sns as sns
from constructs import Construct


class SchedulerStack(Stack):
    """Creates EventBridge schedules and Lambda functions for heartbeat and consolidation.

    Accepts references from the storage stack (workspace bucket, memory table)
    so the Lambda functions can be granted appropriate permissions.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        workspace_bucket: s3.IBucket,
        memory_table: dynamodb.ITable,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SNS Topic for heartbeat/consolidation alerts ---
        self.alerts_topic = sns.Topic(
            self,
            "SchedulerAlertsTopic",
            topic_name="openclaw-scheduler-alerts",
        )

        # --- Heartbeat Lambda ---
        self.heartbeat_lambda = _lambda.Function(
            self,
            "HeartbeatHandler",
            function_name="openclaw-heartbeat-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="heartbeat_handler.handler",
            code=_lambda.Code.from_asset("../lambdas"),
            timeout=Duration.seconds(90),
            memory_size=256,
            environment={
                "WORKSPACE_BUCKET": workspace_bucket.bucket_name,
                "SNS_TOPIC_ARN": self.alerts_topic.topic_arn,
            },
        )

        # Grant heartbeat Lambda permissions
        workspace_bucket.grant_read(self.heartbeat_lambda)
        self.alerts_topic.grant_publish(self.heartbeat_lambda)
        # Allow heartbeat Lambda to invoke the supervisor (other Lambdas)
        self.heartbeat_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=["*"],
            )
        )

        # --- Memory Consolidation Lambda ---
        self.consolidation_lambda = _lambda.Function(
            self,
            "ConsolidationHandler",
            function_name="openclaw-consolidation-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="memory_consolidation.handler",
            code=_lambda.Code.from_asset("../lambdas"),
            timeout=Duration.seconds(300),
            memory_size=512,
            environment={
                "WORKSPACE_BUCKET": workspace_bucket.bucket_name,
                "MEMORY_TABLE_NAME": memory_table.table_name,
                "SNS_TOPIC_ARN": self.alerts_topic.topic_arn,
            },
        )

        # Grant consolidation Lambda permissions
        workspace_bucket.grant_read_write(self.consolidation_lambda)
        memory_table.grant_read_write_data(self.consolidation_lambda)
        self.alerts_topic.grant_publish(self.consolidation_lambda)
        # Bedrock for Claude Haiku invocation
        self.consolidation_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock-agent-runtime:*",
                ],
                resources=["*"],
            )
        )

        # --- EventBridge Rule: Heartbeat every 30 minutes (Req 7.1) ---
        self.heartbeat_rule = events.Rule(
            self,
            "HeartbeatRule",
            rule_name="openclaw-heartbeat-30min",
            schedule=events.Schedule.rate(Duration.minutes(30)),
        )
        self.heartbeat_rule.add_target(
            targets.LambdaFunction(self.heartbeat_lambda)
        )

        # --- EventBridge Rule: Nightly consolidation at 02:00 UTC (Req 7.5) ---
        self.consolidation_rule = events.Rule(
            self,
            "ConsolidationRule",
            rule_name="openclaw-nightly-consolidation",
            schedule=events.Schedule.cron(
                minute="0",
                hour="2",
                month="*",
                week_day="*",
                year="*",
            ),
        )
        self.consolidation_rule.add_target(
            targets.LambdaFunction(self.consolidation_lambda)
        )

        # --- EventBridge Scheduler schedule group for agent-created schedules ---
        self.agent_schedule_group = scheduler.CfnScheduleGroup(
            self,
            "AgentScheduleGroup",
            name="openclaw-agent-schedules",
        )

        # --- IAM Role for EventBridge Scheduler to invoke Lambda targets ---
        self.scheduler_role = iam.Role(
            self,
            "SchedulerInvokeRole",
            role_name="openclaw-scheduler-invoke-role",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        self.scheduler_role.add_to_policy(
            iam.PolicyStatement(
                actions=["lambda:InvokeFunction"],
                resources=[
                    self.heartbeat_lambda.function_arn,
                    self.consolidation_lambda.function_arn,
                ],
            )
        )
