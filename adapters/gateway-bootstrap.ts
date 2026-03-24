/**
 * Gateway bootstrap / wiring layer.
 *
 * Reads the PROVIDER env var and assembles the correct adapter graph:
 *   - "bedrock"   → S3 Workspace + Bedrock Model (with CircuitBreaker) + Model Router + AgentCore Memory
 *   - "anthropic"  → native OpenClaw local paths (no AWS adapters)
 *
 * Requirements: 2.7, 3.5, 17.1, 19.1
 */

import { S3WorkspaceAdapter, type S3WorkspaceConfig } from "./s3-workspace.js";
import { BedrockModelAdapter, type BedrockModelConfig, type ModelAdapter } from "./bedrock-model.js";
import { CircuitBreaker } from "./circuit-breaker.js";
import { DefaultModelRouter, type ModelRouter } from "./model-router.js";
import { assembleSystemPrompt } from "./workspace-assembly.js";
import { StructuredLogger } from "./structured-logger.js";
import { SupervisorAgent, type SupervisorConfig } from "../agents/supervisor.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type Provider = "bedrock" | "anthropic";

export interface BootstrapConfig {
  /** Override the PROVIDER env var. */
  provider?: Provider;
  /** Override WORKSPACE_BUCKET env var. */
  workspaceBucket?: string;
  /** Override TENANT_ID env var. */
  tenantId?: string;
  /** Override AGENT_ID env var. */
  agentId?: string;
  /** Override MEMORY_STORE_ID env var. */
  memoryStoreId?: string;
  /** Override AWS_REGION env var. */
  region?: string;
  /** Local filesystem path for workspace file cache. */
  localWorkspacePath?: string;
  /** Bedrock guardrail identifier. */
  guardrailIdentifier?: string;
  /** Bedrock guardrail version. */
  guardrailVersion?: string;
}

export interface GatewayContext {
  provider: Provider;
  logger: StructuredLogger;
  modelAdapter: ModelAdapter | null;
  modelRouter: ModelRouter | null;
  workspaceAdapter: S3WorkspaceAdapter | null;
  supervisor: SupervisorAgent;
  systemPrompt: string;
  circuitBreaker: CircuitBreaker | null;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function env(key: string, fallback?: string): string {
  const value = process.env[key] ?? fallback;
  if (value === undefined) {
    throw new Error(`Missing required environment variable: ${key}`);
  }
  return value;
}

// ---------------------------------------------------------------------------
// Circuit-breaker-wrapped model adapter
// ---------------------------------------------------------------------------

/**
 * Wraps a ModelAdapter so every `invoke` call passes through a CircuitBreaker.
 * Streaming calls are left unwrapped (circuit breaker triggers on the initial
 * send, which is already retried inside BedrockModelAdapter).
 */
class CircuitBreakerModelAdapter implements ModelAdapter {
  constructor(
    private readonly inner: BedrockModelAdapter,
    private readonly cb: CircuitBreaker,
  ) {}

  invoke(
    ...args: Parameters<ModelAdapter["invoke"]>
  ): ReturnType<ModelAdapter["invoke"]> {
    return this.cb.execute(() => this.inner.invoke(...args));
  }

  invokeStream(
    ...args: Parameters<ModelAdapter["invokeStream"]>
  ): ReturnType<ModelAdapter["invokeStream"]> {
    // Streaming is not wrapped — the initial send is retried internally.
    return this.inner.invokeStream(...args);
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

/**
 * Wire all adapters together and return a ready-to-use GatewayContext.
 *
 * When `provider === "bedrock"`:
 *   1. Create S3WorkspaceAdapter, sync workspace files
 *   2. Create BedrockModelAdapter wrapped in a CircuitBreaker
 *   3. Create DefaultModelRouter
 *   4. Assemble system prompt from workspace files
 *   5. Create SupervisorAgent with the wired adapters
 *
 * When `provider === "anthropic"`:
 *   Skip all AWS adapters. The caller is expected to use OpenClaw's native
 *   local paths (Requirement 2.7, 19.1).
 */
export async function bootstrapGateway(
  config: BootstrapConfig = {},
): Promise<GatewayContext> {
  const provider: Provider =
    (config.provider ?? process.env.PROVIDER ?? "bedrock") as Provider;

  const agentId = config.agentId ?? env("AGENT_ID", "default-agent");
  const logger = new StructuredLogger(agentId);

  logger.info("Bootstrapping gateway", {
    sessionId: "startup",
    channel: "system",
    responseLatency: 0,
    provider,
  });

  // ----- anthropic (local) mode -----
  if (provider === "anthropic") {
    const supervisor = new SupervisorAgent({
      // In local mode the caller supplies its own model adapter via OpenClaw
      // native paths. We create a minimal stub so the type is satisfied.
      modelAdapter: null as unknown as ModelAdapter,
      modelRouter: null as unknown as ModelRouter,
      systemPrompt: "",
    });

    logger.info("Gateway bootstrapped in local (anthropic) mode", {
      sessionId: "startup",
      channel: "system",
      responseLatency: 0,
    });

    return {
      provider,
      logger,
      modelAdapter: null,
      modelRouter: null,
      workspaceAdapter: null,
      supervisor,
      systemPrompt: "",
      circuitBreaker: null,
    };
  }

  // ----- bedrock mode -----
  const bucket = config.workspaceBucket ?? env("WORKSPACE_BUCKET");
  const tenantId = config.tenantId ?? env("TENANT_ID");
  const localPath = config.localWorkspacePath ?? "/tmp/workspace";

  // 1. S3 Workspace
  const workspaceConfig: S3WorkspaceConfig = {
    bucket,
    tenantId,
    agentId,
    localPath,
  };
  const workspaceAdapter = new S3WorkspaceAdapter(workspaceConfig);

  logger.info("Syncing workspace from S3", {
    sessionId: "startup",
    channel: "system",
    responseLatency: 0,
    bucket,
    tenantId,
    agentId,
  });

  const prefix = `${tenantId}/${agentId}/`;
  await workspaceAdapter.syncFromS3(bucket, prefix, localPath);

  // 2. Assemble system prompt (Requirement 17.1)
  const systemPrompt = await assembleSystemPrompt(workspaceAdapter);

  // 3. Bedrock Model Adapter + CircuitBreaker
  const bedrockConfig: BedrockModelConfig = {
    guardrailIdentifier: config.guardrailIdentifier ?? env("GUARDRAIL_ID", ""),
    guardrailVersion: config.guardrailVersion ?? env("GUARDRAIL_VERSION", "1"),
  };
  const rawModelAdapter = new BedrockModelAdapter(bedrockConfig);
  const circuitBreaker = new CircuitBreaker();
  const modelAdapter = new CircuitBreakerModelAdapter(rawModelAdapter, circuitBreaker);

  // 4. Model Router
  const modelRouter = new DefaultModelRouter();

  // 5. Supervisor Agent
  const supervisorConfig: SupervisorConfig = {
    modelAdapter,
    modelRouter,
    systemPrompt,
  };
  const supervisor = new SupervisorAgent(supervisorConfig);

  logger.info("Gateway bootstrapped in bedrock mode", {
    sessionId: "startup",
    channel: "system",
    responseLatency: 0,
  });

  return {
    provider,
    logger,
    modelAdapter,
    modelRouter,
    workspaceAdapter,
    supervisor,
    systemPrompt,
    circuitBreaker,
  };
}
