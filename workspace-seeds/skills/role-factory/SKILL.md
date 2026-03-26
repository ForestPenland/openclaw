---
name: role-factory
description: "Create and manage task-scoped IAM roles for elevated AWS permissions. Use when: (1) a task needs permissions beyond the base ECS task role, (2) creating resources that require specific IAM policies, (3) assuming temporary elevated access for a deployment. NOT for: read-only AWS queries (base role is sufficient), operations within the base permission set."
metadata:
  {
    "openclaw":
      {
        "emoji": "🔐",
        "requires": { "bins": ["aws"] },
      },
  }
---

# Role Factory Skill

Create task-scoped IAM roles with elevated permissions when the base ECS task role is insufficient. All roles are constrained by the `agent-permission-boundary` managed policy.

## When to Use

✅ **USE this skill when:**

- A task needs write permissions to services not covered by the base role
- Creating AWS resources that require specific IAM policies
- Deploying infrastructure that needs elevated access
- Running operations that need temporary admin-like permissions (within boundary)

## When NOT to Use

❌ **DON'T create a role when:**

- The base ECS task role already has the needed permissions
- Running read-only queries (S3 list, describe stacks, etc.)
- The operation is a simple AWS CLI command within existing permissions

## Constraints

- All roles MUST be prefixed with `agent-task-`
- All roles MUST have `agent-permission-boundary` attached
- Maximum 5 concurrent active roles
- Roles expire after 24 hours (cleanup Lambda deletes them)
- STS session duration: default 1 hour, max 4 hours
- Permission boundary denies: IAM (except scoped creation), Organizations, Account, Billing

## Creating a Task-Scoped Role

### Step 1: Create the role with a trust policy

```bash
# Create trust policy document
exec command:"cat > /tmp/trust-policy.json << 'EOF'
{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Effect\": \"Allow\",
    \"Principal\": {
      \"AWS\": \"$(aws sts get-caller-identity --query Arn --output text)\"
    },
    \"Action\": \"sts:AssumeRole\"
  }]
}
EOF"

# Create the role with permission boundary
exec command:"aws iam create-role \
  --role-name agent-task-$(date +%s) \
  --assume-role-policy-document file:///tmp/trust-policy.json \
  --permissions-boundary arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/agent-permission-boundary \
  --tags Key=Purpose,Value='CDK deployment' Key=CreatedBy,Value=agent"
```

### Step 2: Attach the needed policy

```bash
# Attach a managed policy or create an inline policy
exec command:"aws iam put-role-policy \
  --role-name agent-task-TIMESTAMP \
  --policy-name task-policy \
  --policy-document file:///tmp/task-policy.json"
```

### Step 3: Assume the role

```bash
# Get temporary credentials
exec command:"aws sts assume-role \
  --role-arn arn:aws:iam::ACCOUNT:role/agent-task-TIMESTAMP \
  --role-session-name agent-session \
  --duration-seconds 3600"
```

### Step 4: Use the credentials

```bash
# Export the temporary credentials
exec command:"export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_SESSION_TOKEN=..."

# Run commands with elevated permissions
exec command:"aws cloudformation create-stack ..."
```

### Step 5: Clean up (optional — auto-cleanup runs every 6 hours)

```bash
exec command:"aws iam delete-role-policy --role-name agent-task-TIMESTAMP --policy-name task-policy"
exec command:"aws iam delete-role --role-name agent-task-TIMESTAMP"
```

## Notes

- The permission boundary is a hard ceiling — even with AdministratorAccess attached, the boundary limits what the role can do
- Roles are tracked in the `openclaw-agent-roles` DynamoDB table
- A cleanup Lambda deletes roles older than 24 hours every 6 hours
- Always check `canCreateRole()` before creating (max 5 concurrent)
- Log the role purpose for audit trail
