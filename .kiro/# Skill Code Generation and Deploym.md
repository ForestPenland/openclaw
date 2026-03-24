# Skill: Code Generation and Deployment

**Purpose:** How the AWS-enhanced OpenClaw agent writes, tests, and deploys code — from Lambda handlers to full microservices. This is a **net-new capability** added on top of the OpenClaw foundation, combining AgentCore Code Interpreter for safe sandboxed testing with AWS deployment APIs for production delivery.

---

## Hybrid Approach: What This Adds to OpenClaw

The original OpenClaw can write and run code via tools like `bash` and `computer_use`, but has no structured pipeline for testing code in a sandbox before deploying it to production. This skill adds that pipeline.

**How it integrates with OpenClaw:**
- Delivered as a SKILL.md file in the skills registry — no changes to OpenClaw core required
- The agent invokes this skill when asked to "write a webhook handler", "build a new feature", or "fix the Lambda function"
- Code Interpreter provides an isolated sandbox where code is written and tested before any real deployment happens
- Deployment is gated on Code Interpreter tests passing — never deploy without testing

**The code generation pipeline:**
```
User request: "Build a Stripe webhook handler"
        │
        ▼
Agent writes handler code + unit tests
        │
        ▼
Code Interpreter: run unit tests in sandbox (no real AWS calls)
        │
        ├── Tests fail → agent revises code → re-test (up to 3 iterations)
        └── Tests pass →
                │
                ▼
        Package code (zip for Lambda / Dockerfile for ECS)
                │
                ▼
        Deploy to AWS (Lambda update / ECS service update)
                │
                ▼
        Write deployment outputs to MEMORY.md
```

**What the original OpenClaw can already do:** Write code, read files, execute bash commands. The agent could deploy code using bash today. This skill adds the **structured safety layer** — test before deploy, sandbox isolation, rollback on failure.

---

## AWS Services Used

- **Amazon Bedrock AgentCore Code Interpreter** — sandboxed Python/JS/TS execution for testing
- **Amazon Bedrock** (Claude) — code generation via natural language
- **AWS Lambda** — deploying function code
- **Amazon ECR** — storing container images for ECS services
- **Amazon ECS/Fargate** — deploying containerized services
- **Amazon S3** — storing deployment artifacts
- **AWS CodeBuild** — (optional) CI/CD pipeline integration
- **GitHub** (via AgentCore Gateway) — source control operations

---

## Key APIs and Operations

### AgentCore Code Interpreter

```python
import boto3

code_interpreter = boto3.client("bedrock-agentcore-code-interpreter",
                                 region_name="us-east-1")


def execute_python(code: str, session_id: str = None, timeout: int = 30) -> dict:
    """
    Execute Python code in AgentCore's sandboxed Code Interpreter.
    Returns stdout, stderr, files generated, and execution status.
    """
    import uuid
    session_id = session_id or uuid.uuid4().hex

    response = code_interpreter.create_code_interpreter_session(
        sessionId=session_id
    )

    result = code_interpreter.execute_code(
        sessionId=session_id,
        code=code,
        language="python",
        timeoutInSeconds=timeout
    )

    return {
        "success": result.get("executionStatus") == "success",
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "output_files": result.get("outputFiles", []),
        "error": result.get("error")
    }


def execute_aws_cli(command: str, session_id: str = None) -> dict:
    """
    Execute an AWS CLI command in the Code Interpreter sandbox.
    The sandbox has IAM credentials with limited AWS access.
    """
    python_code = f"""
import subprocess
import json

result = subprocess.run(
    {repr(command.split())},
    capture_output=True,
    text=True
)
print("STDOUT:", result.stdout)
print("STDERR:", result.stderr)
print("EXIT CODE:", result.returncode)
"""
    return execute_python(python_code, session_id)
```

### Code Generation Pattern

```python
from strands import Agent, tool
from strands.models.bedrock import BedrockModel


@tool
def generate_and_test_code(
    description: str,
    language: str = "python",
    code_type: str = "lambda_handler"
) -> dict:
    """
    Generate code from a description, test it in Code Interpreter, and return
    the tested, production-ready code.

    Args:
        description: Natural language description of what the code should do
        language: 'python', 'javascript', or 'typescript'
        code_type: 'lambda_handler', 'fastapi_app', 'utility_script', 'cdk_stack'

    Returns:
        Dict with 'code', 'tests', 'test_results', 'ready_for_deployment'
    """
    model = BedrockModel(model_id="anthropic.claude-sonnet-4-6-20251015-v1:0")

    # Generate the code
    code_agent = Agent(model=model)
    code_prompt = f"""Write production-ready {language} code for: {description}

Requirements:
- Type: {code_type}
- Include error handling
- Include logging
- Include input validation
- Write clean, readable code with docstrings
- {get_code_type_requirements(code_type)}

Return ONLY the code, no explanation. Use markdown code fences."""

    generated_code = str(code_agent(code_prompt))
    code_content = extract_code_from_markdown(generated_code)

    # Generate tests
    test_prompt = f"""Write comprehensive tests for this {language} code:

{code_content}

Requirements:
- Cover happy path and error cases
- Test edge cases
- Use pytest (for Python) or Jest (for JavaScript)
- Mock external dependencies
Return ONLY the test code."""

    generated_tests = str(code_agent(test_prompt))
    test_content = extract_code_from_markdown(generated_tests)

    # Run tests in Code Interpreter
    test_runner_code = f"""
{code_content}

# --- Tests ---
{test_content}

# Run tests
import sys
import io

# Capture test output
old_stdout = sys.stdout
sys.stdout = io.StringIO()

try:
    # Simple assertion-based test execution
    exec('''{test_content.replace("'", "\\'")}''')
    output = sys.stdout.getvalue()
    sys.stdout = old_stdout
    print("TESTS PASSED")
    print(output)
except AssertionError as e:
    sys.stdout = old_stdout
    print(f"TEST FAILED: {{e}}")
except Exception as e:
    sys.stdout = old_stdout
    print(f"TEST ERROR: {{e}}")
"""

    test_result = execute_python(test_runner_code)

    return {
        "code": code_content,
        "tests": test_content,
        "test_passed": test_result["success"] and "TESTS PASSED" in test_result.get("stdout", ""),
        "test_output": test_result.get("stdout", ""),
        "test_errors": test_result.get("stderr", ""),
        "ready_for_deployment": test_result["success"]
    }
```

### Lambda Function Deployment

```python
import zipfile
import io
import boto3


def package_lambda_code(handler_code: str, requirements: list = None) -> bytes:
    """
    Package Lambda code into a deployment zip.
    Optionally include Python dependencies.
    """
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Main handler
        zf.writestr("handler.py", handler_code)

        # Requirements file (for documentation)
        if requirements:
            zf.writestr("requirements.txt", "\n".join(requirements))

    zip_buffer.seek(0)
    return zip_buffer.read()


def deploy_lambda_from_code(
    function_name: str,
    code: str,
    description: str = "",
    environment_variables: dict = None,
    timeout: int = 30,
    memory_mb: int = 256,
    runtime: str = "python3.12"
) -> dict:
    """
    Complete Lambda deployment from code string.
    Returns function ARN and invocation URL if API Gateway created.
    """
    lambda_client = boto3.client("lambda")
    safe_name = f"agent-{function_name.lstrip('agent-')}"

    # Package the code
    zip_bytes = package_lambda_code(code)

    # Store artifact in S3 first (for audit trail)
    s3_client = boto3.client("s3")
    artifact_key = f"lambda-artifacts/{safe_name}/{int(time.time())}/handler.zip"
    s3_client.put_object(
        Bucket=os.environ["ARTIFACTS_BUCKET"],
        Key=artifact_key,
        Body=zip_bytes
    )

    role_arn = os.environ["LAMBDA_EXECUTION_ROLE_ARN"]

    try:
        # Try update first
        lambda_client.update_function_code(
            FunctionName=safe_name,
            ZipFile=zip_bytes
        )
        if environment_variables:
            lambda_client.update_function_configuration(
                FunctionName=safe_name,
                Environment={"Variables": environment_variables}
            )
        action = "updated"
        func_info = lambda_client.get_function(FunctionName=safe_name)
        func_arn = func_info["Configuration"]["FunctionArn"]

    except lambda_client.exceptions.ResourceNotFoundException:
        # Create new function
        response = lambda_client.create_function(
            FunctionName=safe_name,
            Runtime=runtime,
            Role=role_arn,
            Handler="handler.main",
            Code={"ZipFile": zip_bytes},
            Description=description or f"Deployed by OpenClaw agent",
            Timeout=timeout,
            MemorySize=memory_mb,
            Environment={"Variables": environment_variables or {}},
            Tags={
                "deployed-by": "openclaw-agent",
                "artifact-s3-key": artifact_key
            }
        )
        func_arn = response["FunctionArn"]
        action = "created"

    # Wait for Lambda to be active
    waiter = lambda_client.get_waiter("function_active")
    waiter.wait(FunctionName=safe_name)

    return {
        "success": True,
        "action": action,
        "function_name": safe_name,
        "function_arn": func_arn,
        "artifact_stored_at": f"s3://{os.environ['ARTIFACTS_BUCKET']}/{artifact_key}"
    }
```

### Container Image Build and Push (For ECS Services)

```python
def build_and_push_docker(
    service_name: str,
    code: str,
    dockerfile: str = None,
    requirements: list = None
) -> str:
    """
    Build a Docker image and push to ECR.
    Returns the ECR image URI.
    Runs via Code Interpreter (which has Docker access in the sandbox).
    """
    if dockerfile is None:
        dockerfile = generate_dockerfile(code, requirements)

    # Use Code Interpreter to build and push
    build_code = f"""
import subprocess
import boto3
import json

# Get ECR details
ecr_client = boto3.client('ecr', region_name='{os.environ["AWS_DEFAULT_REGION"]}')
account_id = boto3.client('sts').get_caller_identity()['Account']
region = '{os.environ["AWS_DEFAULT_REGION"]}'
image_name = 'agent-{service_name}'
ecr_uri = f'{{account_id}}.dkr.ecr.{{region}}.amazonaws.com/{{image_name}}'

# Ensure ECR repository exists
try:
    ecr_client.create_repository(repositoryName=image_name)
    print(f'Created ECR repo: {{image_name}}')
except ecr_client.exceptions.RepositoryAlreadyExistsException:
    print(f'ECR repo exists: {{image_name}}')

# Write Dockerfile
with open('/tmp/Dockerfile', 'w') as f:
    f.write({repr(dockerfile)})

# Write app code
with open('/tmp/app.py', 'w') as f:
    f.write({repr(code)})

# Authenticate Docker to ECR
auth_cmd = f'aws ecr get-login-password --region {{region}} | docker login --username AWS --password-stdin {{account_id}}.dkr.ecr.{{region}}.amazonaws.com'
subprocess.run(auth_cmd, shell=True, check=True)

# Build
subprocess.run(['docker', 'build', '-t', image_name, '/tmp/'], check=True)
subprocess.run(['docker', 'tag', image_name, f'{{ecr_uri}}:latest'], check=True)

# Push
subprocess.run(['docker', 'push', f'{{ecr_uri}}:latest'], check=True)

print(f'SUCCESS: {{ecr_uri}}:latest')
"""

    result = execute_python(build_code)
    if result["success"]:
        # Extract ECR URI from output
        for line in result["stdout"].split("\n"):
            if "SUCCESS:" in line:
                return line.split("SUCCESS: ")[1].strip()

    raise RuntimeError(f"Docker build failed: {result['stderr']}")
```

---

## Implementation Patterns

### Pattern 1: Complete Feature Build Pipeline

```python
def build_feature_end_to_end(feature_request: str) -> dict:
    """
    Full pipeline: understand → generate → test → deploy → verify
    """
    results = {}

    # 1. Generate code and tests
    code_result = generate_and_test_code(
        description=feature_request,
        language="python",
        code_type="lambda_handler"
    )

    if not code_result["test_passed"]:
        # Retry with error context
        code_result = generate_and_test_code(
            description=f"{feature_request}\n\nPrevious attempt had test failures: {code_result['test_errors']}",
            language="python",
            code_type="lambda_handler"
        )

    results["code"] = code_result

    # 2. Deploy Lambda
    function_name = generate_function_name_from_description(feature_request)
    deploy_result = deploy_lambda_from_code(
        function_name=function_name,
        code=code_result["code"],
        description=feature_request
    )
    results["deployment"] = deploy_result

    # 3. Verify deployment with a test invocation
    verification = verify_lambda_deployment(deploy_result["function_name"])
    results["verification"] = verification

    return results
```

### Pattern 2: Iterative Code Improvement

```python
def improve_existing_code(function_name: str, issue_description: str) -> dict:
    """
    Retrieve existing Lambda code, improve it, and redeploy.
    """
    lambda_client = boto3.client("lambda")

    # Get existing code
    response = lambda_client.get_function(FunctionName=function_name)
    download_url = response["Code"]["Location"]

    # Download zip and extract code
    import requests
    import zipfile
    import io

    zip_response = requests.get(download_url)
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as zf:
        code = zf.read("handler.py").decode("utf-8")

    # Generate improvement
    model = BedrockModel(model_id="anthropic.claude-sonnet-4-6-20251015-v1:0")
    agent = Agent(model=model)

    improved = str(agent(
        f"Improve this Lambda function to fix: {issue_description}\n\n"
        f"Current code:\n```python\n{code}\n```\n\n"
        f"Return only the improved code."
    ))
    new_code = extract_code_from_markdown(improved)

    # Test and deploy
    test_result = execute_python(new_code)
    if test_result["success"]:
        return deploy_lambda_from_code(function_name, new_code)
    else:
        return {"error": "Improved code has errors", "details": test_result}
```

---

## Example Code Snippets

### Lambda Handler Templates

```python
# Stripe webhook handler template
STRIPE_WEBHOOK_TEMPLATE = '''
import json
import os
import boto3
import stripe
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

stripe.api_key = boto3.client("secretsmanager").get_secret_value(
    SecretId="openclaw/stripe/api-key"
)["SecretString"]

WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def main(event, context):
    """Handle Stripe webhook events."""
    try:
        body = event.get("body", "{}")
        signature = event.get("headers", {}).get("Stripe-Signature", "")

        # Verify webhook signature
        stripe_event = stripe.Webhook.construct_event(
            body, signature, WEBHOOK_SECRET
        )

        event_type = stripe_event["type"]
        logger.info(f"Processing Stripe event: {event_type}")

        # Handle different event types
        if event_type == "payment_intent.succeeded":
            handle_payment_succeeded(stripe_event["data"]["object"])
        elif event_type == "customer.subscription.deleted":
            handle_subscription_cancelled(stripe_event["data"]["object"])

        return {"statusCode": 200, "body": json.dumps({"received": True})}

    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid Stripe signature: {e}")
        return {"statusCode": 400, "body": "Invalid signature"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        return {"statusCode": 500, "body": "Internal error"}


def handle_payment_succeeded(payment_intent):
    """Store successful payment in DynamoDB."""
    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(os.environ["ORDERS_TABLE"])

    table.put_item(Item={
        "pk": f"ORDER#{payment_intent['id']}",
        "sk": "PAYMENT",
        "amount": payment_intent["amount"],
        "currency": payment_intent["currency"],
        "status": "paid",
        "created_at": payment_intent["created"]
    })


def handle_subscription_cancelled(subscription):
    """Handle subscription cancellation."""
    logger.info(f"Subscription cancelled: {subscription['id']}")
    # Add business logic here
'''
```

### Dockerfile Generator

```python
def generate_dockerfile(code: str, requirements: list = None) -> str:
    """Generate an appropriate Dockerfile for the code."""
    req_str = "\n".join(requirements) if requirements else ""

    return f"""FROM public.ecr.aws/lambda/python:3.12

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt --no-cache-dir

# Copy application code
COPY app.py .

# Set the handler
CMD ["app.main"]
"""
```

---

## Gotchas & Best Practices

### Always Test Before Deploying
Use the Code Interpreter for every code generation before deploying to AWS. A 5-second sandbox test saves a 10-minute debug session in Lambda.

### Lambda Cold Starts
Python Lambda functions have cold starts of 200ms-2s depending on dependencies. For latency-sensitive functions:
- Keep code lean (minimal imports)
- Use Lambda SnapStart (Java) or consider ECS for consistently warm services
- Avoid loading large models or heavy SDKs at initialization

### Environment Variable Management
Code the Lambda to read secrets from Secrets Manager at invocation time, not hardcode them:
```python
# ❌ BAD — hardcoded in environment
STRIPE_KEY = os.environ["STRIPE_SECRET_KEY"]

# ✅ GOOD — lazy loading from Secrets Manager
_stripe_key = None
def get_stripe_key():
    global _stripe_key
    if not _stripe_key:
        _stripe_key = boto3.client("secretsmanager").get_secret_value(
            SecretId="openclaw/stripe/api-key"
        )["SecretString"]
    return _stripe_key
```

### Code Interpreter Has Limited Packages
The Code Interpreter sandbox has common packages pre-installed (boto3, numpy, pandas, requests) but not all packages. For testing code that depends on specific libraries, note in the test what packages would be needed in the actual Lambda deployment.

### Lambda Size Limits
Lambda deployment package: 50MB zipped, 250MB unzipped. For larger code (including ML models or heavy dependencies), use:
- Lambda Layers for shared dependencies
- Container images (up to 10GB) for heavy ML code
- ECS Fargate for very large services

### Rollback Strategy
Always store the previous version before deploying:
```python
# Before deploying: store current version to S3
# After deploying: keep Lambda's previous version via aliases
lambda_client.publish_version(FunctionName=function_name)
lambda_client.update_alias(
    FunctionName=function_name,
    Name="stable",
    FunctionVersion="$LATEST"
)
# On rollback: update alias back to previous published version
```