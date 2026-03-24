# Skill: Autonomous Scheduling

**Purpose:** How the AWS-enhanced OpenClaw agent operates autonomously without human prompting — extending OpenClaw's native heartbeat, cron, and webhook system to run reliably in a cloud-hosted environment via EventBridge Scheduler and API Gateway.

---

## Hybrid Approach: OpenClaw Scheduling vs. AWS

OpenClaw already has a complete autonomous scheduling system. This skill describes how to migrate it to AWS without breaking its behavior.

| Mechanism | OpenClaw Native | AWS Extension | Behavior Change |
|-----------|----------------|---------------|-----------------|
| Heartbeat | Node process, 30-min cycle reads HEARTBEAT.md | EventBridge Scheduler rule → Lambda → Gateway trigger | None — HEARTBEAT.md format and logic unchanged |
| Cron jobs | `~/.openclaw/cron/` files, Node-internal scheduler | EventBridge Scheduler rules, state in DynamoDB | None — same cron syntax, cloud execution |
| Webhooks | `localhost:18789/webhook/<path>` | API Gateway HTTPS + Lambda → SQS → Gateway | URL changes from localhost to public HTTPS |
| Webhook auth | None (relies on network isolation) | Lambda authorizer for signature verification | Adds security; no behavior change for trusted callers |

**What doesn't change:**
- `HEARTBEAT.md` format — still a markdown checklist the agent reads and acts on
- HEARTBEAT_OK behavior — Gateway still silently drops it
- Cron syntax — EventBridge Scheduler uses standard `cron(minute hour day month weekday year)` format
- Webhook payload format — same JSON body, just different URL

**Critical:** In a cloud deploy with multiple ECS Gateway instances, using Node-internal cron would cause duplicate heartbeats. EventBridge Scheduler fires exactly once regardless of instance count.

---

## AWS Services Used

- **Amazon EventBridge Scheduler** — heartbeat and cron scheduling
- **Amazon EventBridge** — event routing and rules
- **AWS Lambda** — scheduled task execution and webhook handling
- **Amazon SQS** — async task queuing for long-running scheduled work
- **Amazon DynamoDB** — task state tracking and deduplication
- **Amazon CloudWatch** — monitoring scheduled task health

---

## Key APIs and Operations

### EventBridge Scheduler (CDK Configuration)

```python
from aws_cdk import (
    Stack, Duration, aws_events as events,
    aws_events_targets as targets,
    aws_scheduler as scheduler,
    aws_iam as iam
)
from constructs import Construct


class SchedulingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str,
                 gateway_router_lambda, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # IAM role for EventBridge Scheduler to invoke Lambda
        scheduler_role = iam.Role(
            self, "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        gateway_router_lambda.grant_invoke(scheduler_role)

        # 1. HEARTBEAT — every 30 minutes (matches OpenClaw default)
        scheduler.CfnSchedule(
            self, "HeartbeatSchedule",
            schedule_expression="rate(30 minutes)",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=gateway_router_lambda.function_arn,
                role_arn=scheduler_role.role_arn,
                input=json.dumps({
                    "source": "heartbeat",
                    "agent_id": "supervisor",
                    "task": "Run HEARTBEAT checklist"
                })
            ),
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"  # Exact timing
            ),
            name="openclaw-heartbeat"
        )

        # 2. DAILY BRIEFING — 7 AM ET (12 UTC)
        scheduler.CfnSchedule(
            self, "DailyBriefingSchedule",
            schedule_expression="cron(0 12 * * ? *)",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=gateway_router_lambda.function_arn,
                role_arn=scheduler_role.role_arn,
                input=json.dumps({
                    "source": "scheduled",
                    "agent_id": "supervisor",
                    "task": "Generate your daily briefing: deployments status, revenue from Stripe, pending customer issues, upcoming tasks, anything notable from the past 24 hours."
                })
            ),
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            name="openclaw-daily-briefing"
        )

        # 3. NIGHTLY MEMORY CONSOLIDATION — 2 AM UTC
        scheduler.CfnSchedule(
            self, "MemoryConsolidationSchedule",
            schedule_expression="cron(0 2 * * ? *)",
            target=scheduler.CfnSchedule.TargetProperty(
                arn=gateway_router_lambda.function_arn,
                role_arn=scheduler_role.role_arn,
                input=json.dumps({
                    "source": "scheduled",
                    "agent_id": "supervisor",
                    "task": "MEMORY_CONSOLIDATION: Run the nightly memory consolidation process."
                })
            ),
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(
                mode="OFF"
            ),
            name="openclaw-memory-consolidation"
        )
```

### Dynamic Schedule Creation (Agent-Controlled)

The agent can create new schedules programmatically:

```python
import boto3
import json
import os
from datetime import datetime, timezone

scheduler_client = boto3.client("scheduler")


def create_schedule(
    schedule_name: str,
    cron_expression: str,
    task_description: str,
    agent_id: str = "supervisor"
) -> dict:
    """
    Create a new scheduled task. The agent can create its own schedules.

    Args:
        schedule_name: Unique name for this schedule
        cron_expression: Standard cron (e.g., '0 9 * * MON-FRI' for weekday 9am)
        task_description: What the agent should do when this fires
        agent_id: Which agent to route to

    Returns:
        Dict with schedule ARN
    """
    safe_name = f"openclaw-{schedule_name.replace(' ', '-').lower()}"

    response = scheduler_client.create_schedule(
        Name=safe_name,
        GroupName="openclaw-agent-schedules",
        ScheduleExpression=f"cron({cron_expression})",
        ScheduleExpressionTimezone="UTC",
        Target={
            "Arn": os.environ["GATEWAY_LAMBDA_ARN"],
            "RoleArn": os.environ["SCHEDULER_ROLE_ARN"],
            "Input": json.dumps({
                "source": "scheduled",
                "agent_id": agent_id,
                "task": task_description,
                "schedule_name": safe_name
            })
        },
        FlexibleTimeWindow={"Mode": "OFF"}
    )

    return {
        "schedule_arn": response["ScheduleArn"],
        "schedule_name": safe_name,
        "expression": cron_expression,
        "task": task_description
    }


def create_one_time_schedule(
    task_description: str,
    fire_at: datetime,
    agent_id: str = "supervisor"
) -> dict:
    """
    Create a one-time scheduled task.

    Args:
        task_description: What to do when it fires
        fire_at: UTC datetime when it should fire
        agent_id: Which agent to route to
    """
    import uuid
    schedule_name = f"openclaw-onetime-{uuid.uuid4().hex[:8]}"

    # EventBridge at() expression
    at_expression = f"at({fire_at.strftime('%Y-%m-%dT%H:%M:%S')})"

    response = scheduler_client.create_schedule(
        Name=schedule_name,
        ScheduleExpression=at_expression,
        Target={
            "Arn": os.environ["GATEWAY_LAMBDA_ARN"],
            "RoleArn": os.environ["SCHEDULER_ROLE_ARN"],
            "Input": json.dumps({
                "source": "scheduled",
                "agent_id": agent_id,
                "task": task_description,
                "one_time": True,
                "schedule_name": schedule_name
            })
        },
        FlexibleTimeWindow={"Mode": "OFF"},
        # Auto-delete after firing
        ActionAfterCompletion="DELETE"
    )

    return {
        "schedule_arn": response["ScheduleArn"],
        "fires_at": fire_at.isoformat(),
        "task": task_description
    }


def list_active_schedules() -> list:
    """List all active OpenClaw agent schedules."""
    response = scheduler_client.list_schedules(
        GroupName="openclaw-agent-schedules"
    )

    schedules = []
    for s in response.get("Schedules", []):
        schedule_detail = scheduler_client.get_schedule(
            Name=s["Name"],
            GroupName="openclaw-agent-schedules"
        )
        target_input = json.loads(schedule_detail["Target"]["Input"])
        schedules.append({
            "name": s["Name"],
            "expression": schedule_detail["ScheduleExpression"],
            "task": target_input.get("task", ""),
            "state": s["State"]
        })

    return schedules


def delete_schedule(schedule_name: str):
    """Delete a schedule by name."""
    scheduler_client.delete_schedule(
        Name=schedule_name,
        GroupName="openclaw-agent-schedules"
    )
```

---

## Implementation Patterns

### Pattern 1: The Heartbeat Handler

The heartbeat is the agent's "always-on awareness" — runs every 30 minutes:

```python
def handle_heartbeat(event: dict, context):
    """
    Process a heartbeat invocation.
    The agent checks its HEARTBEAT.md checklist and acts if needed.
    """
    agent_id = event.get("agent_id", "supervisor")

    # Load HEARTBEAT.md from DynamoDB
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table("openclaw-workspace")
    heartbeat_item = table.get_item(Key={"agent_id": agent_id, "file_type": "HEARTBEAT"})
    heartbeat_md = heartbeat_item.get("Item", {}).get("content", "No heartbeat checklist configured.")

    # Construct the heartbeat prompt
    heartbeat_prompt = f"""You are running your scheduled 30-minute heartbeat check.

HEARTBEAT CHECKLIST:
{heartbeat_md}

For each item in the checklist:
1. Check if action is needed
2. If yes: take action or notify operator via Telegram
3. If all clear: respond only with HEARTBEAT_OK

Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}

Important: If nothing requires attention, respond ONLY with HEARTBEAT_OK.
Do not send any Telegram messages for routine HEARTBEAT_OK responses."""

    # Invoke supervisor agent
    agentcore = boto3.client("bedrock-agentcore-runtime")
    session_id = f"heartbeat-{int(time.time())}"

    response_stream = agentcore.invoke_agent_runtime(
        agentRuntimeArn=os.environ["SUPERVISOR_AGENT_ARN"],
        payload=json.dumps({"message": heartbeat_prompt, "type": "heartbeat"}),
        sessionId=session_id
    )

    full_response = collect_stream_response(response_stream)

    # Log heartbeat result
    import boto3
    logs = boto3.client("logs")
    logs.put_log_events(
        logGroupName="/openclaw/heartbeat",
        logStreamName=datetime.now().strftime("%Y/%m/%d"),
        logEvents=[{
            "timestamp": int(time.time() * 1000),
            "message": json.dumps({
                "heartbeat_ok": "HEARTBEAT_OK" in full_response,
                "response_preview": full_response[:200],
                "agent_id": agent_id
            })
        }]
    )

    return {"statusCode": 200, "heartbeat_ok": "HEARTBEAT_OK" in full_response}
```

### Pattern 2: Operator-Defined Schedules via Chat

```python
@tool
def set_reminder(what: str, when: str) -> str:
    """
    Set a one-time reminder or recurring task from natural language.

    Args:
        what: What to remind about or do (natural language)
        when: When to do it (e.g., 'tomorrow at 9am', 'every Monday at 8am', 'in 2 hours')

    Returns:
        Confirmation with schedule details
    """
    from dateutil import parser
    from datetime import datetime, timezone, timedelta
    import re

    # Parse relative times
    now = datetime.now(timezone.utc)

    if "in " in when.lower():
        # e.g., "in 2 hours", "in 30 minutes"
        match = re.search(r"in (\d+) (minute|hour|day)", when.lower())
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit == "minute":
                fire_at = now + timedelta(minutes=amount)
            elif unit == "hour":
                fire_at = now + timedelta(hours=amount)
            elif unit == "day":
                fire_at = now + timedelta(days=amount)

            result = create_one_time_schedule(
                task_description=f"REMINDER: {what}",
                fire_at=fire_at
            )
            return f"✅ Reminder set for {fire_at.strftime('%Y-%m-%d %H:%M UTC')}: {what}"

    elif "every" in when.lower():
        # Recurring schedule — convert to cron
        cron = natural_language_to_cron(when)
        if cron:
            result = create_schedule(
                schedule_name=f"reminder-{what[:20].replace(' ', '-')}",
                cron_expression=cron,
                task_description=f"RECURRING TASK: {what}"
            )
            return f"✅ Recurring schedule set ({when}): {what}"

    # Fallback: try to parse as absolute datetime
    try:
        fire_at = parser.parse(when, settings={"PREFER_FUTURE": True})
        fire_at = fire_at.replace(tzinfo=timezone.utc)
        result = create_one_time_schedule(
            task_description=f"REMINDER: {what}",
            fire_at=fire_at
        )
        return f"✅ Reminder set for {fire_at.strftime('%Y-%m-%d %H:%M UTC')}: {what}"
    except Exception:
        return f"❌ Couldn't parse time '{when}'. Try 'in 2 hours', 'tomorrow at 9am', or 'every Monday at 8am'"


def natural_language_to_cron(when: str) -> str:
    """Convert simple natural language schedule to cron expression."""
    when_lower = when.lower()

    patterns = {
        r"every day at (\d+)(am|pm)": lambda h, ap: f"0 {to_24h(int(h), ap)} * * *",
        r"every monday at (\d+)(am|pm)": lambda h, ap: f"0 {to_24h(int(h), ap)} * * 1",
        r"every weekday at (\d+)(am|pm)": lambda h, ap: f"0 {to_24h(int(h), ap)} * * 1-5",
        r"every hour": lambda: "0 * * * *",
        r"every 30 minutes": lambda: "*/30 * * * *",
    }

    for pattern, cron_fn in patterns.items():
        import re
        match = re.search(pattern, when_lower)
        if match:
            return cron_fn(*match.groups())

    return None


def to_24h(hour: int, ampm: str) -> int:
    if ampm == "pm" and hour != 12:
        return hour + 12
    if ampm == "am" and hour == 12:
        return 0
    return hour
```

### Pattern 3: Webhook-Triggered Autonomous Actions

```python
def handle_github_webhook_autonomously(event: dict) -> dict:
    """
    When a GitHub webhook fires, route it to the agent for autonomous handling.
    Examples: PR opened → agent reviews; push to main → agent checks for issues.
    """
    payload = json.loads(event.get("body", "{}"))
    event_type = event.get("headers", {}).get("X-GitHub-Event", "")

    if event_type == "push" and payload.get("ref") == "refs/heads/main":
        # Code pushed to main — check for issues
        task = (
            f"Code was just pushed to main branch in repository "
            f"'{payload['repository']['full_name']}'. "
            f"The push included these commits: "
            f"{[c['message'] for c in payload.get('commits', [])][:5]}. "
            f"Check if there are any obvious issues and monitor CloudWatch for errors."
        )
        return route_to_supervisor(task, "github-webhook", "webhook")

    elif event_type == "pull_request" and payload.get("action") == "opened":
        # PR opened — optionally review
        pr = payload["pull_request"]
        task = (
            f"A new pull request was opened in '{payload['repository']['full_name']}': "
            f"'{pr['title']}'. The author is {pr['user']['login']}. "
            f"PR URL: {pr['html_url']}. "
            f"Should I review this PR? If yes, I'll read it and add a review comment."
        )
        notify_operator(task)

    elif event_type == "issues" and payload.get("action") == "opened":
        # Issue opened — log and potentially triage
        issue = payload["issue"]
        task = (
            f"A new GitHub issue was opened: '{issue['title']}' "
            f"by {issue['user']['login']}. "
            f"URL: {issue['html_url']}. "
            f"Please triage and add appropriate labels."
        )
        return route_to_supervisor(task, "github-webhook", "webhook")

    return {"statusCode": 200}
```

### Pattern 4: Task Queue for Long-Running Scheduled Work

```python
def queue_scheduled_task(task: dict) -> str:
    """
    For long-running scheduled tasks (e.g., building monthly report),
    queue them to avoid Lambda timeout.
    """
    sqs = boto3.client("sqs")
    import uuid

    task_id = uuid.uuid4().hex
    task["task_id"] = task_id

    sqs.send_message(
        QueueUrl=os.environ["TASK_QUEUE_URL"],
        MessageBody=json.dumps(task),
        MessageDeduplicationId=task_id,
        MessageGroupId=task.get("agent_id", "supervisor")
    )

    return task_id


# Lambda triggered by SQS (long-running task worker)
def process_scheduled_task_queue(event, context):
    """Process scheduled tasks from SQS queue."""
    for record in event["Records"]:
        task_data = json.loads(record["body"])

        try:
            response = route_to_supervisor(
                message=task_data["task"],
                user_id="scheduled-task",
                channel="scheduled"
            )

            # Notify operator of completion if configured
            if task_data.get("notify_on_completion"):
                notify_operator(
                    f"✅ Scheduled task complete: {task_data.get('task', '')[:100]}\n\n"
                    f"Result: {response[:500]}"
                )

        except Exception as e:
            notify_operator(
                f"❌ Scheduled task failed: {task_data.get('task', '')[:100]}\n"
                f"Error: {str(e)}",
                urgent=True
            )
```

---

## Example Code Snippets

### HEARTBEAT.md Template

```markdown
# Agent Heartbeat Checklist

Run this checklist every 30 minutes. For each item, take action if needed.
If all clear, respond with HEARTBEAT_OK only.

## Customer Support
- [ ] Check for unread Slack messages in #support channel
- [ ] Check for new customer emails (filter: unread from last 30 min)
- [ ] Check for open support tickets older than 2 hours

## Infrastructure Health
- [ ] Check for Lambda errors in CloudWatch (last 30 min)
- [ ] Check for any ECS service unhealthy tasks
- [ ] Check for API Gateway 5xx errors (threshold: >1%)

## Business Metrics
- [ ] Check Stripe for any failed payments or disputes (urgent: disputes)
- [ ] Check for any subscription cancellations

## GitHub
- [ ] Check for any critical CI failures on main branch
- [ ] Check for any high-priority issues opened

## Communication
- [ ] Check if any response has been pending >1 hour

## Action Thresholds
- Customer message unanswered >1 hour → ALERT operator via Telegram
- Lambda error rate >5% → ALERT operator, investigate
- Stripe dispute opened → URGENT ALERT operator
- CI failure on main → NOTIFY operator (not urgent)
```

### CloudWatch Alarm for Missed Heartbeats

```python
from aws_cdk import aws_cloudwatch as cloudwatch

# Alert if heartbeat didn't fire in 35 minutes
heartbeat_alarm = cloudwatch.Alarm(
    self, "MissedHeartbeatAlarm",
    alarm_name="openclaw-missed-heartbeat",
    metric=cloudwatch.Metric(
        namespace="OpenClaw",
        metric_name="HeartbeatExecutions",
        statistic="Sum",
        period=Duration.minutes(35)
    ),
    threshold=0,
    comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,
    evaluation_periods=1,
    alarm_description="OpenClaw heartbeat has not fired in 35 minutes"
)
```

---

## Gotchas & Best Practices

### Heartbeat Duration vs Lambda Timeout
The heartbeat agent may need to run multiple tool checks (CloudWatch, GitHub, Stripe). Set Lambda timeout to at least 5 minutes. For complex heartbeats, use SQS to queue the check and process it async.

### Idempotent Scheduled Tasks
EventBridge may occasionally deliver a scheduled event twice. Make scheduled task handlers idempotent by checking a deduplication key in DynamoDB before processing.

### HEARTBEAT_OK Signal
The heartbeat response `HEARTBEAT_OK` should be detected and silently dropped — never forwarded to the operator. This is a core OpenClaw pattern. Only send operator notifications when action is actually needed.

### Timezone Handling
All cron expressions in EventBridge Scheduler are in UTC. Always specify `ScheduleExpressionTimezone` when creating schedules that should fire at local times. For operators in US Eastern, add 5 hours to UTC.

### Schedule Groups for Organization
Use EventBridge Scheduler Groups to organize schedules: `openclaw-system-schedules` for platform schedules (heartbeat, consolidation) and `openclaw-agent-schedules` for agent-created schedules. This makes it easy to list, pause, or delete agent-created schedules without affecting system schedules.

### Cost Awareness for Scheduled Tasks
EventBridge Scheduler costs $1/million scheduled invocations. A 30-minute heartbeat = 48 invocations/day = ~1,440/month = $0.00144/month. Very cheap, but be careful about agents creating thousands of schedules.