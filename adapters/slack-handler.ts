import { createHmac, timingSafeEqual } from "node:crypto";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Slack Events API event wrapper. */
export interface SlackEventPayload {
  /** `url_verification` for challenge handshake, `event_callback` for real events. */
  type: string;
  /** Present only for `url_verification` — echo it back to Slack. */
  challenge?: string;
  /** The inner event object (present when `type === "event_callback"`). */
  event?: SlackEvent;
}

export interface SlackEvent {
  type: string;
  text?: string;
  user?: string;
  channel?: string;
  /** Present when the message was sent by a bot. */
  bot_id?: string;
  /** `"bot_message"` when the message originates from a bot integration. */
  subtype?: string;
}

/** Callback to route a message to the Supervisor Agent and get a response. */
export type ProcessMessage = (
  text: string,
  userId: string,
  channel: "slack",
) => Promise<string>;

export interface SlackHandlerConfig {
  /** Slack signing secret used for HMAC-SHA256 verification. */
  signingSecret: string;
  /** Route text to the Supervisor Agent. */
  processMessage: ProcessMessage;
}

// ---------------------------------------------------------------------------
// Logger (structured JSON, matching other adapters)
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
// Helpers
// ---------------------------------------------------------------------------

/**
 * Verify a Slack request signature using HMAC-SHA256 with `v0:timestamp:body`.
 *
 * @param body      - The raw request body string.
 * @param timestamp - The `X-Slack-Request-Timestamp` header value.
 * @param signature - The `X-Slack-Signature` header value (e.g. `v0=abc123…`).
 * @param secret    - The Slack signing secret.
 * @returns `true` when the signature is valid.
 */
export function verifySlackSignature(
  body: string,
  timestamp: string,
  signature: string,
  secret: string,
): boolean {
  if (!timestamp || !signature) {
    return false;
  }
  const sigBasestring = `v0:${timestamp}:${body}`;
  const expected =
    "v0=" +
    createHmac("sha256", secret).update(sigBasestring).digest("hex");

  // Constant-time comparison to prevent timing attacks.
  try {
    return timingSafeEqual(
      Buffer.from(expected, "utf-8"),
      Buffer.from(signature, "utf-8"),
    );
  } catch {
    // Lengths differ — signatures don't match.
    return false;
  }
}

/**
 * Determine whether a Slack event originates from a bot.
 * Exported standalone for testability.
 */
export function isBot(event: SlackEvent): boolean {
  return !!event.bot_id || event.subtype === "bot_message";
}

// ---------------------------------------------------------------------------
// SlackHandler
// ---------------------------------------------------------------------------

export class SlackHandler {
  private readonly config: SlackHandlerConfig;

  constructor(config: SlackHandlerConfig) {
    this.config = config;
  }

  /**
   * Handle an incoming Slack event payload.
   *
   * @param rawBody   - The raw request body string (needed for signature verification).
   * @param timestamp - The `X-Slack-Request-Timestamp` header.
   * @param signature - The `X-Slack-Signature` header.
   * @param payload   - The parsed JSON payload.
   * @returns A response object: `{ statusCode, body }`.
   */
  async handleEvent(
    rawBody: string,
    timestamp: string,
    signature: string,
    payload: SlackEventPayload,
  ): Promise<{ statusCode: number; body: string }> {
    // 1. URL verification challenge — no signature check needed per Slack docs.
    if (payload.type === "url_verification" && payload.challenge) {
      return { statusCode: 200, body: payload.challenge };
    }

    // 2. Verify request signature.
    if (
      !verifySlackSignature(
        rawBody,
        timestamp,
        signature,
        this.config.signingSecret,
      )
    ) {
      logger.warn("Slack signature verification failed", { timestamp });
      return { statusCode: 401, body: "Invalid signature" };
    }

    // 3. Only process event_callback payloads.
    if (payload.type !== "event_callback" || !payload.event) {
      return { statusCode: 200, body: "ok" };
    }

    const event = payload.event;

    // 4. Ignore bot messages to prevent loops.
    if (isBot(event)) {
      logger.info("Ignoring bot message", {
        botId: event.bot_id,
        subtype: event.subtype,
      });
      return { statusCode: 200, body: "ok" };
    }

    // 5. Only handle message events with text.
    if (event.type !== "message" || !event.text) {
      return { statusCode: 200, body: "ok" };
    }

    const userId = event.user ?? "unknown";
    const channel = event.channel ?? "unknown";

    // 6. Route to Supervisor Agent.
    try {
      await this.config.processMessage(event.text, userId, "slack");
      logger.info("Slack message processed", { userId, channel });
    } catch (err) {
      logger.error("Supervisor Agent processing failed", {
        userId,
        channel,
        error: String(err),
      });
    }

    return { statusCode: 200, body: "ok" };
  }
}
