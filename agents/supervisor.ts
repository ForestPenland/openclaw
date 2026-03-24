import { SSMClient, GetParameterCommand } from "@aws-sdk/client-ssm";
import { StructuredLogger } from "../adapters/structured-logger.js";
import type { ModelAdapter, ConverseInput, ConverseOutput } from "../adapters/bedrock-model.js";
import type { ModelRouter } from "../adapters/model-router.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type Channel = "telegram" | "slack" | "web" | "heartbeat";

export type SpecialistType = "infrastructure" | "code" | "communications" | "research";

export interface IncomingMessage {
  text: string;
  channel: Channel;
  userId: string;
  sessionId: string;
}

export interface AgentResponse {
  text: string;
  sessionId: string;
  delegatedTo?: SpecialistType;
  handledDirectly: boolean;
}

export interface Session {
  sessionId: string;
  channel: Channel;
  userId: string;
  delegationDepth: number;
}

/** Callback to invoke a specialist sub-agent via AgentCore Runtime. */
export type InvokeSpecialist = (
  specialistType: SpecialistType,
  taskText: string,
  sessionId: string,
) => Promise<string>;

/** Callback to send a proactive Telegram notification. */
export type SendNotification = (chatId: string, text: string) => Promise<void>;

export interface SupervisorConfig {
  /** Bedrock model adapter for direct handling. */
  modelAdapter: ModelAdapter;
  /** Model router for selecting the right model. */
  modelRouter: ModelRouter;
  /** System prompt assembled from workspace files. */
  systemPrompt: string;
  /** Invoke a specialist sub-agent. */
  invokeSpecialist?: InvokeSpecialist;
  /** Send a proactive notification to operator. */
  sendNotification?: SendNotification;
  /** SSM parameter name for operator Telegram chat ID. */
  operatorChatIdParam?: string;
  /** Optional SSM client (injected for testing). */
  ssmClient?: SSMClient;
  /** Maximum specialist retries before fallback. */
  maxSpecialistRetries?: number;
  /** Maximum delegation depth. */
  maxDelegationDepth?: number;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_DELEGATION_DEPTH = 2;
const MAX_SPECIALIST_RETRIES = 2;
/**
 * Patterns used to classify incoming messages for specialist delegation.
 * Exported for testability.
 */
export const SPECIALIST_PATTERNS: Record<SpecialistType, RegExp> = {
  infrastructure: /\b(deploy|provision|infrastructure|cdk|cloudformation|stack|fargate|lambda|s3|ec2|ecs)\b/i,
  code: /\b(write code|generate code|implement|refactor|debug|fix bug|unit test|code review)\b/i,
  communications: /\b(send message|notify|email|slack message|telegram message|broadcast|announce)\b/i,
  research: /\b(research|look up|find out|investigate|analyze|compare|evaluate|summarize article)\b/i,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Generate a session ID for a sub-agent call.
 * Format: `{rootSessionId}-{agentType}-{step}`
 *
 * Exported for testability and property testing.
 */
export function generateSubAgentSessionId(
  rootSessionId: string,
  agentType: SpecialistType,
  step: number,
): string {
  return `${rootSessionId}-${agentType}-${step}`;
}

/**
 * Determine which specialist (if any) should handle a message.
 * Returns `undefined` if the supervisor should handle directly.
 *
 * Exported for testability.
 */
export function classifyMessage(text: string): SpecialistType | undefined {
  for (const [type, pattern] of Object.entries(SPECIALIST_PATTERNS)) {
    if (pattern.test(text)) {
      return type as SpecialistType;
    }
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// SupervisorAgent
// ---------------------------------------------------------------------------

export class SupervisorAgent {
  private readonly config: SupervisorConfig;
  private readonly logger: StructuredLogger;
  private readonly ssmClient: SSMClient;
  private readonly maxRetries: number;
  private readonly maxDepth: number;
  private cachedOperatorChatId: string | undefined;

  constructor(config: SupervisorConfig) {
    this.config = config;
    this.logger = new StructuredLogger("supervisor");
    this.ssmClient = config.ssmClient ?? new SSMClient({});
    this.maxRetries = config.maxSpecialistRetries ?? MAX_SPECIALIST_RETRIES;
    this.maxDepth = config.maxDelegationDepth ?? MAX_DELEGATION_DEPTH;
  }

  /**
   * Process an incoming message.
   * Analyzes the message, delegates to a specialist if appropriate,
   * and falls back to direct handling on failure.
   *
   * Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 12.6
   */
  async process(message: IncomingMessage): Promise<AgentResponse> {
    const startTime = Date.now();
    const session: Session = {
      sessionId: message.sessionId,
      channel: message.channel,
      userId: message.userId,
      delegationDepth: 0,
    };

    this.logger.info("Processing message", {
      sessionId: message.sessionId,
      channel: message.channel,
      responseLatency: 0,
    });

    try {
      // Classify the message to determine if delegation is needed
      const specialistType = classifyMessage(message.text);

      // If no specialist match or no invokeSpecialist callback, handle directly
      if (!specialistType || !this.config.invokeSpecialist) {
        const response = await this.handleDirectly(message, session);
        this.logCompletion(session, startTime, true);
        return response;
      }

      // Attempt delegation (depth check: supervisor=0 → specialist=1, capped at maxDepth)
      if (session.delegationDepth + 1 >= this.maxDepth) {
        this.logger.info("Delegation depth would exceed limit, handling directly", {
          sessionId: session.sessionId,
          channel: session.channel,
          responseLatency: 0,
          currentDepth: session.delegationDepth,
          maxDepth: this.maxDepth,
        });
        const response = await this.handleDirectly(message, session);
        this.logCompletion(session, startTime, true);
        return response;
      }

      // Try specialist delegation with retries
      const delegationResult = await this.delegateToSpecialist(
        specialistType,
        message,
        session,
      );

      this.logCompletion(session, startTime, false, specialistType);
      return delegationResult;
    } catch (err) {
      this.logger.error(
        "Unhandled error in process",
        {
          sessionId: session.sessionId,
          channel: session.channel,
          responseLatency: Date.now() - startTime,
        },
        err,
      );
      throw err;
    }
  }
  /**
   * Delegate a task to a specialist sub-agent.
   * Retries up to maxRetries times; on exhaustion, falls back to direct handling.
   *
   * Requirements: 9.2, 9.3, 9.5
   */
  private async delegateToSpecialist(
    specialistType: SpecialistType,
    message: IncomingMessage,
    session: Session,
  ): Promise<AgentResponse> {
    const invokeSpecialist = this.config.invokeSpecialist!;

    for (let attempt = 1; attempt <= this.maxRetries; attempt++) {
      const subSessionId = generateSubAgentSessionId(
        message.sessionId,
        specialistType,
        attempt,
      );

      this.logger.info(`Delegating to ${specialistType} specialist`, {
        sessionId: session.sessionId,
        channel: session.channel,
        responseLatency: 0,
        attempt,
        subSessionId,
        specialistType,
      });

      try {
        const result = await invokeSpecialist(
          specialistType,
          message.text,
          subSessionId,
        );

        return {
          text: result,
          sessionId: message.sessionId,
          delegatedTo: specialistType,
          handledDirectly: false,
        };
      } catch (err) {
        this.logger.error(
          `Specialist ${specialistType} failed (attempt ${attempt}/${this.maxRetries})`,
          {
            sessionId: session.sessionId,
            channel: session.channel,
            responseLatency: 0,
          },
          err,
        );

        if (attempt === this.maxRetries) {
          // Exhausted retries — fall back to direct handling (Req 9.5)
          this.logger.info(
            `Specialist ${specialistType} exhausted retries, handling directly`,
            {
              sessionId: session.sessionId,
              channel: session.channel,
              responseLatency: 0,
            },
          );
          return this.handleDirectly(message, session);
        }
      }
    }

    // Should not reach here, but satisfy TypeScript
    return this.handleDirectly(message, session);
  }

  /**
   * Handle a message directly using the model adapter.
   *
   * Requirement: 9.1
   */
  private async handleDirectly(
    message: IncomingMessage,
    session: Session,
  ): Promise<AgentResponse> {
    const modelId = this.config.modelRouter.selectModel(message.text, {
      source: message.channel === "heartbeat" ? "heartbeat" : "user",
      charCount: message.text.length,
    });

    const converseInput: ConverseInput = {
      modelId,
      messages: [
        {
          role: "user",
          content: [{ text: message.text }],
        },
      ],
      system: this.config.systemPrompt
        ? [{ text: this.config.systemPrompt }]
        : undefined,
    };

    const output: ConverseOutput = await this.config.modelAdapter.invoke(converseInput);

    return {
      text: output.content,
      sessionId: message.sessionId,
      handledDirectly: true,
    };
  }

  /**
   * Send a proactive notification to the operator via Telegram.
   * Retrieves the operator's chat ID from SSM Parameter Store (cached).
   *
   * Requirement: 12.6
   */
  async notifyOperator(text: string, sessionId: string): Promise<void> {
    if (!this.config.sendNotification) {
      this.logger.info("No notification callback configured, skipping", {
        sessionId,
        channel: "telegram",
        responseLatency: 0,
      });
      return;
    }

    try {
      const chatId = await this.getOperatorChatId();
      if (!chatId) {
        this.logger.error("Operator chat ID not available", {
          sessionId,
          channel: "telegram",
          responseLatency: 0,
        });
        return;
      }

      await this.config.sendNotification(chatId, text);
      this.logger.info("Proactive notification sent", {
        sessionId,
        channel: "telegram",
        responseLatency: 0,
      });
    } catch (err) {
      this.logger.error(
        "Failed to send proactive notification",
        { sessionId, channel: "telegram", responseLatency: 0 },
        err,
      );
    }
  }

  /**
   * Retrieve the operator's Telegram chat ID from SSM Parameter Store.
   * Caches the value after first retrieval.
   */
  private async getOperatorChatId(): Promise<string | undefined> {
    if (this.cachedOperatorChatId) {
      return this.cachedOperatorChatId;
    }

    const paramName =
      this.config.operatorChatIdParam ?? "/openclaw/operator/telegram-chat-id";

    try {
      const result = await this.ssmClient.send(
        new GetParameterCommand({
          Name: paramName,
          WithDecryption: true,
        }),
      );
      this.cachedOperatorChatId = result.Parameter?.Value;
      return this.cachedOperatorChatId;
    } catch (err) {
      this.logger.error(
        "Failed to retrieve operator chat ID from SSM",
        { sessionId: "system", channel: "telegram", responseLatency: 0 },
        err,
      );
      return undefined;
    }
  }

  private logCompletion(
    session: Session,
    startTime: number,
    handledDirectly: boolean,
    delegatedTo?: SpecialistType,
  ): void {
    this.logger.info("Message processing complete", {
      sessionId: session.sessionId,
      channel: session.channel,
      responseLatency: Date.now() - startTime,
      handledDirectly,
      ...(delegatedTo ? { delegatedTo } : {}),
    });
  }
}
