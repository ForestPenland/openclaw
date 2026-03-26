#!/usr/bin/env node
/**
 * MCP Gateway Bridge — stdio-to-HTTP proxy for AgentCore Gateway.
 *
 * Runs as a stdio MCP server that OpenClaw can spawn as a child process.
 * Proxies all MCP requests to the AgentCore Gateway's HTTP endpoint,
 * handling OAuth2 client credentials token exchange with Cognito.
 *
 * Environment variables:
 *   GATEWAY_MCP_URL       — AgentCore Gateway MCP endpoint URL
 *   GATEWAY_CLIENT_ID     — Cognito client ID
 *   GATEWAY_CLIENT_SECRET — Cognito client secret
 *   GATEWAY_TOKEN_URL     — Cognito token endpoint
 *   GATEWAY_SCOPE         — OAuth scope (e.g., "OpenClawToolGateway/invoke")
 */

import { createInterface } from "node:readline";

const GATEWAY_MCP_URL = process.env.GATEWAY_MCP_URL;
const CLIENT_ID = process.env.GATEWAY_CLIENT_ID;
const CLIENT_SECRET = process.env.GATEWAY_CLIENT_SECRET;
const TOKEN_URL = process.env.GATEWAY_TOKEN_URL;
const SCOPE = process.env.GATEWAY_SCOPE || "";

if (!GATEWAY_MCP_URL || !CLIENT_ID || !CLIENT_SECRET || !TOKEN_URL) {
  process.stderr.write(
    "ERROR: Missing required env vars: GATEWAY_MCP_URL, GATEWAY_CLIENT_ID, GATEWAY_CLIENT_SECRET, GATEWAY_TOKEN_URL\n"
  );
  process.exit(1);
}

// Token cache
let cachedToken = null;
let tokenExpiresAt = 0;

async function getOAuthToken() {
  const now = Date.now();
  if (cachedToken && now < tokenExpiresAt) {
    return cachedToken;
  }

  const body = new URLSearchParams({
    grant_type: "client_credentials",
    client_id: CLIENT_ID,
    client_secret: CLIENT_SECRET,
  });
  if (SCOPE) {
    body.set("scope", SCOPE);
  }

  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`OAuth token request failed: ${response.status} ${text}`);
  }

  const data = await response.json();
  cachedToken = data.access_token;
  // Cache with 5-minute buffer
  const expiresIn = (data.expires_in || 3600) - 300;
  tokenExpiresAt = now + expiresIn * 1000;

  process.stderr.write(`[mcp-bridge] OAuth token acquired (expires in ${expiresIn}s)\n`);
  return cachedToken;
}

/**
 * Forward a JSON-RPC request to the Gateway and return the response.
 */
async function forwardToGateway(jsonRpcRequest) {
  const token = await getOAuthToken();

  const response = await fetch(GATEWAY_MCP_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify(jsonRpcRequest),
  });

  if (!response.ok) {
    const text = await response.text();
    process.stderr.write(`[mcp-bridge] Gateway error: ${response.status} ${text}\n`);
    return {
      jsonrpc: "2.0",
      id: jsonRpcRequest.id,
      error: {
        code: -32603,
        message: `Gateway returned ${response.status}: ${text.slice(0, 200)}`,
      },
    };
  }

  const contentType = response.headers.get("content-type") || "";

  // Handle SSE streaming response
  if (contentType.includes("text/event-stream")) {
    const text = await response.text();
    // Parse SSE events — find the last JSON-RPC response
    const lines = text.split("\n");
    let lastData = null;
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        lastData = line.slice(6);
      }
    }
    if (lastData) {
      try {
        return JSON.parse(lastData);
      } catch {
        return {
          jsonrpc: "2.0",
          id: jsonRpcRequest.id,
          result: { content: [{ type: "text", text: lastData }] },
        };
      }
    }
  }

  // Handle plain JSON response
  const responseText = await response.text();
  try {
    return JSON.parse(responseText);
  } catch {
    return {
      jsonrpc: "2.0",
      id: jsonRpcRequest.id,
      result: { content: [{ type: "text", text: responseText }] },
    };
  }
}

/**
 * Handle the MCP initialize handshake locally (the Gateway doesn't
 * need initialization — it's stateless HTTP).
 */
function handleInitialize(request) {
  return {
    jsonrpc: "2.0",
    id: request.id,
    result: {
      protocolVersion: "2024-11-05",
      capabilities: {
        tools: {},
      },
      serverInfo: {
        name: "agentcore-gateway-bridge",
        version: "1.0.0",
      },
    },
  };
}

function handleInitialized() {
  // No response needed for notifications
  return null;
}

// --- Main stdio loop ---

const rl = createInterface({ input: process.stdin });

process.stderr.write(`[mcp-bridge] Starting — Gateway: ${GATEWAY_MCP_URL}\n`);

rl.on("line", async (line) => {
  if (!line.trim()) return;

  let request;
  try {
    request = JSON.parse(line);
  } catch {
    process.stderr.write(`[mcp-bridge] Invalid JSON: ${line.slice(0, 100)}\n`);
    return;
  }

  let response;

  try {
    if (request.method === "initialize") {
      response = handleInitialize(request);
    } else if (request.method === "notifications/initialized") {
      response = handleInitialized();
    } else {
      // Forward everything else to the Gateway
      response = await forwardToGateway(request);
    }
  } catch (err) {
    process.stderr.write(`[mcp-bridge] Error: ${err.message}\n`);
    response = {
      jsonrpc: "2.0",
      id: request.id,
      error: { code: -32603, message: err.message },
    };
  }

  if (response) {
    process.stdout.write(JSON.stringify(response) + "\n");
  }
});

rl.on("close", () => {
  process.stderr.write("[mcp-bridge] stdin closed, exiting\n");
  process.exit(0);
});
