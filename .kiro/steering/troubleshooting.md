---
inclusion: manual
---

# Troubleshooting Guide

Real issues encountered during development and deployment of the OpenClaw AWS Extension, organized by category.

## Docker Build Issues

### node:22-slim Missing AWS CLI
**Symptom:** S3 workspace sync fails at startup — `aws: command not found`.
**Cause:** Slim Node.js images don't include curl, unzip, or AWS CLI.
**Fix:** Use `node:24-bookworm` (full image) and install AWS CLI explicitly in the Dockerfile. Don't use `-slim` or `-alpine` variants.

### No App Code in Container
**Symptom:** Container starts but crashes with `exec: openclaw.mjs: not found`.
**Cause:** Single-stage Dockerfile that doesn't build the OpenClaw application.
**Fix:** Use multi-stage build. Stage 1: `pnpm install` + `pnpm build:docker`. Stage 2: copy built artifacts from Stage 1. Ensure `COPY --from=runtime-assets` includes `dist/`, `node_modules/`, `openclaw.mjs`, `extensions/`, `scripts/`, etc.

### pnpm --frozen-lockfile Fails
**Symptom:** Docker build fails at `pnpm install` with lockfile mismatch errors.
**Cause:** Upstream merges modify `package.json` without updating `pnpm-lock.yaml` exactly.
**Fix:** Use `--no-frozen-lockfile` in the Dockerfile.

### pnpm prune CI/TTY Catch-22
**Symptom:** `pnpm prune --prod` either prompts for confirmation (no TTY in Docker) or fails because `CI=true` enables frozen lockfile.
**Cause:** `CI=true` sets `--frozen-lockfile` by default, which conflicts with the modified lockfile from `--no-frozen-lockfile` install.
**Fix:** `CI=true pnpm prune --prod --config.frozen-lockfile=false`

## CDK Deployment Issues

### ENAMETOOLONG During Asset Staging
**Symptom:** `cdk deploy` fails with `ENAMETOOLONG` error.
**Cause:** CDK copies the entire project root as a Docker asset, including `cdk.out/` which contains nested Docker image layers.
**Fix:** Exclude `infra/` (and other large dirs) from the Docker asset in `gateway_stack.py`:
```python
exclude=["infra", ".git", ".kiro", "node_modules", ".pnpm-store", ...]
```

### `constructs` Module Not Found
**Symptom:** `cdk synth` fails with `Cannot find module 'constructs'`.
**Cause:** The local `infra/constructs/` directory shadows the pip `constructs` package.
**Fix:** `infra/app.py` has a `sys.path` workaround that temporarily removes the current directory from `sys.path` before importing `aws_cdk`. Ensure you're running from the `infra/` directory with the venv activated.

### Orphaned CloudWatch Log Groups
**Symptom:** Redeployment fails with `ResourceAlreadyExistsException` for a CloudWatch log group.
**Cause:** A previous failed deployment created a log group with a hardcoded name. CDK tries to create the same name.
**Fix:** Don't hardcode `log_group_name` in CDK. Let CloudWatch auto-name it. If already stuck, manually delete the orphaned log group in the CloudWatch console, then redeploy.

## ECS / Fargate Issues

### Gateway Refuses Non-Loopback Binding
**Symptom:** Gateway starts but health checks fail. Connections from outside `127.0.0.1` are rejected.
**Cause:** OpenClaw's control UI security requires explicit opt-in for non-loopback binding.
**Fix:** Include in `openclaw.json`:
```json
{ "gateway": { "controlUi": { "dangerouslyAllowHostHeaderOriginFallback": true } } }
```
And start with `--bind lan`.

### ECS Task Keeps Restarting
**Symptom:** Task starts, runs for a few seconds, then restarts in a loop.
**Cause:** Usually one of: missing `WORKSPACE_BUCKET` env var, empty S3 bucket (no workspace files), or Bedrock model access not enabled.
**Fix:** Check CloudWatch logs for the specific error. Common fixes:
- Run `seed-workspace.py` to populate S3
- Verify `WORKSPACE_BUCKET` is set correctly in `gateway_stack.py`
- Enable model access in Bedrock console

### Health Check Failing
**Symptom:** ECS reports task as unhealthy, keeps restarting.
**Cause:** Health check hits `http://127.0.0.1:18789/healthz` — if the Gateway hasn't started yet or crashed, it fails.
**Fix:** The task definition has a 60-second `start_period` grace period. If the Gateway takes longer to start (large workspace sync), increase `start_period` in `gateway_stack.py`.

## OpenClaw Config Issues

### dmPolicy "allow" Invalid
**Symptom:** Gateway fails to start with a validation error about `dmPolicy`.
**Cause:** OpenClaw expects `"open"`, not `"allow"`, for the Telegram DM policy.
**Fix:** Use `dmPolicy: "open"` with `allowFrom: ["*"]` in the Telegram channel config.

### Telegram Signature Verification Failing
**Symptom:** Custom webhook Lambda receives Telegram updates but can't verify the signature.
**Cause:** Trying to implement Telegram webhook verification manually instead of using OpenClaw's native handler.
**Fix:** Don't use a custom webhook Lambda for Telegram. OpenClaw handles Telegram natively — just provide the bot token in `openclaw.json`. The Gateway registers its own webhook and handles verification internally.

## Bedrock / IAM Issues

### Model Access Denied
**Symptom:** `AccessDeniedException: You don't have access to the model with the specified model ID.`
**Cause:** Either the model requires marketplace access (third-party models like Claude) or the model ID is wrong.
**Fix:**
- **Amazon models** (Nova Lite, Nova Pro): Should work out of the box. Verify model ID and region.
- **Third-party models** (Anthropic Claude): Go to Bedrock console → Model access → Request access. Accept the EULA. The task role already has `aws-marketplace:*` permissions.

### Bedrock Throttling
**Symptom:** Intermittent `ThrottlingException` from Bedrock API.
**Cause:** Exceeding the default provisioned throughput for the model.
**Fix:** The Bedrock adapter has built-in retry with exponential backoff (3 retries). For sustained high throughput, request a quota increase in the AWS console or use provisioned throughput.

## Debugging Commands

```bash
# ECS exec into running container
CLUSTER=$(aws ecs list-clusters --profile openclaw-dev --query 'clusterArns[0]' --output text)
TASK=$(aws ecs list-tasks --cluster $CLUSTER --profile openclaw-dev --query 'taskArns[0]' --output text)
aws ecs execute-command --cluster $CLUSTER --task $TASK --container GatewayContainer --interactive --command "/bin/bash" --profile openclaw-dev

# Inside container — check config
cat ~/.openclaw/openclaw.json
ls -la /home/node/.openclaw/workspace/
env | grep -E 'WORKSPACE|PROVIDER|BEDROCK|TELEGRAM'

# Force restart ECS task
SERVICE=$(aws ecs list-services --cluster $CLUSTER --profile openclaw-dev --query 'serviceArns[0]' --output text)
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment --profile openclaw-dev
```
