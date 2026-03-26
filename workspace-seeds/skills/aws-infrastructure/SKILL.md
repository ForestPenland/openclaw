---
name: aws-infrastructure
description: "AWS infrastructure operations via built-in exec tool + AWS CLI. Use when: (1) querying AWS resources (S3, CloudFormation, EC2, IAM), (2) creating or managing S3 buckets, (3) deploying or updating CloudFormation stacks, (4) managing EC2 instances or security groups. NOT for: AgentCore Gateway MCP tools (use ACPX session instead), heavy builds or deployments (delegate to CodeBuild), GPU or long-running workloads (delegate to EC2)."
metadata:
  {
    "openclaw":
      {
        "emoji": "☁️",
        "requires": { "bins": ["aws"] },
      },
  }
---

# AWS Infrastructure Skill

Use the built-in `exec` tool to run AWS CLI commands directly on the host. This is the fastest path for AWS operations — no sandbox overhead.

## When to Use

✅ **USE this skill when:**

- Querying AWS resources (list buckets, describe stacks, check instances)
- Creating or managing S3 buckets and objects
- Deploying or updating CloudFormation stacks
- Managing EC2 instances, security groups, AMIs
- Running quick AWS CLI commands (< 5 minutes)
- Checking deployment status or resource state

## When NOT to Use

❌ **DON'T use this skill when:**

- You need AgentCore Gateway MCP tools (deploy_static_site, manage_s3) → enter ACPX session
- Running CDK deployments or Docker builds → delegate to CodeBuild
- GPU workloads or long-running processes → delegate to EC2
- Deploying persistent services → delegate to ECS Fargate

## Common Patterns

### S3 Operations

```bash
# List buckets
exec command:"aws s3 ls"

# Create bucket
exec command:"aws s3 mb s3://agent-my-site-$(date +%s)"

# Upload files
exec command:"aws s3 cp ./build/ s3://my-bucket/prefix/ --recursive"

# Configure static website hosting
exec command:"aws s3 website s3://my-bucket --index-document index.html --error-document error.html"

# Sync directory
exec command:"aws s3 sync ./dist/ s3://my-bucket/ --delete"
```

### CloudFormation

```bash
# List stacks
exec command:"aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE"

# Describe a stack
exec command:"aws cloudformation describe-stacks --stack-name MyStack --query 'Stacks[0].Outputs'"

# Create stack from template
exec command:"aws cloudformation create-stack --stack-name agent-my-stack --template-body file://template.yaml --capabilities CAPABILITY_IAM"

# Wait for stack completion
exec command:"aws cloudformation wait stack-create-complete --stack-name agent-my-stack"

# Delete stack
exec command:"aws cloudformation delete-stack --stack-name agent-my-stack"
```

### EC2 Queries

```bash
# List running instances
exec command:"aws ec2 describe-instances --filters 'Name=instance-state-name,Values=running' --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,Tags[?Key==`Name`].Value|[0]]' --output table"

# Describe security groups
exec command:"aws ec2 describe-security-groups --query 'SecurityGroups[].[GroupId,GroupName,Description]' --output table"

# List available AMIs (Amazon Linux 2023)
exec command:"aws ec2 describe-images --owners amazon --filters 'Name=name,Values=al2023-ami-*-x86_64' --query 'sort_by(Images,&CreationDate)[-1].[ImageId,Name]' --output text"
```

### General Resource Discovery

```bash
# List all resources in a stack
exec command:"aws cloudformation list-stack-resources --stack-name MyStack --query 'StackResourceSummaries[].[ResourceType,LogicalResourceId,PhysicalResourceId]' --output table"

# Check ECS services
exec command:"aws ecs list-services --cluster my-cluster"

# View CloudWatch logs
exec command:"aws logs tail /aws/ecs/my-service --since 1h --follow"
```

## Notes

- AWS CLI is pre-installed on the Fargate host — no setup needed
- All commands run with the ECS task role's permissions
- Use `--output table` for human-readable output, `--output json` for parsing
- Use `--query` (JMESPath) to filter results
- For elevated permissions, create a task-scoped IAM role (see role-factory skill)
