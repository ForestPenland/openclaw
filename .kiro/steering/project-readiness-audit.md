---
inclusion: manual
---

# OpenClaw AWS Extension — Project Readiness Audit

Comprehensive analysis performed March 27, 2026. Covers correctness review,
documentation validation, local-vs-deployed divergence, and publish readiness.

## Executive Summary

The project has two deployment models in two separate repos:

1. **openclaw/infra/** — CDK Python (9 stacks, ECS Fargate, Bedrock, DynamoDB, S3, Lambda).
   Deployed to account 608291745848 (openclaw-dev) in us-east-1. Gateway is RUNNING and HEALTHY.

2. **sample-OpenClaw-on-AWS-with-Bedrock/** — CloudFormation templates (EC2-based, one-click deploy).
   Not currently deployed. Designed for community/public use.

These are fundamentally different deployment architectures (Fargate vs EC2) targeting
different audiences (internal/advanced vs community/quick-start).

## What Is Actually Deployed (Account 608291745848, us-east-1)

### CDK Stacks (all healthy)

| Stack | Status | Last Updated |
|-------|--------|-------------|
| OpenClawStorage | CREATE_COMPLETE | Mar 24 |
| OpenClawGateway | UPDATE_COMPLETE | Mar 26 |
| OpenClawIdentity | CREATE_COMPLETE | Mar 24 |
| OpenClawApi | UPDATE_COMPLETE | Mar 25 |
| OpenClawMemory | CREATE_COMPLETE | Mar 25 |
| OpenClawScheduler | CREATE_COMPLETE | Mar 25 |
| OpenClawBuilder | CREATE_COMPLETE | Mar 25 |
| OpenClawAgentCoreTools | CREATE_COMPLETE | Mar 25 |
| OpenClawComputeEnvironments | UPDATE_COMPLETE | Mar 26 |

### Agent-Created Stacks (built autonomously by the running agent)

| Stack | Purpose |
|-------|---------|
| rockclaw-email | SES domain, S3 inbound, Lambda email processor |
| rockclaw-health-monitoring | External watchdog for OpenClaw + AgentCore agents |
| cairnwatch-backend | Cairnwatch Phase 1 backend (DynamoDB, Lambda, EventBridge) |

### ECS Gateway

- Cluster: OpenClawGateway-GatewayCluster30039C24-4SVe1JxlYpj9
- Service: ACTIVE, desired=1, running=1
- Task: RUNNING, HEALTHY (started Mar 26 23:21 ET)
- Task definition revision: 19
- CPU: 1024, Memory: 2048 MiB, Platform: X86_64/LINUX
- Model: us.anthropic.claude-sonnet-4-6

### Lambda Functions (11 total, 6 from CDK stacks)

CDK-managed: openclaw-webhook-handler, openclaw-deploy-static-site,
openclaw-environment-cleanup, openclaw-consolidation-handler, openclaw-heartbeat-handler,
plus CDK helper Lambdas.

Agent-created: cost-anomaly-telegram, rockclaw-email-processor,
rockclaw-health-telegram-notifier, cairnwatch-poller, cairnwatch-api.

### DynamoDB Tables (11 total, 9 from CDK)

CDK-managed: openclaw-memory, openclaw-sessions, openclaw-agents, openclaw-dedup,
openclaw-connections, openclaw-environments, openclaw-agent-roles, openclaw-tool-registry,
openclaw-cost-ledger.

Agent-created: cairnwatch-feed, cairnwatch-waitlist.

### S3 Buckets

CDK-managed: workspace, skills, artifacts (plus CDK assets bucket).
Agent-created: cairnwatch-site, forest-obsidian-vault, kawasaki-x2-tribute-site,
openclaw-cfn-templates, oracle-agent-build, rockclaw-email.

### Secrets Manager (9 secrets)

CDK-managed: openclaw/telegram-bot-token, openclaw/slack-bot-token, openclaw/github-token,
openclaw/agentcore-gateway-credentials, openclaw-keypair.
AgentCore-managed: 3 OAuth2 credential secrets (oracle-agent, memory-agent, creative-agent).
Agent-created: rockclaw/github/pat.

### EventBridge Rules

- openclaw-heartbeat-30min: ENABLED, rate(30 minutes)
- openclaw-nightly-consolidation: ENABLED, cron(0 2 ? * * *)

### Management Account (735093477682)

- openclaw-admin-override stack: CREATE_COMPLETE
  - 4 SSO permission sets (Admin, Networking, AccountFactory, ReadOnly)
  - Permission boundary for human-in-the-loop elevated access
  - SNS alert topic for admin override usage

## Local Code vs Deployed: Divergence Analysis

### CDK Code Matches Deployment (No Drift Detected)

The gateway_stack.py environment variables match the deployed task definition exactly:
- BEDROCK_MODEL_ID: `us.anthropic.claude-sonnet-4-6` (code) = `us.anthropic.claude-sonnet-4-6` (deployed)
- CPU/Memory: 1024/2048 (code) = 1024/2048 (deployed)
- Platform: X86_64/LINUX (code) = X86_64/LINUX (deployed)
- All 8 environment variables match

### Documentation vs Reality Discrepancies

1. **DEPLOYMENT.md says "Nova Lite by default"** but the actual deployed model is
   `us.anthropic.claude-sonnet-4-6` (Claude Sonnet 4.6). The CDK code also sets
   Claude Sonnet 4.6 as the default. DEPLOYMENT.md intro paragraph is stale.

2. **DEPLOYMENT.md Environment Variables table** says BEDROCK_MODEL_ID default is
   `us.anthropic.claude-sonnet-4-6` which matches the code, but the prose says
   "Amazon Bedrock (Nova Lite by default)" in the opening paragraph.

3. **AgentCore Memory store ID is still PLACEHOLDER** — the SSM parameter
   `/openclaw/memory-store-id` has never been populated. The Memory stack and
   AgentCore Memory Adapter are deployed but the actual memory store has not been
   created at runtime. This means semantic memory retrieval is not functional.

4. **Scheduler Lambda handlers are described as "stubs"** in the steering docs,
   but the actual Lambda code in `lambdas/` has real implementations. The
   aws-deployment.md steering file says "Lambda handlers are stubs" which is
   inaccurate — they have real handler code.

5. **Builder stack is described as "Not yet used"** — this is accurate. The builder
   agent role exists but no builder sub-agent is actively running.

6. **Cost estimates in DEPLOYMENT.md** say ~$75-80/month infrastructure. This is
   roughly accurate for the base CDK stacks, but doesn't account for the
   agent-created stacks (rockclaw, cairnwatch) which add Lambda, DynamoDB, SES,
   and S3 costs.

### Sample Project vs CDK Project Divergence

These are intentionally different architectures:

| Aspect | openclaw/infra (CDK) | sample-OpenClaw-on-AWS-with-Bedrock (CFn) |
|--------|---------------------|------------------------------------------|
| Compute | ECS Fargate | EC2 (Graviton ARM) |
| IaC | CDK Python (9 stacks) | Single CloudFormation YAML |
| Model default | Claude Sonnet 4.6 | Nova 2 Lite |
| Access | ECS Exec | SSM Session Manager + port forwarding |
| Networking | VPC + NAT GW | VPC + optional VPC endpoints |
| Cost | ~$75-80/mo | ~$31-56/mo |
| Target audience | Internal/advanced | Community/quick-start |
| Multi-tenant | Yes (S3 prefixes, IAM boundaries) | No (single user) |
| AgentCore | Yes (Gateway, Memory, Runtime) | No |
| ACPX/MCP | Yes | No |

## Spec Task Completion Status

From openclaw/.kiro/specs/openclaw-aws-extension/tasks.md:

- Tasks 1-14: COMPLETE (infrastructure, adapters, agents, lambdas, CDK stacks)
- Task 15 (Gateway Config Manager): COMPLETE
- Task 16 (Bedrock Provider Plugin): COMPLETE
- Task 17 (Native Telegram Channel): COMPLETE
- Task 18 (Checkpoint): COMPLETE
- Task 19 (E2E Verification): PARTIALLY COMPLETE (19.1 done, 19.2 not done)
- Task 20 (E2E Checkpoint): NOT DONE
- Task 21 (Documentation Update): PARTIALLY COMPLETE (21.1 done but has stale content)
- Task 22 (Final Checkpoint): NOT DONE

Optional tasks (marked with *): 14 property tests and unit tests NOT written.

## Publish Readiness Assessment

### Ready for Internal Use

The CDK deployment is production-quality for internal use:
- Gateway is running and healthy
- Telegram channel is connected and working
- Bedrock integration is functional
- Agent is autonomously creating infrastructure (3 stacks built)
- Role factory with permission boundaries is operational
- Cleanup automation is running (30min environment, 6hr role cleanup)

### Not Ready for Public/Community Publishing

The CDK-based deployment (openclaw/infra/) has several blockers for public use:

1. **Hardcoded account references**: deploy-admin-override.sh references account
   735093477682 and 608291745848. The admin-override CFn template URL points to
   an S3 bucket in account 608291745848.

2. **No parameterization for new users**: The CDK stacks assume a specific AWS
   Organization structure with Identity Center, management account, and member
   accounts. A new user can't just `cdk deploy --all`.

3. **Missing property tests**: 14 property tests are marked as optional but would
   be needed for confidence in a public release.

4. **AgentCore Memory not functional**: The memory store ID is still PLACEHOLDER.
   Semantic memory retrieval doesn't work.

5. **No CI/CD pipeline**: No GitHub Actions or CodePipeline for automated testing
   and deployment.

6. **Documentation gaps**: DEPLOYMENT.md has stale references (Nova Lite default),
   and the E2E verification tasks are incomplete.

### Sample Project IS Closer to Publish-Ready

The sample-OpenClaw-on-AWS-with-Bedrock/ repo is much closer to community-ready:
- One-click CloudFormation deploy (no CDK knowledge needed)
- 4-region launch buttons
- Clear README with architecture diagram
- Cost breakdown
- Troubleshooting guide
- Enterprise multi-tenant option
- Skills (S3 files, Kiro CLI, AWS backup)
- Kiro conversational deployment guide

Remaining gaps for the sample project:
- CloudFormation template URL points to a China region S3 bucket
  (sharefile-jiade.s3.cn-northwest-1.amazonaws.com.cn) — should be a global/US bucket
- No automated testing of the CloudFormation template
- Enterprise features are documented but deployment scripts may need testing

## Recommended Actions

### Immediate (Documentation Fixes)

1. ~~Fix DEPLOYMENT.md opening paragraph: change "Nova Lite by default" to match
   actual default (Claude Sonnet 4.6)~~ DONE
2. ~~Fix DEPLOYMENT.md stacks table: remove "Lambda handlers are stubs"~~ DONE
3. ~~Fix DEPLOYMENT.md "Changing the AI Model" section: update default model~~ DONE
4. ~~Fix aws-deployment.md steering: update BEDROCK_MODEL_ID default~~ DONE
5. Complete E2E verification tasks (19.2, 20)

### Short-Term (Publish Blockers)

4. Create the AgentCore Memory store and update the SSM parameter
5. Remove hardcoded account IDs from local-scripts/
6. Add parameterization for the CDK deployment (region, account, tenant config)
7. Move CloudFormation template hosting to a public US-region S3 bucket

### Medium-Term (Quality)

8. Write the 14 optional property tests
9. Set up CI/CD pipeline (GitHub Actions)
10. Add CDK snapshot tests
11. Drift detection automation


## Agent-Created Infrastructure Inventory (Outside CDK Stacks)

The OpenClaw agent (Rock Penland) has autonomously created substantial
infrastructure beyond the 9 CDK stacks. Inventoried March 27, 2026.

### AgentCore Resources (Created at Runtime)

| Resource | ID/Name | Status | Purpose |
|----------|---------|--------|---------|
| AgentCore Gateway | openclawtoolgateway-mm1xyfi9xg | ACTIVE | MCP tool server routing calls to Runtime agents |
| AgentCore Memory | RockClawMemory-wWEAxO2S48 | ACTIVE | Semantic + episodic memory (2 strategies: semanticFacts, userPreferences) |
| OracleAgent Runtime | OracleAgent-G8OK6nBtWy v5 | READY | Research/analysis (Strands + Nova Lite) |
| MemoryAgent Runtime | MemoryAgent-pWu4FY8hZ5 v3 | READY | Memory MCP server (4 tools: store, search, context, list) |
| CreativeAgent Runtime | CreativeAgent-mDA9geAFGF v3 | READY | Image/video generation (Nova Canvas + Nova Reel) |
| Cognito Pool | us-east-1_zqvxyb25H | ACTIVE | OAuth2 M2M auth for Gateway to Runtime |
| 3 Cognito Resource Servers | oracle/memory/creative-agent-runtime | ACTIVE | Per-agent OAuth scopes |
| 3 M2M Client Credentials | In Secrets Manager | ACTIVE | Per-agent OAuth client creds |
| 3 AgentCore Identity Providers | oracle/memory/creative-agent-m2m-creds | ACTIVE | JWT credential providers |

### ECR Repositories (3 agent-created)

oracle-agent (7 images), memory-agent (6 images), creative-agent (12 images)

### CodeBuild Projects (3 agent-created)

oracle-agent-build, memory-agent-build, creative-agent-build — all S3-sourced,
standard:7.0 image, BUILD_GENERAL1_SMALL.

### IAM Roles (15 agent-task-* roles)

Key long-lived roles:
- agent-task-memory-agent-exec (MemoryAgent Runtime execution)
- agent-task-creative-agent-exec (CreativeAgent Runtime execution)
- agent-task-oracle-runtime-exec (OracleAgent Runtime execution)
- agent-task-oracle-codebuild (CodeBuild service role for all agent builds)
- agent-task-cost-anomaly-lambda-exec (Cost anomaly Lambda)
- agent-task-telegram-notifier-exec (Health monitoring notifier)
- agent-task-rockclaw-email-lambda (Email processor Lambda)
- 8 temporary task roles (various one-off operations)

### Skills in S3 Workspace (10 skills)

| Skill | Packageable? | Notes |
|-------|-------------|-------|
| acpx-mcp-tools | Yes | Teaches ACPX/MCP usage patterns |
| agentcore-agent-builder | Yes | Full agent deployment playbook with isolation checklist |
| aws-infrastructure | Yes | AWS CLI patterns for direct host execution |
| role-factory | Yes | IAM role creation with permission boundary |
| cost-monitor | Partially | Has account-specific IDs, needs parameterization |
| creative-agent | Yes | CreativeAgent usage guide |
| github | Partially | Has org-specific config (Rock-f-me) |
| admin-override | Yes | Identity Center OIDC device auth flow |
| ses-email | No | Personal email domain (f-me.ai) |
| obsidian-vault | No | Personal vault sync |

### Memory System (3-layer architecture)

1. STM: AgentCore Memory events per session (create_event / list_events)
2. LTM: AgentCore Memory semantic/preference records (retrieve_memory_records)
3. File-based: memory/ directory with PARA knowledge graph

Knowledge graph: 5 project files, 2 area files, 4 resource files, tacit
knowledge (preferences, lessons, security, conventions), daily logs.

The SSM parameter /openclaw/memory-store-id is still PLACEHOLDER because
the agent created the memory store directly via AgentCore API (ID:
RockClawMemory-wWEAxO2S48) rather than through the CDK Memory stack.

### Other Agent-Created Resources

- S3: oracle-agent-build-608291745848 (build artifacts)
- S3: forest-obsidian-vault-608291, kawasaki-x2-tribute-site
- SNS: cost-anomaly-alerts
- Lambda: cost-anomaly-telegram
- EventBridge: rockclaw-ecs-task-stopped (ECS crash detection)
- CloudWatch Alarms: 3 (CPU, memory, task-down)
- Route 53: f-me.ai domain
- SES: f-me.ai identity (sandbox mode)
- GitHub org: Rock-f-me (3 repos)

## Packageability Assessment

### Tier 1: Should Replace Incomplete CDK Stacks

These agent-created resources are production-proven and should replace
the placeholder/stub CDK stacks:

1. AgentCore Memory integration — The MemoryAgent + AgentCore Memory
   store replaces the placeholder MemoryStack. The agent's implementation
   is working (4 MCP tools, multi-actor, semantic + preference strategies).
   The CDK MemoryStack just creates an SSM parameter with PLACEHOLDER.

2. AgentCore Gateway + Runtime agent pattern — The agent-builder skill
   documents a complete, repeatable pattern for deploying Strands agents
   to AgentCore Runtime and registering them as Gateway MCP tools. This
   could become a CDK construct.

3. Role Factory pattern — The role-factory skill + the
   agent-permission-boundary policy (already in CDK) form a complete
   IAM delegation system. The skill documentation is the missing piece.

4. Health monitoring — The rockclaw-health-monitoring stack (CloudWatch
   alarms + ECS task-stopped rule + Telegram notifier) should be a CDK
   stack. Essential for production.

### Tier 2: Packageable with Generalization

5. Cost monitoring — cost-monitor skill + cost-anomaly-telegram Lambda +
   budget + anomaly detection. Needs account/chat IDs parameterized.

6. Admin override — admin-override skill + scripts + CFn stack. Needs
   SSO instance ARN and account IDs parameterized.

7. Skills framework — The skills directory structure and SKILL.md format
   with YAML frontmatter is a proven pattern. The acpx-mcp-tools,
   aws-infrastructure, and role-factory skills are generic enough to ship.

### Tier 3: Not Packageable (Personal/Specific)

8. Email infrastructure (f-me.ai domain, SES, Rock Penland persona)
9. Obsidian vault sync
10. GitHub org-specific configuration
11. Application stacks (rockclaw-email, cairnwatch, rockclaw-health-monitoring)
