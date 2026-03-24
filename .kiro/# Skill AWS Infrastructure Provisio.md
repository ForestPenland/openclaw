# Skill: AWS Infrastructure Provisioning

**Purpose:** Enable the agent to BUILD, DEPLOY, and MANAGE AWS infrastructure — from Lambda functions to full CDK stacks. This is a **net-new capability** that does not exist in the original OpenClaw open-source project. It is added as an extension to the forked codebase.

---

## Hybrid Approach: What This Adds to OpenClaw

The original OpenClaw has no concept of building AWS infrastructure. This skill is entirely additive — it introduces new tools that the agent can call, delivered as a new SKILL.md in the skills registry.

**How it integrates with OpenClaw:**
- This capability is delivered as a SKILL.md file (`skills/aws-infrastructure-provisioning/SKILL.md`) — the standard OpenClaw skills format
- The supervisor agent loads this skill like any other — via OpenClaw's selective skill injection system
- When infrastructure work is needed, the supervisor spawns a Builder sub-agent via OpenClaw's `sessions_spawn` tool
- The builder sub-agent runs on AgentCore Runtime (not local sessions) to support multi-hour deployments
- Results (stack outputs, Lambda ARNs, API URLs) are written back to `MEMORY.md` in S3

**Critical guardrail:** The builder capability introduces real risk. The agent can deploy real AWS resources that cost real money. Always:
1. Run CDK synth before CDK deploy — review the CloudFormation template
2. Apply IAM permission boundaries to all agent roles
3. Apply Bedrock Guardrails to all model calls that trigger builder actions
4. Set budget alerts on the AWS account before deploying

---

## AWS Services Used

- **AWS Lambda** — serverless function deployment
- **AWS CDK** (via subprocess) — full infrastructure stacks
- **AWS ECS/Fargate** — containerized service deployment
- **Amazon CloudFormation** — stack management (via CDK + direct API)
- **Amazon ECR** — container image registry
- **Amazon API Gateway** — REST and HTTP API creation
- **Amazon DynamoDB** — database provisioning
- **Amazon S3** — bucket provisioning
- **AWS IAM** — execution roles (read-only; roles pre-created)
- **Amazon CloudWatch** — monitoring and alerting
- **AWS Secrets Manager** — secrets storage for deployed services
- **AWS EventBridge** — scheduling for deployed services
- **Amazon SNS/SQS** — messaging infrastructure

---

## Key APIs and Operations

### Lambda

```python
import boto3
lambda_client = boto3.client("lambda")

# Create function
lambda_client.create_function(
    FunctionName="agent-my-function",
    Runtime="python3.12",
    Role=role_arn,
    Handler="handler.main",
    Code={"ZipFile": zip_bytes},
    Description="Deployed by OpenClaw agent",
    Timeout=30,
    MemorySize=256,
    Environment={"Variables": {"KEY": "value"}},
    Tags={"deployed-by": "openclaw-agent"}
)

# Update code
lambda_client.update_function_code(
    FunctionName="agent-my-function",
    ZipFile=new_zip_bytes
)

# Invoke
lambda_client.invoke(
    FunctionName="agent-my-function",
    InvocationType="RequestResponse",
    Payload=json.dumps({"test": "payload"})
)

# List functions
lambda_client.list_functions(FunctionVersion="ALL")

# Get function
lambda_client.get_function(FunctionName="agent-my-function")

# Delete (use sparingly — confirm first)
lambda_client.delete_function(FunctionName="agent-my-function")
```

### CDK Stack Deployment

```python
import subprocess
import tempfile
import os

def deploy_cdk_stack(stack_name: str, cdk_python_code: str, env: str = "dev") -> dict:
    """Deploy a CDK stack from agent-generated code."""
    safe_name = f"agent-{stack_name}"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write stack.py
        with open(f"{tmpdir}/stack.py", "w") as f:
            f.write(cdk_python_code)

        # Write app.py
        with open(f"{tmpdir}/app.py", "w") as f:
            class_name = extract_class_name(cdk_python_code)
            f.write(f"""
import aws_cdk as cdk
from stack import {class_name}
app = cdk.App()
{class_name}(app, "{safe_name}")
app.synth()
""")

        # Synth + deploy
        result = subprocess.run(
            ["cdk", "deploy", "--require-approval", "never", "--app", "python3 app.py"],
            cwd=tmpdir, capture_output=True, text=True
        )

        return {"success": result.returncode == 0, "output": result.stdout, "error": result.stderr}
```

### ECS/Fargate Service

```python
ecs_client = boto3.client("ecs")

# Create task definition
task_def = ecs_client.register_task_definition(
    family=f"agent-{service_name}",
    networkMode="awsvpc",
    requiresCompatibilities=["FARGATE"],
    cpu="256",
    memory="512",
    executionRoleArn=task_execution_role_arn,
    containerDefinitions=[{
        "name": service_name,
        "image": f"{ecr_uri}/{service_name}:latest",
        "portMappings": [{"containerPort": 8080, "protocol": "tcp"}],
        "environment": [{"name": k, "value": v} for k, v in env_vars.items()],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": f"/ecs/agent-{service_name}",
                "awslogs-region": region,
                "awslogs-stream-prefix": "ecs"
            }
        }
    }]
)

# Create service
ecs_client.create_service(
    cluster=cluster_arn,
    serviceName=f"agent-{service_name}",
    taskDefinition=f"agent-{service_name}",
    desiredCount=desired_count,
    launchType="FARGATE",
    networkConfiguration={
        "awsvpcConfiguration": {
            "subnets": private_subnet_ids,
            "securityGroups": [service_sg_id],
            "assignPublicIp": "DISABLED"
        }
    }
)

# Scale service
ecs_client.update_service(
    cluster=cluster_arn,
    service=f"agent-{service_name}",
    desiredCount=new_count
)
```

### API Gateway

```python
apigw = boto3.client("apigatewayv2")

# Create HTTP API backed by Lambda
api = apigw.create_api(
    Name=f"agent-{api_name}",
    ProtocolType="HTTP",
    Target=lambda_function_arn,  # Quick Lambda integration
    CorsConfiguration={
        "AllowOrigins": ["*"],
        "AllowMethods": ["GET", "POST", "OPTIONS"],
        "AllowHeaders": ["Content-Type", "Authorization"]
    }
)

api_url = api["ApiEndpoint"]  # https://xxxxx.execute-api.us-east-1.amazonaws.com/

# Add custom domain (if needed)
apigw.create_domain_name(
    DomainName=f"api.{base_domain}",
    DomainNameConfigurations=[{
        "CertificateArn": acm_cert_arn,
        "EndpointType": "REGIONAL"
    }]
)
```

### DynamoDB Table

```python
dynamodb = boto3.client("dynamodb")

dynamodb.create_table(
    TableName=f"agent-{table_name}",
    BillingMode="PAY_PER_REQUEST",
    AttributeDefinitions=[
        {"AttributeName": "pk", "AttributeType": "S"},
        {"AttributeName": "sk", "AttributeType": "S"}
    ],
    KeySchema=[
        {"AttributeName": "pk", "KeyType": "HASH"},
        {"AttributeName": "sk", "KeyType": "RANGE"}
    ],
    Tags=[{"Key": "deployed-by", "Value": "openclaw-agent"}]
)
```

---

## Implementation Patterns

### Pattern 1: Lambda + API Gateway (Most Common for Webhooks/APIs)

When an operator says "build a webhook handler" or "create an API for X":

```
1. Agent writes Python handler code
2. Agent tests the code in Code Interpreter
3. Agent calls deploy_lambda_function() → Lambda ARN
4. Agent calls create_api_gateway(lambda_arn) → API URL
5. Agent stores in memory: "Deployed {service} at {url}"
6. Agent reports URL and any secrets to operator
```

### Pattern 2: Full CDK Stack (Complex Infrastructure)

When the operator needs a complete solution (e.g., full e-commerce backend):

```
1. Agent plans the stack: Lambda + DynamoDB + SQS + API Gateway
2. Agent writes CDK Python code for the complete stack
3. Agent validates via Code Interpreter: `cdk synth`
4. Agent calls deploy_cdk_stack() with stack code
5. Agent reads outputs (URLs, ARNs, etc.)
6. Agent stores everything in memory
```

### Pattern 3: ECS Service (Long-Running Processes)

For services that need to run continuously (not Lambda-style):

```
1. Build Dockerfile and push to ECR
2. Create task definition
3. Create ECS service on Fargate
4. Optionally add Application Load Balancer
5. Set up CloudWatch alarms for the service
```

### Pattern 4: Iterative Improvement

When an existing service needs changes:

```
1. List existing agent-deployed resources
2. Get current code/config
3. Make targeted changes
4. Test in staging (agent- namespace)
5. Deploy update
6. Verify with CloudWatch logs
```

---

## Example Code Snippets

### Checking What's Currently Deployed

```python
@tool
def list_agent_deployed_resources() -> dict:
    """
    List all AWS resources deployed by the OpenClaw agent.
    Returns Lambda functions, ECS services, CDK stacks, and API Gateways.
    """
    lambda_client = boto3.client("lambda")
    ecs_client = boto3.client("ecs")
    cf_client = boto3.client("cloudformation")
    apigw_client = boto3.client("apigatewayv2")

    # Lambda functions
    lambda_response = lambda_client.list_functions()
    lambdas = [
        f["FunctionName"] for f in lambda_response["Functions"]
        if f["FunctionName"].startswith("agent-")
    ]

    # CloudFormation stacks
    cf_response = cf_client.list_stacks(
        StackStatusFilter=["CREATE_COMPLETE", "UPDATE_COMPLETE", "ROLLBACK_COMPLETE"]
    )
    stacks = [
        s["StackName"] for s in cf_response["StackSummaries"]
        if s["StackName"].startswith("agent-")
    ]

    return {
        "lambda_functions": lambdas,
        "cloudformation_stacks": stacks,
        "summary": f"{len(lambdas)} Lambda functions, {len(stacks)} CDK stacks"
    }
```

### Getting CloudWatch Logs for Deployed Service

```python
@tool
def get_recent_logs(function_or_service_name: str, minutes: int = 30) -> str:
    """
    Get recent CloudWatch logs for a Lambda function or ECS service.
    Useful for debugging issues with agent-deployed services.
    """
    logs_client = boto3.client("logs")
    log_group = f"/aws/lambda/{function_or_service_name}"
    end_time = int(time.time() * 1000)
    start_time = end_time - (minutes * 60 * 1000)

    response = logs_client.filter_log_events(
        logGroupName=log_group,
        startTime=start_time,
        endTime=end_time,
        limit=50
    )

    events = response.get("events", [])
    if not events:
        return f"No logs found for {function_or_service_name} in last {minutes} minutes"

    log_lines = [e["message"] for e in events]
    return "\n".join(log_lines[-20:])  # Last 20 lines
```

---

## Gotchas & Best Practices

### Naming Convention (CRITICAL)
**All agent-deployed resources MUST be prefixed with `agent-`**. This is enforced by IAM conditions and enables:
- Easy identification in AWS console
- Scoped IAM permissions
- Safe bulk operations (the agent can safely list/modify its own resources without touching platform resources)

### IAM Execution Roles
The agent does NOT create IAM roles. A pre-created `AgentLambdaExecutionRole` is used for all Lambda functions. This role has:
- Basic Lambda permissions (CloudWatch Logs)
- S3 read access to the artifacts bucket
- DynamoDB access to `agent-*` tables
- Secrets Manager access to `openclaw/agent-*` paths

### Cold Start Awareness
Lambda cold starts can be 1-3 seconds for Python functions. For latency-sensitive deployments, the agent should:
- Use provisioned concurrency for critical functions
- Consider ECS/Fargate for consistently high-traffic services

### Deployment Confirmation
Always confirm with the operator before deploying to production. Pattern:
```
"I'm ready to deploy the Stripe webhook handler to production.
URL will be: https://api.example.com/webhooks/stripe
Estimated cost: ~$0.02/month (Lambda invocations)
Shall I proceed?"
```

### Cost Awareness
Before deploying, estimate costs:
- Lambda: $0.20 per 1M requests + compute time
- ECS Fargate: ~$0.012/vCPU/hour + $0.0013/GB/hour
- DynamoDB: $1.25/M WCU, $0.25/M RCU (on-demand)
- API Gateway: $1/M requests

Always tag resources with `estimated-monthly-cost` for tracking.

### Error Recovery
If a CDK deployment fails:
1. Run `cdk diff` to see what would change
2. Check CloudFormation events for specific failure
3. For rollback: delete failed stack, fix code, redeploy
4. Never manually edit CloudFormation-managed resources

### Version Control
Store all deployed code in S3 before deploying:
```python
s3_client.put_object(
    Bucket="openclaw-artifacts",
    Key=f"deployments/{function_name}/{timestamp}/handler.py",
    Body=code_content
)
```
This enables rollback and audit trail.