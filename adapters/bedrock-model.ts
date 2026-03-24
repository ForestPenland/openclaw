import {
  BedrockRuntimeClient,
  ConverseCommand,
  ConverseStreamCommand,
  ThrottlingException,
  type ContentBlock,
  type ConversationRole,
  type ConverseStreamOutput,
  type Message as BedrockMessage,
  type SystemContentBlock,
} from "@aws-sdk/client-bedrock-runtime";

// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

export interface Message {
  role: "user" | "assistant";
  content: { text: string }[];
}

export interface SystemMessage {
  text: string;
}

export interface InferenceConfig {
  maxTokens?: number;
  temperature?: number;
  topP?: number;
  stopSequences?: string[];
}

export interface GuardrailConfig {
  guardrailIdentifier: string;
  guardrailVersion: string;
}

export interface ConverseInput {
  modelId: string;
  messages: Message[];
  system?: SystemMessage[];
  inferenceConfig?: InferenceConfig;
  guardrailConfig?: GuardrailConfig;
}

export interface ConverseOutput {
  content: string;
  usage: { inputTokens: number; outputTokens: number };
  modelId: string;
  stopReason: string;
}

export interface ConverseStreamChunk {
  type: "contentDelta" | "metadata" | "stop";
  text?: string;
  usage?: { inputTokens: number; outputTokens: number };
  stopReason?: string;
}

export interface ModelAdapter {
  /** Non-streaming invocation via Converse API */
  invoke(params: ConverseInput): Promise<ConverseOutput>;
  /** Streaming invocation via ConverseStream API */
  invokeStream(params: ConverseInput): AsyncIterable<ConverseStreamChunk>;
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export interface BedrockModelConfig {
  guardrailIdentifier: string;
  guardrailVersion: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const RETRY_BASE_MS = 500;
const MAX_RETRIES = 3;

// ---------------------------------------------------------------------------
// Logger (structured JSON, matching s3-workspace.ts pattern)
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Compute delay with jitter for exponential backoff.
 * Jitter is random within [0, delay] range.
 */
export function computeRetryDelay(attempt: number, baseMs: number = RETRY_BASE_MS): number {
  const delay = baseMs * 2 ** attempt;
  const jitter = Math.random() * delay;
  return delay + jitter;
}

function isThrottlingException(err: unknown): boolean {
  return err instanceof ThrottlingException;
}

/**
 * Emit token metrics as structured JSON log (CloudWatch-compatible).
 */
export function emitTokenMetrics(
  inputTokens: number,
  outputTokens: number,
  modelId: string,
): void {
  logger.info("TokenMetrics", {
    InputTokens: inputTokens,
    OutputTokens: outputTokens,
    ModelId: modelId,
  });
}

/**
 * Convert our Message[] to the Bedrock SDK's expected format.
 */
function toBedrockMessages(messages: Message[]): BedrockMessage[] {
  return messages.map((m) => ({
    role: m.role as ConversationRole,
    content: m.content.map((c) => ({ text: c.text }) as ContentBlock),
  }));
}

/**
 * Convert our SystemMessage[] to the Bedrock SDK's expected format.
 */
function toBedrockSystem(system?: SystemMessage[]): SystemContentBlock[] | undefined {
  if (!system || system.length === 0) return undefined;
  return system.map((s) => ({ text: s.text }) as SystemContentBlock);
}

// ---------------------------------------------------------------------------
// Adapter Implementation
// ---------------------------------------------------------------------------

export class BedrockModelAdapter implements ModelAdapter {
  private readonly client: BedrockRuntimeClient;
  private readonly guardrailConfig: GuardrailConfig;

  constructor(config: BedrockModelConfig, client?: BedrockRuntimeClient) {
    this.client = client ?? new BedrockRuntimeClient({});
    this.guardrailConfig = {
      guardrailIdentifier: config.guardrailIdentifier,
      guardrailVersion: config.guardrailVersion,
    };
  }

  /**
   * Non-streaming invocation via Converse API.
   * Retries on ThrottlingException with exponential backoff + jitter.
   */
  async invoke(params: ConverseInput): Promise<ConverseOutput> {
    const command = new ConverseCommand({
      modelId: params.modelId,
      messages: toBedrockMessages(params.messages),
      system: toBedrockSystem(params.system),
      inferenceConfig: params.inferenceConfig,
      guardrailConfig: params.guardrailConfig ?? this.guardrailConfig,
    });

    let lastError: unknown;
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const response = await this.client.send(command);

        const content =
          response.output?.message?.content
            ?.map((block) => ("text" in block ? block.text : ""))
            .join("") ?? "";

        const usage = {
          inputTokens: response.usage?.inputTokens ?? 0,
          outputTokens: response.usage?.outputTokens ?? 0,
        };

        emitTokenMetrics(usage.inputTokens, usage.outputTokens, params.modelId);

        return {
          content,
          usage,
          modelId: params.modelId,
          stopReason: response.stopReason ?? "end_turn",
        };
      } catch (err) {
        lastError = err;
        if (isThrottlingException(err) && attempt < MAX_RETRIES) {
          const delayMs = computeRetryDelay(attempt);
          logger.warn(`ThrottlingException retry ${attempt + 1}/${MAX_RETRIES}`, {
            modelId: params.modelId,
            delayMs,
          });
          await sleep(delayMs);
          continue;
        }
        throw err;
      }
    }
    // Should not reach here, but satisfy TypeScript
    throw lastError;
  }

  /**
   * Streaming invocation via ConverseStream API.
   * Retries on ThrottlingException with exponential backoff + jitter.
   * Yields chunks as they arrive.
   */
  async *invokeStream(params: ConverseInput): AsyncIterable<ConverseStreamChunk> {
    const command = new ConverseStreamCommand({
      modelId: params.modelId,
      messages: toBedrockMessages(params.messages),
      system: toBedrockSystem(params.system),
      inferenceConfig: params.inferenceConfig,
      guardrailConfig: params.guardrailConfig ?? this.guardrailConfig,
    });

    let lastError: unknown;
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const response = await this.client.send(command);
        const stream = response.stream;
        if (!stream) {
          throw new Error("ConverseStream returned no stream");
        }

        let totalInputTokens = 0;
        let totalOutputTokens = 0;

        for await (const event of stream as AsyncIterable<ConverseStreamOutput>) {
          if (event.contentBlockDelta?.delta && "text" in event.contentBlockDelta.delta) {
            yield {
              type: "contentDelta",
              text: event.contentBlockDelta.delta.text,
            };
          }

          if (event.metadata?.usage) {
            totalInputTokens = event.metadata.usage.inputTokens ?? 0;
            totalOutputTokens = event.metadata.usage.outputTokens ?? 0;
            yield {
              type: "metadata",
              usage: {
                inputTokens: totalInputTokens,
                outputTokens: totalOutputTokens,
              },
            };
          }

          if (event.messageStop) {
            yield {
              type: "stop",
              stopReason: event.messageStop.stopReason ?? "end_turn",
            };
          }
        }

        emitTokenMetrics(totalInputTokens, totalOutputTokens, params.modelId);
        return; // Successfully streamed — exit retry loop
      } catch (err) {
        lastError = err;
        if (isThrottlingException(err) && attempt < MAX_RETRIES) {
          const delayMs = computeRetryDelay(attempt);
          logger.warn(`ThrottlingException retry ${attempt + 1}/${MAX_RETRIES} (stream)`, {
            modelId: params.modelId,
            delayMs,
          });
          await sleep(delayMs);
          continue;
        }
        throw err;
      }
    }
    throw lastError;
  }
}
