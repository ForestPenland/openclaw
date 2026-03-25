/**
 * Gateway Configuration Manager
 *
 * Reads channel secrets from AWS Secrets Manager at container startup and
 * writes ~/.openclaw/openclaw.json with the channel configuration block
 * before the Gateway process starts.
 *
 * Graceful degradation:
 *   - If a secret is not found → that channel is omitted from config
 *   - If Secrets Manager is unreachable → minimal config (WebSocket-only)
 *
 * Requirements: 6.1, 6.3, 8.2, 8.3
 */

import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import {
  SecretsManagerClient,
  GetSecretValueCommand,
  ResourceNotFoundException,
} from "@aws-sdk/client-secrets-manager";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GatewayConfigManagerOptions {
  /** Secrets Manager secret name for the Telegram bot token. */
  telegramSecretName?: string;
  /** Secrets Manager secret name for the Slack bot token. */
  slackBotTokenSecretName?: string;
  /** Secrets Manager secret name for the Slack app token. */
  slackAppTokenSecretName?: string;
  /** Secrets Manager secret name for the Slack signing secret. */
  slackSigningSecretName?: string;
  /** Override the config directory (default: ~/.openclaw). */
  configDir?: string;
  /** Injected SecretsManager client (for testing). */
  secretsClient?: SecretsManagerClient;
}

export interface OpenClawGatewayConfig {
  gateway: {
    controlUi: {
      dangerouslyAllowHostHeaderOriginFallback: boolean;
    };
  };
  agents?: {
    defaults?: {
      model?: string;
    };
  };
  channels?: {
    telegram?: {
      botToken: string;
    };
    slack?: {
      botToken?: string;
      appToken?: string;
      signingSecret?: string;
    };
  };
}

// ---------------------------------------------------------------------------
// Logger
// ---------------------------------------------------------------------------

const logger = {
  info: (msg: string, meta?: Record<string, unknown>) =>
    console.log(JSON.stringify({ level: "info", msg, ...meta })),
  warn: (msg: string, meta?: Record<string, unknown>) =>
    console.warn(JSON.stringify({ level: "warn", msg, ...meta })),
  error: (msg: string, meta?: Record<string, unknown>) =>
    console.error(JSON.stringify({ level: "error", msg, ...meta })),
};

// ---------------------------------------------------------------------------
// Secret retrieval
// ---------------------------------------------------------------------------

/**
 * Retrieve a secret string from Secrets Manager.
 * Returns null if the secret does not exist.
 * Throws on unexpected errors (network, permissions, etc.).
 */
export async function getSecretValue(
  client: SecretsManagerClient,
  secretName: string,
): Promise<string | null> {
  try {
    const response = await client.send(new GetSecretValueCommand({ SecretId: secretName }));
    return response.SecretString ?? null;
  } catch (err) {
    if (err instanceof ResourceNotFoundException) {
      logger.warn("Secret not found — channel will be omitted", { secretName });
      return null;
    }
    throw err;
  }
}

/**
 * Parse a secret value that may be a plain string or a JSON object
 * with a "token" or "botToken" field.
 */
export function parseSecretToken(raw: string): string {
  const trimmed = raw.trim();
  // Try JSON parse first
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed);
      return (parsed.token ?? parsed.botToken ?? parsed.value ?? trimmed).trim();
    } catch {
      // Not valid JSON — use as-is
    }
  }
  return trimmed;
}

// ---------------------------------------------------------------------------
// Config generation
// ---------------------------------------------------------------------------

/**
 * Build the openclaw.json config object from available secrets.
 */
export function buildConfig(secrets: {
  telegramBotToken?: string | null;
  slackBotToken?: string | null;
  slackAppToken?: string | null;
  slackSigningSecret?: string | null;
  /** When "bedrock", set the default model to use the amazon-bedrock provider. */
  provider?: string | null;
  /** Bedrock model ID (e.g. us.anthropic.claude-sonnet-4-20250514-v1:0). */
  bedrockModelId?: string | null;
}): OpenClawGatewayConfig {
  const config: OpenClawGatewayConfig = {
    gateway: {
      controlUi: {
        dangerouslyAllowHostHeaderOriginFallback: true,
      },
    },
  };

  // Register Bedrock as the default model provider when PROVIDER=bedrock
  if (secrets.provider === "bedrock") {
    const modelId = secrets.bedrockModelId ?? "us.anthropic.claude-sonnet-4-6";
    config.agents = {
      defaults: {
        model: `amazon-bedrock/${modelId}`,
      },
    };
  }

  const channels: NonNullable<OpenClawGatewayConfig["channels"]> = {};
  let hasChannels = false;

  if (secrets.telegramBotToken) {
    channels.telegram = { botToken: secrets.telegramBotToken };
    hasChannels = true;
  }

  if (secrets.slackBotToken || secrets.slackAppToken || secrets.slackSigningSecret) {
    channels.slack = {};
    if (secrets.slackBotToken) {
      channels.slack.botToken = secrets.slackBotToken;
    }
    if (secrets.slackAppToken) {
      channels.slack.appToken = secrets.slackAppToken;
    }
    if (secrets.slackSigningSecret) {
      channels.slack.signingSecret = secrets.slackSigningSecret;
    }
    hasChannels = true;
  }

  if (hasChannels) {
    config.channels = channels;
  }

  return config;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

/**
 * Read channel secrets from Secrets Manager and write openclaw.json.
 *
 * Called from docker-entrypoint.sh before the Gateway process starts.
 */
export async function configureGateway(
  opts: GatewayConfigManagerOptions = {},
): Promise<OpenClawGatewayConfig> {
  const configDir = opts.configDir ?? join(homedir(), ".openclaw");
  const configPath = join(configDir, "openclaw.json");

  let config: OpenClawGatewayConfig;

  try {
    const client = opts.secretsClient ?? new SecretsManagerClient({});

    // Fetch secrets in parallel
    const [telegramRaw, slackBotTokenRaw, slackAppTokenRaw, slackSigningSecretRaw] =
      await Promise.all([
        opts.telegramSecretName
          ? getSecretValue(client, opts.telegramSecretName)
          : Promise.resolve(null),
        opts.slackBotTokenSecretName
          ? getSecretValue(client, opts.slackBotTokenSecretName)
          : Promise.resolve(null),
        opts.slackAppTokenSecretName
          ? getSecretValue(client, opts.slackAppTokenSecretName)
          : Promise.resolve(null),
        opts.slackSigningSecretName
          ? getSecretValue(client, opts.slackSigningSecretName)
          : Promise.resolve(null),
      ]);

    config = buildConfig({
      telegramBotToken: telegramRaw ? parseSecretToken(telegramRaw) : null,
      slackBotToken: slackBotTokenRaw ? parseSecretToken(slackBotTokenRaw) : null,
      slackAppToken: slackAppTokenRaw ? parseSecretToken(slackAppTokenRaw) : null,
      slackSigningSecret: slackSigningSecretRaw ? parseSecretToken(slackSigningSecretRaw) : null,
      provider: process.env.PROVIDER ?? null,
      bedrockModelId: process.env.BEDROCK_MODEL_ID ?? null,
    });

    logger.info("Gateway config generated from Secrets Manager", {
      hasTelegram: !!config.channels?.telegram,
      hasSlack: !!config.channels?.slack,
    });
  } catch (err) {
    logger.error("Secrets Manager unreachable — writing minimal config", {
      error: String(err),
    });
    // Graceful fallback: minimal config without channel credentials
    config = buildConfig({});
  }

  // Write config file
  if (!existsSync(configDir)) {
    mkdirSync(configDir, { recursive: true });
  }
  writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");
  logger.info("Wrote gateway config", { path: configPath });

  return config;
}
