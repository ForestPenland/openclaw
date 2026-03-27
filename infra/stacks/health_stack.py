"""CDK Health Stack — CloudWatch alarms and ECS crash detection for OpenClaw.

Creates production-essential monitoring: task-down alarm, CPU/memory alarms,
ECS task-stopped EventBridge rule, and SNS notification topic.

This stack was derived from the agent's self-created rockclaw-health-monitoring
CloudFormation stack, generalized for any OpenClaw deployment.
"""

from aws_cdk import (
    Duration,
    Stack,
)
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_cloudwatch_actions as cw_actions
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_sns as sns
from constructs import Construct


class HealthStack(Stack):
    """Creates CloudWatch alarms and ECS crash detection for the OpenClaw Gateway.

    Accepts the ECS cluster and service names to monitor.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cluster_name: str,
        service_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --- SNS Topic for health alerts ---
        self.alerts_topic = sns.Topic(
            self,
            "HealthAlertsTopic",
            topic_name="openclaw-health-alerts",
        )

        # --- CloudWatch Alarm: Task Down ---
        # Fires when no ECS tasks are running (gateway is down)
        self.task_down_alarm = cloudwatch.Alarm(
            self,
            "TaskDownAlarm",
            alarm_name="openclaw-task-down",
            alarm_description="OpenClaw Gateway has no running tasks",
            metric=cloudwatch.Metric(
                namespace="AWS/ECS",
                metric_name="RunningTaskCount",
                dimensions_map={
                    "ClusterName": cluster_name,
                    "ServiceName": service_name,
                },
                statistic="Minimum",
                period=Duration.minutes(1),
            ),
            threshold=1,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            evaluation_periods=2,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
        )
        self.task_down_alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        # --- CloudWatch Alarm: CPU High ---
        self.cpu_alarm = cloudwatch.Alarm(
            self,
            "CpuHighAlarm",
            alarm_name="openclaw-cpu-high",
            alarm_description="OpenClaw Gateway CPU utilization above 85%",
            metric=cloudwatch.Metric(
                namespace="AWS/ECS",
                metric_name="CPUUtilization",
                dimensions_map={
                    "ClusterName": cluster_name,
                    "ServiceName": service_name,
                },
                statistic="Average",
                period=Duration.minutes(5),
            ),
            threshold=85,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=3,
        )
        self.cpu_alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        # --- CloudWatch Alarm: Memory High ---
        self.memory_alarm = cloudwatch.Alarm(
            self,
            "MemoryHighAlarm",
            alarm_name="openclaw-memory-high",
            alarm_description="OpenClaw Gateway memory utilization above 90%",
            metric=cloudwatch.Metric(
                namespace="AWS/ECS",
                metric_name="MemoryUtilization",
                dimensions_map={
                    "ClusterName": cluster_name,
                    "ServiceName": service_name,
                },
                statistic="Average",
                period=Duration.minutes(5),
            ),
            threshold=90,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=3,
        )
        self.memory_alarm.add_alarm_action(cw_actions.SnsAction(self.alerts_topic))

        # --- EventBridge Rule: ECS Task Stopped ---
        # Fires when any ECS task stops in the OpenClaw cluster
        # (crash, OOM, manual stop, deployment rotation)
        self.task_stopped_rule = events.Rule(
            self,
            "TaskStoppedRule",
            rule_name="openclaw-ecs-task-stopped",
            description="Fires when any OpenClaw ECS task stops",
            event_pattern=events.EventPattern(
                source=["aws.ecs"],
                detail_type=["ECS Task State Change"],
                detail={
                    "clusterArn": [{"suffix": cluster_name}],
                    "lastStatus": ["STOPPED"],
                },
            ),
        )
        self.task_stopped_rule.add_target(targets.SnsTopic(self.alerts_topic))
