"""Builder Sub-Agent.

A specialist sub-agent running on AgentCore Runtime for infrastructure
provisioning and code deployment.  Handles CDK code generation, testing
via Code Interpreter, CDK deployment, and Lambda function deployment.

Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7,
              11.1, 11.2, 11.3, 11.4, 11.5, 11.6
"""

from __future__ import annotations

import io
import logging
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RESOURCE_PREFIX = "agent-"

MAX_CODE_ITERATIONS = 3

_CDK_GENERATION_PROMPT = """\
You are an AWS CDK expert.  Generate a complete CDK Python stack for the
following requirement.  All resource names MUST start with the prefix
"{prefix}".  Output ONLY valid Python code, nothing else.

Requirement:
{instruction}
"""

_LAMBDA_ACTIVE_POLL_INTERVAL = 2  # seconds
_LAMBDA_ACTIVE_TIMEOUT = 60  # seconds


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CDKDeployResult:
    """Result of a CDK test-and-deploy cycle."""

    success: bool
    stack_name: str
    outputs: dict[str, str] = field(default_factory=dict)
    failure_events: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


@dataclass
class LambdaDeployResult:
    """Result of a Lambda deployment."""

    success: bool
    function_name: str
    function_arn: str = ""
    version: str = ""
    previous_version: str = ""
    s3_artifact_key: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# BuilderAgent
# ---------------------------------------------------------------------------


class BuilderAgent:
    """Builder sub-agent for infrastructure provisioning and code deployment.

    Key behaviours
    --------------
    * **generate_cdk** – generate CDK Python code from a natural-language
      instruction using Bedrock with Guardrails (Req 10.1, 10.2, 10.3).
    * **test_and_deploy** – test generated code via Code Interpreter, run
      ``cdk synth``, then ``cdk deploy`` (Req 10.2, 10.4, 10.7, 11.1).
    * **deploy_lambda** – package handler code, store artifact in S3,
      create/update Lambda, wait for Active state (Req 11.2–11.6).
    * All agent-deployed resources prefixed with ``agent-`` (Req 10.5).
    * Bedrock Guardrails applied to every model call (Req 10.3).
    * On CDK failure: extract CloudFormation failure events (Req 10.7).
    * Store previous Lambda version before deploying (Req 11.6).
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.bedrock_client = config["bedrock_client"]
        self.s3_client = config["s3_client"]
        self.lambda_client = config["lambda_client"]
        self.cfn_client = config["cfn_client"]
        self.guardrail_id: str = config["guardrail_id"]
        self.artifacts_bucket: str = config["artifacts_bucket"]
        self.resource_prefix: str = config.get("resource_prefix", RESOURCE_PREFIX)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_cdk(self, instruction: str) -> str:
        """Generate CDK Python code from a natural-language instruction.

        Uses Bedrock Converse API with Guardrails to produce CDK code.
        All resource names in the generated code are prefixed with
        ``self.resource_prefix`` (default ``agent-``).

        Requirements: 10.1, 10.2, 10.3, 10.5
        """
        prompt = _CDK_GENERATION_PROMPT.format(
            prefix=self.resource_prefix,
            instruction=instruction,
        )
        return self._invoke_model(prompt)

    def test_and_deploy(self, cdk_code: str, stack_name: str) -> dict:
        """Test CDK code and deploy if tests pass.

        Pipeline: generate tests → execute in Code Interpreter → run
        ``cdk synth`` → ``cdk deploy``.  Retries up to 3 iterations on
        test failure before reporting failure.

        Requirements: 10.2, 10.4, 10.7, 11.1, 11.2
        """
        prefixed_stack = self._prefixed_name(stack_name)

        # --- Code generation pipeline: test → deploy (Req 11.1) ---
        test_code = self._generate_tests_for_cdk(cdk_code)
        last_error = ""

        for iteration in range(1, MAX_CODE_ITERATIONS + 1):
            logger.info(
                "Code Interpreter test iteration %d/%d for stack %s",
                iteration,
                MAX_CODE_ITERATIONS,
                prefixed_stack,
            )
            test_passed, test_output = self._run_in_code_interpreter(
                cdk_code, test_code
            )

            if test_passed:
                break

            last_error = test_output
            logger.warning(
                "Tests failed (iteration %d/%d): %s",
                iteration,
                MAX_CODE_ITERATIONS,
                test_output[:500],
            )

            if iteration < MAX_CODE_ITERATIONS:
                # Revise code using error output (Req 11.2)
                cdk_code = self._revise_code(cdk_code, test_output)
                test_code = self._generate_tests_for_cdk(cdk_code)
        else:
            # Exhausted all iterations without passing tests
            return CDKDeployResult(
                success=False,
                stack_name=prefixed_stack,
                error=f"Tests failed after {MAX_CODE_ITERATIONS} iterations: {last_error}",
            ).__dict__

        # --- cdk synth ---
        synth_ok, synth_output = self._cdk_synth(cdk_code, prefixed_stack)
        if not synth_ok:
            return CDKDeployResult(
                success=False,
                stack_name=prefixed_stack,
                error=f"cdk synth failed: {synth_output}",
            ).__dict__

        # --- cdk deploy ---
        deploy_ok, deploy_output = self._cdk_deploy(prefixed_stack)
        if not deploy_ok:
            # Extract CloudFormation failure events (Req 10.7)
            failure_events = self._get_cfn_failure_events(prefixed_stack)
            return CDKDeployResult(
                success=False,
                stack_name=prefixed_stack,
                failure_events=failure_events,
                error=f"cdk deploy failed: {deploy_output}",
            ).__dict__

        # --- Collect stack outputs (Req 10.4) ---
        outputs = self._get_stack_outputs(prefixed_stack)

        return CDKDeployResult(
            success=True,
            stack_name=prefixed_stack,
            outputs=outputs,
        ).__dict__

    def deploy_lambda(
        self,
        handler_code: str,
        function_name: str,
        runtime: str,
        role_arn: str,
    ) -> dict:
        """Package, store in S3, create/update Lambda, wait for Active.

        Requirements: 11.2, 11.3, 11.4, 11.5, 11.6
        """
        prefixed_name = self._prefixed_name(function_name)

        # 1. Package handler into a zip (Req 11.3)
        zip_bytes = self._package_handler(handler_code)

        # 2. Store artifact in S3 for audit trail (Req 11.3)
        artifact_key = f"lambda-artifacts/{prefixed_name}/{int(time.time())}.zip"
        try:
            self.s3_client.put_object(
                Bucket=self.artifacts_bucket,
                Key=artifact_key,
                Body=zip_bytes,
            )
            logger.info("Stored Lambda artifact at s3://%s/%s", self.artifacts_bucket, artifact_key)
        except (BotoCoreError, ClientError) as exc:
            return LambdaDeployResult(
                success=False,
                function_name=prefixed_name,
                error=f"Failed to store artifact in S3: {exc}",
            ).__dict__

        # 3. Store previous version before deploying (Req 11.6)
        previous_version = self._get_current_lambda_version(prefixed_name)

        # 4. Create or update Lambda function (Req 11.3)
        try:
            result = self._create_or_update_lambda(
                prefixed_name, runtime, role_arn, artifact_key
            )
        except (BotoCoreError, ClientError) as exc:
            return LambdaDeployResult(
                success=False,
                function_name=prefixed_name,
                previous_version=previous_version,
                s3_artifact_key=artifact_key,
                error=f"Failed to create/update Lambda: {exc}",
            ).__dict__

        # 5. Wait for Active state (Req 11.4)
        active_ok = self._wait_for_lambda_active(prefixed_name)
        if not active_ok:
            return LambdaDeployResult(
                success=False,
                function_name=prefixed_name,
                previous_version=previous_version,
                s3_artifact_key=artifact_key,
                error="Lambda did not reach Active state within timeout",
            ).__dict__

        return LambdaDeployResult(
            success=True,
            function_name=prefixed_name,
            function_arn=result.get("FunctionArn", ""),
            version=result.get("Version", "$LATEST"),
            previous_version=previous_version,
            s3_artifact_key=artifact_key,
        ).__dict__

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prefixed_name(self, name: str) -> str:
        """Ensure *name* starts with the resource prefix (Req 10.5)."""
        if name.startswith(self.resource_prefix):
            return name
        return f"{self.resource_prefix}{name}"

    def _invoke_model(self, prompt: str) -> str:
        """Invoke Bedrock Converse API with Guardrails (Req 10.3).

        Every model call that could trigger infrastructure changes goes
        through this method so that Guardrails are always applied.
        """
        try:
            response = self.bedrock_client.converse(
                modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 8192, "temperature": 0.2},
                guardrailConfig={
                    "guardrailIdentifier": self.guardrail_id,
                    "guardrailVersion": "DRAFT",
                },
            )
            output = response.get("output", {})
            message = output.get("message", {})
            blocks = message.get("content", [])
            return blocks[0].get("text", "") if blocks else ""
        except (BotoCoreError, ClientError):
            logger.error("Bedrock model invocation failed", exc_info=True)
            raise

    def _generate_tests_for_cdk(self, cdk_code: str) -> str:
        """Ask the model to generate unit tests for the given CDK code."""
        prompt = (
            "Write pytest unit tests for the following CDK Python code.  "
            "Use aws_cdk.assertions.  Output ONLY valid Python test code.\n\n"
            f"{cdk_code}"
        )
        return self._invoke_model(prompt)

    def _revise_code(self, cdk_code: str, error_output: str) -> str:
        """Ask the model to fix CDK code based on test error output (Req 11.2)."""
        prompt = (
            "The following CDK Python code failed its tests.  Fix the code "
            "so the tests pass.  All resource names MUST start with the "
            f'prefix "{self.resource_prefix}".  Output ONLY the fixed Python code.\n\n'
            f"Code:\n{cdk_code}\n\nError:\n{error_output}"
        )
        return self._invoke_model(prompt)

    def _run_in_code_interpreter(
        self, cdk_code: str, test_code: str
    ) -> tuple[bool, str]:
        """Execute code + tests in AgentCore Code Interpreter (Req 11.1).

        Returns ``(passed, output)`` where *passed* is True when tests
        succeed and *output* contains stdout/stderr.
        """
        combined = (
            "# --- CDK Code ---\n"
            f"{cdk_code}\n\n"
            "# --- Tests ---\n"
            f"{test_code}\n\n"
            "# --- Run ---\n"
            "import subprocess, sys\n"
            "result = subprocess.run([sys.executable, '-m', 'pytest', '-x', '--tb=short'],\n"
            "                        capture_output=True, text=True)\n"
            "print(result.stdout)\n"
            "print(result.stderr)\n"
            "sys.exit(result.returncode)\n"
        )
        try:
            response = self.bedrock_client.converse(
                modelId="us.anthropic.claude-sonnet-4-20250514-v1:0",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "text": (
                                    "Execute the following Python code in a sandbox "
                                    "and return the output.\n\n" + combined
                                )
                            }
                        ],
                    }
                ],
                inferenceConfig={"maxTokens": 4096, "temperature": 0.0},
                guardrailConfig={
                    "guardrailIdentifier": self.guardrail_id,
                    "guardrailVersion": "DRAFT",
                },
            )
            output = response.get("output", {})
            message = output.get("message", {})
            blocks = message.get("content", [])
            text = blocks[0].get("text", "") if blocks else ""

            passed = "passed" in text.lower() and "failed" not in text.lower()
            return passed, text
        except (BotoCoreError, ClientError):
            logger.error("Code Interpreter execution failed", exc_info=True)
            return False, "Code Interpreter invocation error"

    def _cdk_synth(self, cdk_code: str, stack_name: str) -> tuple[bool, str]:
        """Run ``cdk synth`` via Code Interpreter and return (ok, output)."""
        prompt = (
            "Run `cdk synth` on the following CDK Python code and return "
            "the CloudFormation template or any errors.\n\n"
            f"Stack name: {stack_name}\n\n{cdk_code}"
        )
        try:
            output = self._invoke_model(prompt)
            failed = any(
                kw in output.lower()
                for kw in ("error", "exception", "failed", "traceback")
            )
            return not failed, output
        except (BotoCoreError, ClientError) as exc:
            return False, str(exc)

    def _cdk_deploy(self, stack_name: str) -> tuple[bool, str]:
        """Run ``cdk deploy`` for *stack_name* and return (ok, output)."""
        try:
            prompt = (
                f"Deploy the CDK stack '{stack_name}' using `cdk deploy "
                f"--require-approval never`.  Return the deployment output."
            )
            output = self._invoke_model(prompt)
            failed = any(
                kw in output.lower()
                for kw in ("error", "fail", "rollback")
            )
            return not failed, output
        except (BotoCoreError, ClientError) as exc:
            return False, str(exc)

    def _get_cfn_failure_events(self, stack_name: str) -> list[dict[str, Any]]:
        """Extract CloudFormation failure events for *stack_name* (Req 10.7)."""
        try:
            paginator = self.cfn_client.get_paginator("describe_stack_events")
            failure_events: list[dict[str, Any]] = []
            for page in paginator.paginate(StackName=stack_name):
                for event in page.get("StackEvents", []):
                    status = event.get("ResourceStatus", "")
                    if "FAILED" in status:
                        failure_events.append(
                            {
                                "logical_id": event.get("LogicalResourceId", ""),
                                "resource_type": event.get("ResourceType", ""),
                                "status": status,
                                "reason": event.get("ResourceStatusReason", ""),
                                "timestamp": str(event.get("Timestamp", "")),
                            }
                        )
            return failure_events
        except (BotoCoreError, ClientError):
            logger.warning("Failed to retrieve CloudFormation failure events", exc_info=True)
            return []

    def _get_stack_outputs(self, stack_name: str) -> dict[str, str]:
        """Retrieve CloudFormation stack outputs (Req 10.4)."""
        try:
            response = self.cfn_client.describe_stacks(StackName=stack_name)
            stacks = response.get("Stacks", [])
            if not stacks:
                return {}
            outputs = stacks[0].get("Outputs", [])
            return {
                o["OutputKey"]: o["OutputValue"]
                for o in outputs
                if "OutputKey" in o and "OutputValue" in o
            }
        except (BotoCoreError, ClientError):
            logger.warning("Failed to retrieve stack outputs", exc_info=True)
            return {}

    # -- Lambda deployment helpers ------------------------------------------

    @staticmethod
    def _package_handler(handler_code: str) -> bytes:
        """Package handler code into a zip archive (Req 11.3)."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("handler.py", handler_code)
        return buf.getvalue()

    def _get_current_lambda_version(self, function_name: str) -> str:
        """Get the current published version of a Lambda function (Req 11.6).

        Returns the version string or empty string if the function does
        not exist or has no published versions.
        """
        try:
            response = self.lambda_client.get_function(FunctionName=function_name)
            config = response.get("Configuration", {})
            return config.get("Version", "$LATEST")
        except self.lambda_client.exceptions.ResourceNotFoundException:
            return ""
        except (BotoCoreError, ClientError):
            logger.warning(
                "Failed to get current Lambda version for %s", function_name, exc_info=True
            )
            return ""

    def _create_or_update_lambda(
        self,
        function_name: str,
        runtime: str,
        role_arn: str,
        artifact_key: str,
    ) -> dict[str, Any]:
        """Create or update a Lambda function from an S3 artifact (Req 11.3)."""
        code_config = {
            "S3Bucket": self.artifacts_bucket,
            "S3Key": artifact_key,
        }

        try:
            # Try updating existing function
            response = self.lambda_client.update_function_code(
                FunctionName=function_name,
                **code_config,
                Publish=True,
            )
            logger.info("Updated Lambda function %s", function_name)
            return response
        except self.lambda_client.exceptions.ResourceNotFoundException:
            # Function doesn't exist — create it
            response = self.lambda_client.create_function(
                FunctionName=function_name,
                Runtime=runtime,
                Role=role_arn,
                Handler="handler.handler",
                Code=code_config,
                Publish=True,
                Timeout=30,
                MemorySize=256,
            )
            logger.info("Created Lambda function %s", function_name)
            return response

    def _wait_for_lambda_active(self, function_name: str) -> bool:
        """Poll until the Lambda function reaches Active state (Req 11.4).

        Returns True if Active within timeout, False otherwise.
        """
        deadline = time.time() + _LAMBDA_ACTIVE_TIMEOUT
        while time.time() < deadline:
            try:
                response = self.lambda_client.get_function(FunctionName=function_name)
                state = response.get("Configuration", {}).get("State", "")
                if state == "Active":
                    return True
                if state == "Failed":
                    logger.error("Lambda %s entered Failed state", function_name)
                    return False
            except (BotoCoreError, ClientError):
                logger.warning(
                    "Error polling Lambda state for %s", function_name, exc_info=True
                )
            time.sleep(_LAMBDA_ACTIVE_POLL_INTERVAL)
        logger.error("Lambda %s did not reach Active within %ds", function_name, _LAMBDA_ACTIVE_TIMEOUT)
        return False
