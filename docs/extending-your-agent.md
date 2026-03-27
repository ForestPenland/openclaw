# Extending Your OpenClaw Agent

Once your OpenClaw Gateway is running on AWS with the foundational CDK stacks,
the agent can self-extend by building new capabilities. This page describes
patterns and ideas for what your agent can build autonomously using the
included skills.

## How Self-Extension Works

The agent uses three foundational skills to create new capabilities:

1. **agentcore-agent-builder** — Deploy new AI agents as MCP tools on AgentCore Runtime
2. **role-factory** — Create scoped IAM roles for elevated AWS operations
3. **aws-infrastructure** — Direct AWS CLI operations on the Fargate host

The pattern: the agent writes code, builds a container, deploys it to AgentCore
Runtime, registers it as a Gateway MCP tool, and can then call it from future
conversations. Each new agent becomes a permanent capability.

## Example Agents You Can Build

### Research Agent

A lightweight agent powered by Amazon Nova Lite that performs web research
and topic analysis. Useful as a cheap second opinion before the main agent
acts on complex decisions.

- Model: Nova Lite ($0.30/$2.50 per 1M tokens)
- Tools: `research(prompt)`, `analyze(topic, depth)`
- Use case: "Research the best approach to X before I commit"

### Creative Agent

An AI creative director that generates images via Nova Canvas and videos
via Nova Reel. Uses a reasoning layer (Strands + Nova Lite) to translate
high-level briefs into optimized generation prompts.

- Models: Nova Lite (director) + Nova Canvas (images) + Nova Reel (video)
- Tools: `generate_image(brief)`, `edit_image(s3_key, instruction)`,
  `create_brand_kit(brand_name)`, `generate_video(brief)`
- Use case: Brand identity, marketing assets, product visuals

### Memory Agent

A persistent semantic memory server backed by AgentCore Memory. Stores
conversation turns, extracts facts and preferences automatically, and
provides semantic search across all past knowledge.

- Backend: AgentCore Memory (semantic + episodic strategies)
- Tools: `memory_store(actor, session, user_msg, assistant_msg)`,
  `memory_search(actor, query)`, `memory_context(actor, session)`,
  `memory_list(actor)`
- Use case: Cross-session context, operator preference learning
- Reference implementation: `agents/memory-agent/`

### Data Analysis Agent

An agent with pandas, matplotlib, and scientific Python libraries for
data analysis tasks. Accepts CSV/JSON data, runs analysis, generates
charts, and returns insights.

- Model: Claude Sonnet or Nova Pro for reasoning
- Tools: `analyze_data(s3_key, question)`, `generate_chart(data, chart_type)`
- Use case: Business intelligence, log analysis, cost reporting

### Code Review Agent

An agent that reviews pull requests, checks for security issues, and
suggests improvements. Can be triggered by GitHub webhooks through the
Api stack.

- Model: Claude Sonnet for code understanding
- Tools: `review_pr(repo, pr_number)`, `security_scan(code)`
- Use case: Automated code review, security auditing

## Other Capabilities Your Agent Can Build

Beyond AgentCore Runtime agents, the OpenClaw agent can create:

### Email Integration

Connect your agent to email using Amazon SES. The agent can receive
inbound emails, process them with Bedrock, and send responses. Requires
SES domain verification and a Lambda processor.

### Cost Monitoring

Set up AWS Cost Explorer anomaly detection, budgets, and automated
cost reports delivered to your messaging channel. The agent creates
the necessary IAM roles, anomaly monitors, and scheduled reports.

### Health Monitoring

CloudWatch alarms for ECS task health, CPU/memory utilization, and
automatic crash detection via EventBridge. The foundational health
stack is included in the CDK deployment.

### Static Site Deployment

Deploy static websites to S3 + CloudFront using the `deploy_static_site`
MCP tool (already registered on the AgentCore Gateway). The agent can
generate HTML/CSS, deploy it, and return the URL.

### Obsidian/Notes Sync

Sync the agent's knowledge graph to an S3-backed Obsidian vault or
similar note-taking system. The agent maintains structured knowledge
files that can be browsed externally.

### GitHub Integration

Create and manage GitHub repositories, commit code, and push changes.
The agent can publish its own skills, agent source code, or project
artifacts to GitHub.

## Building Your Own Agent

See the `agentcore-agent-builder` skill in `workspace-seeds/skills/` for
the complete step-by-step pattern. The `agents/memory-agent/` directory
contains a reference implementation with Dockerfile, buildspec, and
Python source.

The key steps:
1. Write a FastMCP server with `@mcp.tool()` decorated functions
2. Create an ECR repository and build the container via CodeBuild
3. Create an IAM execution role using the role-factory pattern
4. Set up Cognito OAuth2 M2M authentication
5. Deploy to AgentCore Runtime
6. Register as a Gateway target

Each new agent gets its own isolated set of resources (ECR repo, IAM role,
Cognito client, Runtime instance, Gateway target) following the naming
convention `<agent-name>-*`.
