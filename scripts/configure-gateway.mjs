#!/usr/bin/env node
/**
 * Gateway Configuration Manager — standalone Node.js script.
 *
 * Reads channel secrets from AWS Secrets Manager and writes
 * ~/.openclaw/openclaw.json before the Gateway process starts.
 *
 * Environment variables:
 *   TELEGRAM_SECRET_NAME  — Secrets Manager secret name for Telegram bot token
 *   SLACK_BOT_TOKEN_SECRET_NAME — (optional) Slack bot token secret
 *   SLACK_APP_TOKEN_SECRET_NAME — (optional) Slack app token secret
 *   SLACK_SIGNING_SECRET_NAME   — (optional) Slack signing secret
 *   OPENCLAW_CONFIG_DIR         — Override config directory (default: ~/.openclaw)
 *
 * Exit codes:
 *   0 — config written successfully (even if some secrets were missing)
 *   1 — unexpected fatal error
 */

import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { SecretsManagerClient, GetSecretValueCommand } from "@aws-sdk/client-secrets-manager";

const log = (level, msg, meta = {}) =>
  console[level === "error" ? "error" : level === "warn" ? "warn" : "log"](
    JSON.stringify({ level, msg, ...meta }),
  );

async function getSecretValue(client, secretName) {
  try {
    const response = await client.send(new GetSecretValueCommand({ SecretId: secretName }));
    return response.SecretString ?? null;
  } catch (err) {
    if (err.name === "ResourceNotFoundException") {
      log("warn", "Secret not found — channel will be omitted", { secretName });
      return null;
    }
    throw err;
  }
}

function parseSecretToken(raw) {
  const trimmed = raw.trim();
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

async function main() {
  const configDir = process.env.OPENCLAW_CONFIG_DIR || join(homedir(), ".openclaw");
  const configPath = join(configDir, "openclaw.json");

  const telegramSecretName = process.env.TELEGRAM_SECRET_NAME || "";
  const slackBotTokenSecretName = process.env.SLACK_BOT_TOKEN_SECRET_NAME || "";
  const slackAppTokenSecretName = process.env.SLACK_APP_TOKEN_SECRET_NAME || "";
  const slackSigningSecretName = process.env.SLACK_SIGNING_SECRET_NAME || "";
  const provider = process.env.PROVIDER || "";
  const bedrockModelId = process.env.BEDROCK_MODEL_ID || "";

  // Base config — always present
  const config = {
    gateway: {
      controlUi: {
        dangerouslyAllowHostHeaderOriginFallback: true,
      },
    },
  };

  // Register Bedrock as the default model provider when PROVIDER=bedrock
  if (provider === "bedrock") {
    const modelId = bedrockModelId || "amazon.nova-lite-v1:0";
    config.agents = {
      defaults: {
        model: `amazon-bedrock/${modelId}`,
      },
    };
  }

  try {
    const client = new SecretsManagerClient({});
    const channels = {};
    let hasChannels = false;

    // Telegram
    if (telegramSecretName) {
      const raw = await getSecretValue(client, telegramSecretName);
      if (raw) {
        const token = parseSecretToken(raw);
        if (token) {
          channels.telegram = { botToken: token, dmPolicy: "open", allowFrom: ["*"] };
          hasChannels = true;
        }
      }
    }

    // Slack
    const slackConfig = {};
    if (slackBotTokenSecretName) {
      const raw = await getSecretValue(client, slackBotTokenSecretName);
      if (raw) {
        slackConfig.botToken = parseSecretToken(raw);
      }
    }
    if (slackAppTokenSecretName) {
      const raw = await getSecretValue(client, slackAppTokenSecretName);
      if (raw) {
        slackConfig.appToken = parseSecretToken(raw);
      }
    }
    if (slackSigningSecretName) {
      const raw = await getSecretValue(client, slackSigningSecretName);
      if (raw) {
        slackConfig.signingSecret = parseSecretToken(raw);
      }
    }
    if (Object.keys(slackConfig).length > 0) {
      channels.slack = slackConfig;
      hasChannels = true;
    }

    if (hasChannels) {
      config.channels = channels;
    }

    log("info", "Gateway config generated from Secrets Manager", {
      hasTelegram: !!channels.telegram,
      hasSlack: !!channels.slack,
    });
  } catch (err) {
    log("error", "Secrets Manager unreachable — writing minimal config", {
      error: String(err),
    });
    // Graceful fallback: minimal config without channel credentials
  }

  // Write config
  if (!existsSync(configDir)) {
    mkdirSync(configDir, { recursive: true });
  }
  writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");
  log("info", "Wrote gateway config", { path: configPath });
}

main().catch((err) => {
  log("error", "Fatal error in configure-gateway", { error: String(err) });
  process.exit(1);
});
