---
name: aws-infrastructure
description: "AWS CLI patterns for direct host execution. Use for S3, CloudFormation, EC2, ECS, and other AWS service queries and operations via the exec tool."
metadata:
  openclaw:
    emoji: "☁️"
    requires:
      bins: ["aws"]
---

# AWS Infrastructure Skill

Patterns for using AWS CLI directly on the Fargate host via the `exec` tool.
This is the fastest execution path — no sandbox overhead.

## When to Use

- AWS read queries (list, describe, get)
- S3 operations (ls, cp, sync)
- CloudFormation stack inspection
- Quick AWS write operations within the base task role permissions

## S3 Workspace Sync

```bash
# Sync workspace to S3 (preserves state across container restarts)
aws s3 sync \
  /home/node/.openclaw/workspace/ \
  s3://${WORKSPACE_BUCKET}/${TENANT_ID}/${AGENT_ID}/ \
  --exclude ".openclaw/*" --exclude "*.lock"
```

## CloudFormation Inspection

```bash
# List all stacks
aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \
  --query 'StackSummaries[*].[StackName,StackStatus]' --output table

# Describe a stack
aws cloudformation describe-stacks --stack-name <stack-name> \
  --query 'Stacks[0].{Status:StackStatus,Outputs:Outputs}'

# Get stack events (debugging)
aws cloudformation describe-stack-events --stack-name <stack-name> \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`]'
```

## ECS Service Management

```bash
CLUSTER=$(aws ecs list-clusters --query 'clusterArns[0]' --output text)
SERVICE=$(aws ecs list-services --cluster $CLUSTER --query 'serviceArns[0]' --output text)

# Check service health
aws ecs describe-services --cluster $CLUSTER --service $SERVICE \
  --query 'services[0].{desired:desiredCount,running:runningCount,status:status}'

# Force restart
aws ecs update-service --cluster $CLUSTER --service $SERVICE --force-new-deployment
```

## Elevated Operations

If an operation fails with AccessDenied, use the role-factory skill to
create a temporary elevated role before retrying.
