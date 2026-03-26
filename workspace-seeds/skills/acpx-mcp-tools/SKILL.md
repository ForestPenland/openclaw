---
name: acpx-mcp-tools
description: "Access AgentCore Gateway MCP tools via ACPX coding sessions. Use when: (1) calling structured Gateway tools like deploy_static_site or manage_s3, (2) using MCP-based tool integrations that require the bridge. NOT for: simple AWS CLI operations (use exec directly), file reading/writing on host (use built-in read/write), quick shell commands (use exec)."
metadata:
  {
    "openclaw":
      {
        "emoji": "🔌",
      },
  }
---

# ACPX MCP Tools Skill

Access AgentCore Gateway tools via MCP protocol inside ACPX coding sessions. The MCP gateway bridge handles OAuth2 authentication and JSON-RPC proxying automatically.

## When to Use

✅ **USE ACPX for MCP tools when:**

- Calling AgentCore Gateway tools (deploy_static_site, manage_s3, etc.)
- Using structured tool interfaces that return typed results
- Performing operations that are registered as Gateway targets

## When NOT to Use

❌ **DON'T enter ACPX when:**

- Running simple AWS CLI commands → use `exec` directly (faster, no sandbox overhead)
- Reading or writing files on the host → use built-in `read`/`write`/`edit` tools
- Quick shell commands → use `exec` tool
- Delegating heavy builds → use `exec` + `aws codebuild start-build`

## How It Works

1. Enter an ACPX coding session
2. The MCP gateway bridge (`aws-tools`) is automatically available
3. Call MCP tools using standard tool invocation
4. The bridge handles OAuth2 token exchange with Cognito
5. Results are returned as structured JSON

## Available MCP Tools

The following tools are available via the `aws-tools` MCP server:

- `deploy_static_site` — Deploy a static website to S3 + CloudFront
- `manage_s3` — Structured S3 bucket and object management

Additional tools may be registered dynamically as the agent creates new Lambda functions or APIs.

## Decision Guide

| Task | Use |
|------|-----|
| `aws s3 ls` | `exec` (direct CLI) |
| `aws cloudformation describe-stacks` | `exec` (direct CLI) |
| Deploy a static site with CloudFront | ACPX → `deploy_static_site` MCP tool |
| Structured S3 management | ACPX → `manage_s3` MCP tool |
| CDK deployment | `exec` → delegate to CodeBuild |
| GPU workload | `exec` → delegate to EC2 |

## Notes

- MCP tools only work inside ACPX sessions — they are NOT available in the main session
- The bridge authenticates via OAuth2 client credentials (Cognito)
- Tool results are structured JSON, not raw CLI output
- If a Gateway tool fails, check the bridge logs for OAuth2 or connectivity issues
