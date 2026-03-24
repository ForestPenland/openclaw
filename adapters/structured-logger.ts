/**
 * Structured JSON logger for the Gateway container.
 * Emits JSON logs to stdout (captured by CloudWatch).
 */

export interface LogEntry {
  sessionId: string;
  agentId: string;
  channel: string;
  responseLatency: number;
  timestamp: string;
  level: string;
  message: string;
  [key: string]: unknown;
}

export interface LogRequestParams {
  sessionId: string;
  agentId: string;
  channel: string;
  responseLatency: number;
  message?: string;
  extra?: Record<string, unknown>;
}

/**
 * Create a structured log entry with required fields.
 */
export function logRequest(params: LogRequestParams): LogEntry {
  return {
    timestamp: new Date().toISOString(),
    level: "info",
    message: params.message ?? "request",
    sessionId: params.sessionId,
    agentId: params.agentId,
    channel: params.channel,
    responseLatency: params.responseLatency,
    ...params.extra,
  };
}

/**
 * Structured logger that emits JSON to stdout for CloudWatch ingestion.
 */
export class StructuredLogger {
  private readonly agentId: string;

  constructor(agentId: string) {
    this.agentId = agentId;
  }

  /**
   * Log a request with required structured fields.
   */
  request(params: Omit<LogRequestParams, "agentId">): void {
    const entry = logRequest({ ...params, agentId: this.agentId });
    console.log(JSON.stringify(entry));
  }

  /**
   * Log an info-level message with structured fields.
   */
  info(message: string, params: Omit<LogRequestParams, "agentId" | "message">): void {
    const entry = logRequest({ ...params, agentId: this.agentId, message });
    console.log(JSON.stringify(entry));
  }

  /**
   * Log an error-level message with structured fields.
   */
  error(message: string, params: Omit<LogRequestParams, "agentId" | "message">, error?: unknown): void {
    const entry = logRequest({ ...params, agentId: this.agentId, message });
    entry.level = "error";
    if (error) {
      entry.error = String(error);
    }
    console.error(JSON.stringify(entry));
  }
}
