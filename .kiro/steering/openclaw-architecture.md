---
inclusion: auto
---

# OpenClaw Architecture Reference

This document captures the core design and capabilities of OpenClaw, the open-source
AI agent gateway we are extending with AWS services. All information is sourced from
the official docs at https://docs.openclaw.ai/.

## What OpenClaw Is

OpenClaw is a self-hosted gateway that connects chat apps (WhatsApp, Telegram, Discord,
Slack, Signal, iMessage) to AI coding agents. A single long-lived Gateway process owns
all messaging surfaces and routes messages to an embedded agent runtime built on the
Pi agent core.

- MIT licensed, open source, community-driven
- Runs on Node 24 (recommended) or Node 22 LTS
- Config lives at `~/.openclaw/openclaw.json` (JSON5 format)
- Default port: 18789 (WebSocket + HTTP)

## Gateway Architecture

One Gateway per host. It is the single source of truth for sessions, routing, and
channel connections.

- Maintains provider connections (LLM model providers)
- Exposes a typed WebSocket API (requests, responses, server-push events)
- Validates inbound frames against JSON Schema
- Emits events: agent, chat, presence, health, heartbeat, cron
- Clients (macOS app, CLI, web UI) connect over WebSocket
- Nodes (macOS/iOS/Android/headless) connect with `role: node`
- Canvas host served at `/__openclaw__/canvas/` and `/__openclaw__/a2ui/`

Connection lifecycle: WebSocket text frames with JSON payloads. First frame must be
`connect`. Auth token required when `OPENCLAW_GATEWAY_TOKEN` is set.

## Agent Runtime

OpenClaw runs a single embedded agent runtime built on the Pi agent core (models,
tools, prompt pipeline). Session management, discovery, tool wiring, and channel
delivery are OpenClaw-owned layers on top.

### Workspace

The workspace (`agents.defaults.workspace`, default `~/.openclaw/workspace`) is the
agent's home directory. Tools resolve relative paths against it. It is NOT a hard
sandbox — absolute paths can reach elsewhere unless sandboxing is enabled.

Bootstrap files injected at session start (blank files skipped, large files truncated):
- `AGENTS.md` — operating instructions + memory guidance
- `SOUL.md` — persona, tone, boundaries
- `USER.md` — user profile
- `IDENTITY.md` — agent name/vibe/emoji
- `TOOLS.md` — user-maintained tool notes (guidance only, does not control availability)
- `HEARTBEAT.md` — optional checklist for heartbeat runs
- `BOOTSTRAP.md` — one-time first-run ritual (deleted after completion)
- `BOOT.md` — optional startup checklist on gateway restart

### Memory

Memory is plain Markdown in the workspace. The model only "remembers" what gets
written to disk.

- `memory/YYYY-MM-DD.md` — daily log (append-only), read today + yesterday at start
- `MEMORY.md` — curated long-term memory, loaded in main private session only
- `memory_search` tool — semantic recall over indexed snippets
- `memory_get` tool — targeted read of specific file/line range
- Vector memory search available (BM25 + vector hybrid)
- Automatic memory flush before session compaction

### Sessions

Session transcripts stored as JSONL at:
`~/.openclaw/agents/<agentId>/sessions/<SessionId>.jsonl`

## Built-in Tools

These ship with OpenClaw and are always available (subject to tool policy):

| Tool | What it does |
|------|-------------|
| `exec` / `process` | Run shell commands, manage background processes |
| `browser` | Control Chromium browser (navigate, click, screenshot) |
| `web_search` / `web_fetch` | Search the web, fetch page content |
| `read` / `write` / `edit` | File I/O in the workspace |
| `apply_patch` | Multi-hunk file patches |
| `message` | Send messages across all channels |
| `canvas` | Drive node Canvas (present, eval, snapshot) |
| `nodes` | Discover and target paired devices |
| `cron` / `gateway` | Manage scheduled jobs, restart gateway |
| `image` / `image_generate` | Analyze or generate images |
| `sessions_*` / `agents_list` | Session management, sub-agents |

Tool access controlled via `tools.allow` / `tools.deny` in config. Deny always wins.
Tool profiles: `full` (all), `coding` (file I/O, runtime, sessions, memory, image),
`messaging`, `minimal` (session_status only).

Tool groups for allow/deny: `group:runtime`, `group:fs`, `group:sessions`,
`group:memory`, `group:web`, `group:ui`, `group:automation`, `group:messaging`,
`group:nodes`, `group:openclaw`.

## Skills

Skills are AgentSkills-compatible folders with a `SKILL.md` containing YAML frontmatter
and natural language instructions. They teach the agent how to use tools.

Loaded from three locations (workspace wins on name conflict):
1. Bundled (shipped with install)
2. Managed/local: `~/.openclaw/skills`
3. Workspace: `<workspace>/skills`

Skills are gated at load time via `metadata.openclaw.requires` (bins, env, config).
ClawHub is the public skills registry (https://clawhub.com).

Skills are injected as compact XML into the system prompt. Cost: ~97 chars + field
lengths per skill.

## Plugins

Plugins extend OpenClaw with channels, model providers, tools, skills, speech, image
generation, and more. Two formats: Native (in-process) and Bundle (Codex/Claude/Cursor
compatible).

Key plugin capabilities:
- `registerProvider` — model provider (LLM)
- `registerChannel` — chat channel
- `registerTool` — agent tool
- `registerHook` — lifecycle hooks
- `registerSpeechProvider` — TTS/STT
- `registerWebSearchProvider` — web search
- `registerHttpRoute` — HTTP endpoint

Plugin slots (exclusive): `memory` (default: memory-core), `contextEngine` (default: legacy).

## ACPX (Coding Agent Sandbox)

ACPX is OpenClaw's coding agent sandbox — a separate, isolated execution environment
for complex coding tasks. It is an extension/plugin, not part of the core agent runtime.

Key facts about ACPX:
- Provides isolated file editing, code execution, and MCP tool integration
- MCP servers configured under `plugins.entries.acpx.config.mcpServers` run INSIDE the ACPX sandbox
- MCP servers do NOT run in the main conversational session
- The main session uses built-in tools (exec, read, write, edit) directly on the host
- ACPX is optional — the agent works without it using built-in tools

## MCP (Model Context Protocol)

OpenClaw supports MCP servers for external tool integration. Config key: `mcp.servers`
in `openclaw.json`.

Critical architecture detail: MCP servers configured in `mcp.servers` are managed by
the CLI (`openclaw mcp list/set/show/unset`) but are NOT automatically spawned by the
Gateway for the main conversational session. MCP tools are available inside ACPX
sessions via `plugins.entries.acpx.config.mcpServers`.

For the main session to use external services, the agent uses its built-in `exec` tool
to run CLI commands (e.g., `aws` CLI) or the agent can be taught via SKILL.md files.

## Sandbox

OpenClaw can run agents in isolated sandbox runtimes:
- Docker containers (default backend)
- SSH remote runtimes
- OpenShell runtimes

Config: `agents.defaults.sandbox` with modes `off`, `non-main`, `all`.
Scope: `session`, `agent`, `shared`.

When sandboxing is enabled, tools operate inside a sandbox workspace under
`~/.openclaw/sandboxes`, not the host workspace.

## Heartbeat

Periodic agent turns in the main session (default: every 30 minutes). The agent reads
`HEARTBEAT.md` and surfaces anything needing attention.

- `HEARTBEAT_OK` response means nothing needs attention (suppressed by default)
- Configurable: interval, model, target channel, active hours, isolated sessions
- `lightContext: true` — only inject HEARTBEAT.md (saves tokens)
- `isolatedSession: true` — fresh session each run (no conversation history)

## Model Configuration

Model refs use `provider/model` format (e.g., `anthropic/claude-opus-4-6`,
`amazon-bedrock/us.anthropic.claude-sonnet-4-6`).

Supports fallback chains:
```json
{
  "agent": {
    "model": {
      "primary": "anthropic/claude-opus-4-6",
      "fallbacks": ["anthropic/claude-sonnet-4"]
    }
  }
}
```

## Multi-Agent Routing

Multiple agents with different workspaces, models, and skills. Route by channel,
guild, or sender.

```json
{
  "agents": {
    "list": [
      { "id": "main", "default": true },
      { "id": "coding", "model": "anthropic/claude-opus-4-6", "skills": ["git"] }
    ]
  },
  "routing": {
    "bindings": [
      { "agentId": "coding", "match": { "channel": "discord", "guildId": "123" } }
    ]
  }
}
```

## Key Design Principles for Our AWS Extension

1. The Gateway handles all channel integrations natively — no custom webhook pipelines
2. Built-in tools (exec, read, write, edit) run directly on the host (Fargate container)
3. Skills teach the agent capabilities via natural language SKILL.md files
4. ACPX is needed for MCP tool access (MCP servers run inside the ACPX sandbox). ACPX is a bundled extension that is disabled by default — it must be explicitly enabled with `enabled: true` in `plugins.entries.acpx`
5. The agent's workspace files (SOUL.md, AGENTS.md, etc.) are the source of truth
6. Memory is plain Markdown — daily logs + curated MEMORY.md
7. Plugins extend capabilities (model providers, channels, tools)
8. Config at `~/.openclaw/openclaw.json` — OpenClaw may overwrite it on startup
9. Plugin config path is `plugins.entries.<id>.config`, not `plugins.<id>` — the `entries` intermediate key is required by OpenClaw's config schema
