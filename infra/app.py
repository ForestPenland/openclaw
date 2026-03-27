#!/usr/bin/env python3
"""CDK app entry point — wire all OpenClaw stacks together.

Supports: cdk deploy --all
Requirements: 16.1, 16.4, 16.5
"""

import sys
import os

# Ensure the pip-installed ``constructs`` package is importable despite the
# local ``infra/constructs/`` directory sharing the same name.  We
# temporarily remove the current directory from sys.path so that
# ``import constructs`` resolves to the pip package, then restore it.
_cwd = os.path.abspath(os.path.dirname(__file__))
_removed = []
for _p in list(sys.path):
    if os.path.abspath(_p) == _cwd:
        sys.path.remove(_p)
        _removed.append(_p)

import aws_cdk as cdk  # noqa: E402 — must come after path fix

# Restore the original sys.path so local ``stacks`` / ``constructs`` imports
# inside the stack modules continue to work.
for _p in _removed:
    sys.path.insert(0, _p)

from stacks.storage_stack import StorageStack  # noqa: E402
from stacks.gateway_stack import GatewayStack  # noqa: E402
from stacks.identity_stack import IdentityStack  # noqa: E402
from stacks.api_stack import ApiStack  # noqa: E402

from stacks.memory_stack import MemoryStack
from stacks.scheduler_stack import SchedulerStack
from stacks.builder_stack import BuilderStack
from stacks.agentcore_gateway_stack import AgentCoreGatewayStack
from stacks.compute_environments_stack import ComputeEnvironmentsStack

app = cdk.App()

# --- Phase 3 stacks ---

storage = StorageStack(app, "OpenClawStorage")

gateway = GatewayStack(
    app,
    "OpenClawGateway",
    workspace_bucket=storage.workspace_bucket,
    memory_table=storage.memory_table,
    sessions_table=storage.sessions_table,
    agents_table=storage.agents_table,
)
gateway.add_dependency(storage)

identity = IdentityStack(app, "OpenClawIdentity")

# --- Phase 4 stacks ---

api = ApiStack(
    app,
    "OpenClawApi",
    dedup_table=storage.dedup_table,
    telegram_bot_token_secret=identity.telegram_bot_token_secret,
    slack_bot_token_secret=identity.slack_bot_token_secret,
    github_token_secret=identity.github_token_secret,
)
api.add_dependency(storage)
api.add_dependency(identity)

# --- Phase 5–7 stacks ---

memory = MemoryStack(app, "OpenClawMemory")
memory.add_dependency(storage)

scheduler = SchedulerStack(
    app,
    "OpenClawScheduler",
    workspace_bucket=storage.workspace_bucket,
    memory_table=storage.memory_table,
)
scheduler.add_dependency(storage)

builder = BuilderStack(app, "OpenClawBuilder")
builder.add_dependency(storage)

# --- AgentCore Gateway tools ---

agentcore_gw = AgentCoreGatewayStack(app, "OpenClawAgentCoreTools")

# --- Compute Environments (dynamic execution) ---

compute_envs = ComputeEnvironmentsStack(app, "OpenClawComputeEnvironments")

# --- Health Monitoring (opt-in via context flag) ---
# Skip by default to avoid duplicating agent-created health monitoring.
# Enable with: cdk deploy --context deploy_health_stack=true
if app.node.try_get_context("deploy_health_stack") == "true":
    from stacks.health_stack import HealthStack

    health = HealthStack(
        app,
        "OpenClawHealth",
        cluster_name=gateway.cluster.cluster_name,
        service_name=gateway.service.service_name,
    )
    health.add_dependency(gateway)

app.synth()
