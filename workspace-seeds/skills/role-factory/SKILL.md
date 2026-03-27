---
name: role-factory
description: "Create and manage task-scoped IAM roles for elevated AWS permissions. Use when a task needs permissions beyond the base ECS task role, creating resources that require specific IAM policies, or assuming temporary elevated access for a deployment."
metadata:
  openclaw:
    emoji: "🔐"
    requires:
      bins: ["aws"]
---

# Role Factory Skill

Create task-scoped IAM roles with elevated permissions when the base ECS
task role is insufficient. All roles are constrained by the
`agent-permission-boundary` managed policy.

## Core Rule: One Role Per Project, Never Shared

Every agent, every build, every project gets its own dedicated IAM role.
Do NOT reuse an existing agent's execution role for a new agent.

## Naming Conventions

All agent-managed roles MUST use the `agent-task-` prefix:

| Role type | Pattern | Example |
|-----------|---------|---------|
| Temporary task role | `agent-task-<purpose>-<timestamp>` | `agent-task-cognito-1774546167` |
| Agent execution role | `agent-task-<agent-name>-exec` | `agent-task-memory-agent-exec` |

## Constraints

- All roles MUST be prefixed with `agent-task-`
- All roles MUST have `agent-permission-boundary` attached
- Maximum 5 concurrent active roles
- Roles expire after 24 hours (cleanup Lambda deletes them)
- Permission boundary enforces no cross-account role assumption

## Pattern A: Temporary Task Role

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
ROLE_NAME="agent-task-<purpose>-$(date +%s)"

# Create role with permission boundary
cat > /tmp/trust-policy.json << EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"AWS":"$CALLER_ARN"},"Action":"sts:AssumeRole"}]}
EOF

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --permissions-boundary arn:aws:iam::${ACCOUNT}:policy/agent-permission-boundary \
  --tags Key=Purpose,Value="<description>" Key=CreatedBy,Value=agent

# Attach scoped inline policy
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name task-policy \
  --policy-document file:///tmp/task-policy.json

# Assume role
aws sts assume-role \
  --role-arn "arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}" \
  --role-session-name "task-session" --duration-seconds 3600 > /tmp/creds.json

export AWS_ACCESS_KEY_ID=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['Credentials']['AccessKeyId'])")
export AWS_SECRET_ACCESS_KEY=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['Credentials']['SecretAccessKey'])")
export AWS_SESSION_TOKEN=$(python3 -c "import json; print(json.load(open('/tmp/creds.json'))['Credentials']['SessionToken'])")

# Do elevated work, then clean up
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name task-policy
aws iam delete-role --role-name "$ROLE_NAME"
```

## Pattern B: Agent Execution Role (Long-Lived)

For AgentCore Runtime agents. Trust policy allows the AgentCore service:

```bash
ROLE_NAME="agent-task-${AGENT_NAME}-exec"

cat > /tmp/exec-trust-policy.json << 'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF

aws iam create-role \
  --role-name "$ROLE_NAME" \
  --assume-role-policy-document file:///tmp/exec-trust-policy.json \
  --permissions-boundary arn:aws:iam::${ACCOUNT}:policy/agent-permission-boundary \
  --tags Key=Purpose,Value="${AGENT_NAME}-runtime" Key=CreatedBy,Value=agent
```

Tag execution roles with `Purpose=*-runtime` to exempt them from the
24-hour cleanup Lambda.
