"""CDK Observability Construct — CloudWatch dashboard and alarms for OpenClaw.

Creates:
- CloudWatch Dashboard with widgets for messages/hour, Bedrock token usage,
  memory ops count, active sessions, error rate, builder deployment count
- Alarm: agent unresponsive >5 minutes → SNS notification
- Alarm: Bedrock error rate >5% over 5-minute window → SNS notification
- Alarm: builder deployment failure → SNS notification
- Alarm: heartbeat not fired within 35 minutes → SNS notification

Requirements: 15.1, 15.2, 15.3, 15.4, 15.5
"""

from aws_cdk import Duration
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_sns as sns
from constructs import Construct

METRIC_NAMESPACE = "OpenClaw"


class Observability(Construct):
    """CloudWatch dashboard and alarms for OpenClaw production monitoring.

    Accepts an optional SNS topic for alarm notifications. If none is
    provided, a new topic is created.

    Properties:
        alarm_topic: The SNS topic that receives alarm notifications.
        dashboard: The CloudWatch dashboard.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        alarm_topic: sns.ITopic | None = None,
    ) -> None:
        super().__init__(scope, construct_id)

        # --- SNS Topic ---
        self._alarm_topic = alarm_topic or sns.Topic(
            self,
            "AlarmTopic",
            topic_name="openclaw-alarms",
        )
        sns_action = cw_actions.SnsAction(self._alarm_topic)

        # --- Custom Metrics ---
        messages_per_hour = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="MessagesReceived",
            statistic="Sum",
            period=Duration.hours(1),
        )

        bedrock_input_tokens = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="InputTokens",
            statistic="Sum",
            period=Duration.hours(1),
        )

        bedrock_output_tokens = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="OutputTokens",
            statistic="Sum",
            period=Duration.hours(1),
        )

        memory_ops = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="MemoryOperations",
            statistic="Sum",
            period=Duration.hours(1),
        )

        active_sessions = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="ActiveSessions",
            statistic="Maximum",
            period=Duration.minutes(5),
        )

        error_count = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="Errors",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        request_count = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="Requests",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        builder_deployments = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="BuilderDeployments",
            statistic="Sum",
            period=Duration.hours(1),
        )

        heartbeat_fired = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="HeartbeatFired",
            statistic="Sum",
            period=Duration.minutes(35),
        )

        agent_heartbeat = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="AgentHeartbeat",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        bedrock_errors = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="BedrockErrors",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        bedrock_requests = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="BedrockRequests",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        builder_failures = cw.Metric(
            namespace=METRIC_NAMESPACE,
            metric_name="BuilderDeploymentFailures",
            statistic="Sum",
            period=Duration.minutes(5),
        )

        # --- Error Rate (metric math) ---
        error_rate_expr = cw.MathExpression(
            expression="IF(requests > 0, (errors / requests) * 100, 0)",
            using_metrics={
                "errors": error_count,
                "requests": request_count,
            },
            label="Error Rate (%)",
            period=Duration.minutes(5),
        )

        bedrock_error_rate_expr = cw.MathExpression(
            expression="IF(total > 0, (errors / total) * 100, 0)",
            using_metrics={
                "errors": bedrock_errors,
                "total": bedrock_requests,
            },
            label="Bedrock Error Rate (%)",
            period=Duration.minutes(5),
        )

        # --- Dashboard (Req 15.1) ---
        self._dashboard = cw.Dashboard(
            self,
            "Dashboard",
            dashboard_name="OpenClaw",
        )

        self._dashboard.add_widgets(
            cw.GraphWidget(
                title="Messages / Hour",
                left=[messages_per_hour],
                width=8,
            ),
            cw.GraphWidget(
                title="Bedrock Token Usage",
                left=[bedrock_input_tokens, bedrock_output_tokens],
                width=8,
            ),
            cw.GraphWidget(
                title="Memory Operations / Hour",
                left=[memory_ops],
                width=8,
            ),
        )

        self._dashboard.add_widgets(
            cw.GraphWidget(
                title="Active Sessions",
                left=[active_sessions],
                width=8,
            ),
            cw.GraphWidget(
                title="Error Rate (%)",
                left=[error_rate_expr],
                width=8,
            ),
            cw.GraphWidget(
                title="Builder Deployments / Hour",
                left=[builder_deployments],
                width=8,
            ),
        )

        # --- Alarm: Agent unresponsive >5 minutes (Req 15.2) ---
        self._agent_unresponsive_alarm = cw.Alarm(
            self,
            "AgentUnresponsiveAlarm",
            alarm_name="openclaw-agent-unresponsive",
            alarm_description="Agent has not sent a heartbeat in over 5 minutes",
            metric=agent_heartbeat,
            threshold=0,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.BREACHING,
        )
        self._agent_unresponsive_alarm.add_alarm_action(sns_action)

        # --- Alarm: Bedrock error rate >5% over 5 min (Req 15.3) ---
        self._bedrock_error_alarm = cw.Alarm(
            self,
            "BedrockErrorRateAlarm",
            alarm_name="openclaw-bedrock-error-rate",
            alarm_description="Bedrock error rate exceeds 5% over 5-minute window",
            metric=bedrock_error_rate_expr,
            threshold=5,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self._bedrock_error_alarm.add_alarm_action(sns_action)

        # --- Alarm: Builder deployment failure (Req 15.4) ---
        self._builder_failure_alarm = cw.Alarm(
            self,
            "BuilderDeploymentFailureAlarm",
            alarm_name="openclaw-builder-deployment-failure",
            alarm_description="Builder deployment failure detected — check stack name and failure reason in logs",
            metric=builder_failures,
            threshold=0,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        self._builder_failure_alarm.add_alarm_action(sns_action)

        # --- Alarm: Heartbeat not fired within 35 minutes (Req 15.5) ---
        self._missed_heartbeat_alarm = cw.Alarm(
            self,
            "MissedHeartbeatAlarm",
            alarm_name="openclaw-missed-heartbeat",
            alarm_description="Heartbeat has not fired within 35 minutes",
            metric=heartbeat_fired,
            threshold=0,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cw.TreatMissingData.BREACHING,
        )
        self._missed_heartbeat_alarm.add_alarm_action(sns_action)

    @property
    def alarm_topic(self) -> sns.ITopic:
        """The SNS topic that receives alarm notifications."""
        return self._alarm_topic

    @property
    def dashboard(self) -> cw.Dashboard:
        """The CloudWatch dashboard."""
        return self._dashboard

    @property
    def agent_unresponsive_alarm(self) -> cw.Alarm:
        """Alarm: agent unresponsive >5 minutes."""
        return self._agent_unresponsive_alarm

    @property
    def bedrock_error_alarm(self) -> cw.Alarm:
        """Alarm: Bedrock error rate >5% over 5-minute window."""
        return self._bedrock_error_alarm

    @property
    def builder_failure_alarm(self) -> cw.Alarm:
        """Alarm: builder deployment failure."""
        return self._builder_failure_alarm

    @property
    def missed_heartbeat_alarm(self) -> cw.Alarm:
        """Alarm: heartbeat not fired within 35 minutes."""
        return self._missed_heartbeat_alarm
