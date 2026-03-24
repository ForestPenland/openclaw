# Research Summary: felixcraft.ai, OpenClaw, and Ecosystem

*Compiled March 2026 — Comprehensive background for AWS-native OpenClaw architecture project*

---

## Part 1: Peter Steinberger — The Creator of OpenClaw

### Background

Peter Steinberger is an Austrian software developer, born and raised in rural Austria, who became obsessed with computers at age 14 when a summer guest introduced him to a PC. He is best known as the founder of PSPDFKit, an enterprise PDF SDK company.

- **PSPDFKit (2011–2024):** Steinberger founded PSPDFKit as a solo side project and grew it into a global PDF SDK that powered apps used by nearly a billion people. After 13 years, he achieved a nine-figure exit in 2024. By the time of the exit, PSPDFKit powered PDF rendering in apps across enterprise, government, and consumer sectors worldwide.
- **Post-exit burnout:** After selling PSPDFKit, Steinberger experienced significant burnout. He took a one-way ticket to Madrid to reset. OpenClaw was the 44th AI-related project he had experimented with since 2009.
- **OpenClaw creation (November 2025):** In a single one-hour coding session in November 2025, Steinberger built the initial version of what would become OpenClaw, then named "Clawdbot" (later also called "Moltbot" briefly before settling on OpenClaw).
- **Explosive growth:** By early February 2026, the GitHub repository had surpassed 145,000 stars — a record at the time, making it the fastest-growing repository on GitHub by star count. As of March 2026, the repo shows 328,000+ stars and 63,700+ forks with 20,797+ commits.
- **Joined OpenAI:** On February 14, 2026, Steinberger announced he was joining OpenAI. The OpenClaw project has been moved to an open-source foundation to ensure its continuity as a community project.
- **GitHub:** [steipete (Peter Steinberger)](https://github.com/steipete)
- **OpenClaw GitHub:** [openclaw/openclaw](https://github.com/openclaw/openclaw)

---

## Part 2: OpenClaw — What It Is

### High-Level Description

OpenClaw is a free, open-source autonomous AI agent framework designed to run locally on a user's machine (or a VPS). It functions as a personal AI assistant that connects to messaging platforms and other services through a modular skill system. The core philosophy: **your agent runs on your hardware, your data stays local, you own the agent**.

Unlike cloud-based AI services, OpenClaw gives users complete control over data, processing, and agent behavior.

### Key Stats (as of March 2026)

- 328,000+ GitHub stars; 63,700+ forks
- Fastest-growing GitHub repo ever by star count
- ClawHub (skill registry) hosts 13,729+ community-built skills
- Node 24 (recommended) or Node 22.16+ runtime requirement
- Installation: `npm install -g openclaw` / `openclaw onboard --install-daemon`

---

## Part 3: OpenClaw Architecture — Deep Dive

### The Gateway (Central Control Plane)

The architectural centerpiece of OpenClaw is the **Gateway** — a WebSocket server process running locally at `ws://127.0.0.1:18789`. It serves as the central control plane for all operations:

- Routes incoming messages from 20+ messaging platforms (WhatsApp, Telegram, Slack, Discord, Signal, iMessage/BlueBubbles, Microsoft Teams, Matrix, IRC, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, WebChat) to the agent
- Manages sessions and routes tool execution
- Connects to CLI clients, web interfaces, and mobile nodes
- Runs cron jobs internally (persisted under `~/.openclaw/cron/`)
- Handles webhooks: external POSTs to `http://localhost:18789/webhook/<path>`
- Manages the heartbeat cycle (default: every 30 minutes)

### The Workspace — Agent Identity in Flat Files

Every OpenClaw agent has a **workspace directory** (default: `~/.openclaw/workspace`) containing markdown files that define the agent. On each session start, these files are assembled and injected into the system prompt:

| File | Purpose |
|------|---------|
| `SOUL.md` | Agent's personality, values, tone, behavioral boundaries. The "character sheet." Injected first, every session. |
| `AGENTS.md` | Operating instructions: how the agent uses memory, priorities, behavioral rules. |
| `TOOLS.md` | Documents which tools the agent has access to and usage notes. Does not grant/revoke permissions (that's in config). |
| `USER.md` | Context about the human the agent works with — makes interactions feel personal. |
| `IDENTITY.md` | Agent name, vibe, emoji. Created during bootstrap. |
| `MEMORY.md` | Long-term memory: durable facts, preferences, decisions. Curated to ~100 lines of "what matters most." Always loaded. |
| `HEARTBEAT.md` | Checklist read on each heartbeat. Agent decides if any item requires action; sends message or responds `HEARTBEAT_OK` (silently dropped). |
| `memory/YYYY-MM-DD.md` | Daily log entries — running notes and day-to-day context. |

**Key design principle:** Files are the source of truth. The AI agent only retains what gets written to disk.

### The Memory System

OpenClaw implements a file-based, Markdown-driven memory system with semantic search:

- **Tier 1 (Always Loaded):** `MEMORY.md` — curated ~100 lines of what matters most, in context every conversation
- **Tier 2 (Daily):** `memory/YYYY-MM-DD.md` — day-to-day notes and running context
- **Semantic Index:** SQLite index at `~/.openclaw/memory/{agentId}.sqlite` — chunked embeddings for fast semantic search
- **Search:** Hybrid BM25 + vector search for combining semantic matching with exact keyword lookups
- **Embedding Providers:** OpenAI, Gemini, Voyage, Mistral, Ollama, local GGUF models
- **Memory Philosophy:** Index is derived; markdown files are canonical. The index rebuilds from files.
- **Nightly Consolidation:** Production agents (like Felix) run nightly consolidation jobs to avoid memory bottlenecks — merging daily logs back into MEMORY.md and pruning stale entries.

### The Skills System

Skills are the extension mechanism for OpenClaw agents:

- A **Skill** is a folder containing a `SKILL.md` file with natural language instructions, examples, and optional tool configurations
- Skills live in `.claude/skills/` or the skills directory
- Skills are listed as metadata only (name + description + path) — the model reads the full `SKILL.md` on demand
- **Selective Injection:** OpenClaw only injects skills relevant to the current turn, avoiding context bloat
- **ClawHub:** The public skill registry ([openclaw/clawhub](https://github.com/openclaw/clawhub)) hosts 13,729+ skills, searchable via vector embeddings (OpenAI embeddings backend)
- Skills can include: web browsing, GitHub operations, Telegram integration, code execution, CRM actions, finance tools, etc.

### Automation System

Three mechanisms for autonomous operation:

1. **Cron Jobs** — Scheduled tasks (standard cron syntax), persisted under `~/.openclaw/cron/`. Survive restarts. Run inside the Gateway, not the model. Good for things that must happen at a specific time.

2. **HEARTBEAT.md** — Background awareness. Agent reads its checklist every 30 minutes and acts if needed. Good for monitoring, inbox checks, routine context sweeps.

3. **Webhooks** — Event-driven triggers. External systems POST to the Gateway's webhook endpoint. Good for GitHub push events, payment notifications, API callbacks.

### Multi-Agent / Sub-Agent System

OpenClaw supports spawning sub-agents:

- **Sub-agents** run in isolated sessions: `agent:<agentId>:subagent:<uuid>`
- Spawning is **non-blocking** — returns a run ID immediately
- On completion, sub-agent announces result back to the requester chat channel
- Tool: `sessions_spawn`
- Config: `maxSpawnDepth` (default: 1, allows up to 2 for orchestrator → worker patterns), `maxChildrenPerAgent: 5`, `maxConcurrent: 8`, `runTimeoutSeconds: 900`
- Sub-agents get all tools except session and system tools by default
- Orchestrator sub-agents (depth >= 2) additionally get: `sessions_spawn`, `subagents`, `sessions_list`, `sessions_history`

### Security Model

- **DM pairing:** Unknown senders receive pairing codes
- Can enable "open" policy with explicit allowlist configuration
- `TOOLS.md` documents tool access but config controls actual permissions

### Device Integration

- macOS menu bar apps
- iOS nodes
- Android devices
- Voice wake words, screen recording, camera access
- Canvas visual workspaces

---

## Part 4: felixcraft.ai — The Real-World OpenClaw Showcase

### What Is Felix?

Felix is an OpenClaw agent created by **Nat Eliason** (previously founder of Growth Machine, a content marketing agency acquired in 2025). Felix operates as the "CEO" of The Masinov Company — an AI-operated business with zero human employees besides Nat himself.

- **Twitter/X:** [@FelixCraftAI](https://x.com/FelixCraftAI)
- **Website:** [felixcraft.ai](https://felixcraft.ai)
- **Infrastructure:** Felix runs on a Mac Mini

### Business Performance (as of March 2026)

Felix has generated ~$80,000+ in lifetime revenue across four product lines:

| Product | Model | Revenue |
|---------|-------|---------|
| "How to Hire an AI" PDF guide (66 pages, $29) | One-time purchase | ~$41,000 |
| Claw Mart (AI skills marketplace, 10% commission + $20/mo creator sub) | Marketplace | Ongoing |
| Clawcommerce (custom OpenClaw deployment service, $2,000 setup + $500/mo) | Services | Ongoing |
| Email support from Felix | Bundled with PDF | Value-add |

- **Total startup cost:** ~$1,500
- **Ongoing cost:** ~$400/month (two Claude Max subscriptions + minor hosting)
- **Revenue milestone:** ~$80K in a few weeks; target is $1M ARR

### How Felix Works

1. Nat sends Felix voice notes or text messages (Telegram or direct)
2. Felix interprets, plans, and executes: finds code repos, makes changes, deploys to live sites, handles customer service, manages sub-agents
3. Sub-agents: **Iris** (customer support — refunds, inquiries), **Remy** (sales leads management)
4. Felix autonomously: built a website overnight, integrated Stripe, launched Claw Mart marketplace, handles all customer email via Iris
5. Memory management: custom nightly consolidation to avoid bottlenecks

### Key Product: "How to Hire an AI"

A 66-page playbook ([felixcraft.ai/dl/...](https://felixcraft.ai/dl/c5768e3409026bab01bb1649.pdf)) teaching users how to set up OpenClaw agents with real jobs. Purchasers get ongoing email support from Felix directly.

---

## Part 5: OpenClaw Ecosystem

### ClawHub (Skill Registry)

[github.com/openclaw/clawhub](https://github.com/openclaw/clawhub)

- **Tech stack:** TanStack Start (React), Convex (backend/database), GitHub OAuth, OpenAI embeddings for vector search
- **Skill categories:** Coding, productivity, communication, finance/investing (311+ skills), web browsing (180k+ installs), Telegram integration (145k+ installs), real estate, legal docs, and many more
- **Companion:** [onlycrabs.ai](https://onlycrabs.ai) — the `SOUL.md` registry for sharing agent personalities

### Notable Community Projects

- [awesome-openclaw-skills](https://github.com/VoltAgent/awesome-openclaw-skills) — 5,400+ curated skills
- [openclaw-workspace](https://github.com/win4r/openclaw-workspace) — Claude Code skill for maintaining workspace files
- [openclaw-agents](https://github.com/shenhao-stu/openclaw-agents) — One-command multi-agent setup (9 specialized agents)
- [openclaw-mission-control](https://github.com/abhi1693/openclaw-mission-control) — AI agent orchestration dashboard
- [soul.md](https://github.com/aaronjmars/soul.md) — Tool for building agent personalities from user data

---

## Part 6: OpenClaw's Core Philosophy (Synthesis)

After all research, OpenClaw's fundamental philosophy can be summarized as:

1. **Local-first, file-first:** Agent identity, memory, and behavior live in plain text files. No databases, no admin panels. Files are canonical; indices are derived.

2. **Messaging-native:** The agent lives where you already communicate (Telegram, Slack, Discord, etc.), not in a separate app.

3. **Model-agnostic by design:** Works with Claude, OpenAI, Gemini, local models. The framework is the glue, not the model.

4. **Skills as language:** Capabilities are expressed in natural language Markdown, not code APIs. Anyone can write a skill; anyone can share one.

5. **Autonomous by default:** Heartbeats, cron, webhooks — the agent acts without being asked, within defined parameters.

6. **Minimal infrastructure:** A Mac Mini + two Claude subscriptions = a $80K business. The philosophy is radical simplicity at the infrastructure level.

7. **Real jobs for AI:** The vision is not AI as a chatbot but AI as an employee with a job, a budget, a history, and accountability.

---

*Sources: [OpenClaw GitHub](https://github.com/openclaw/openclaw) · [felixcraft.ai](https://felixcraft.ai) · [ClawHub](https://github.com/openclaw/clawhub) · [Fortune profile](https://fortune.com/2026/02/19/openclaw-who-is-peter-steinberger-openai-sam-altman-anthropic-moltbook/) · [Bankless podcast](https://www.bankless.com/podcast/building-a-million-dollar-zero-human-company-with-openclaw-nat-eliason) · [Ryan Sean Adams tweet](https://x.com/RyanSAdams/status/2029198636811264264)*