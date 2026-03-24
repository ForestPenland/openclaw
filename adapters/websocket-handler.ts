import {
  DynamoDBClient,
  type DynamoDBClientConfig,
} from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  PutCommand,
  DeleteCommand,
} from "@aws-sdk/lib-dynamodb";
import {
  ApiGatewayManagementApiClient,
  PostToConnectionCommand,
  GoneException,
} from "@aws-sdk/client-apigatewaymanagementapi";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Callback to route a message to the Supervisor Agent and get a streaming response. */
export type ProcessMessageStream = (
  text: string,
  userId: string,
  channel: "web",
) => AsyncIterable<string>;

/** Callback to route a message to the Supervisor Agent and get a full response. */
export type ProcessMessage = (
  text: string,
  userId: string,
  channel: "web",
) => Promise<string>;

export interface WebSocketHandlerConfig {
  /** DynamoDB table name for connection state. */
  connectionsTable: string;
  /** API Gateway Management API endpoint URL (e.g. `https://{api-id}.execute-api.{region}.amazonaws.com/{stage}`). */
  apiGatewayEndpoint: string;
  /** Route text to the Supervisor Agent (streaming). */
  processMessageStream?: ProcessMessageStream;
  /** Route text to the Supervisor Agent (non-streaming fallback). */
  processMessage?: ProcessMessage;
  /** Optional DynamoDB client config (injected for testing). */
  dynamoClientConfig?: DynamoDBClientConfig;
  /** Optional pre-built DynamoDB document client (injected for testing). */
  docClient?: DynamoDBDocumentClient;
  /** Optional pre-built API Gateway Management API client (injected for testing). */
  apiGwClient?: ApiGatewayManagementApiClient;
}

export interface ConnectEvent {
  connectionId: string;
  userId?: string;
  agentId?: string;
}

export interface MessageEvent {
  connectionId: string;
  body: string;
}

export interface DisconnectEvent {
  connectionId: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const CONNECTION_TTL_SECONDS = 3600; // 1 hour

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
// WebSocketHandler
// ---------------------------------------------------------------------------

export class WebSocketHandler {
  private readonly config: WebSocketHandlerConfig;
  private readonly docClient: DynamoDBDocumentClient;
  private readonly apiGwClient: ApiGatewayManagementApiClient;

  constructor(config: WebSocketHandlerConfig) {
    this.config = config;

    this.docClient =
      config.docClient ??
      DynamoDBDocumentClient.from(
        new DynamoDBClient(config.dynamoClientConfig ?? {}),
      );

    this.apiGwClient =
      config.apiGwClient ??
      new ApiGatewayManagementApiClient({
        endpoint: config.apiGatewayEndpoint,
      });
  }

  /**
   * Handle a new WebSocket connection.
   * Stores connection state in DynamoDB with a 1-hour TTL.
   */
  async onConnect(event: ConnectEvent): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    const ttl = now + CONNECTION_TTL_SECONDS;

    try {
      await this.docClient.send(
        new PutCommand({
          TableName: this.config.connectionsTable,
          Item: {
            connection_id: event.connectionId,
            agent_id: event.agentId ?? "default",
            user_id: event.userId ?? "anonymous",
            connected_at: Date.now(),
            ttl,
          },
        }),
      );
      logger.info("WebSocket connected", {
        connectionId: event.connectionId,
        ttl,
      });
    } catch (err) {
      logger.error("Failed to store connection state", {
        connectionId: event.connectionId,
        error: String(err),
      });
      throw err;
    }
  }

  /**
   * Handle an incoming WebSocket message.
   * Routes to the Supervisor Agent and streams response chunks back to the client.
   */
  async onMessage(event: MessageEvent): Promise<void> {
    let parsed: { text?: string; userId?: string };
    try {
      parsed = JSON.parse(event.body) as { text?: string; userId?: string };
    } catch {
      await this.postToConnection(event.connectionId, JSON.stringify({ error: "Invalid JSON" }));
      return;
    }

    const text = parsed.text;
    if (!text) {
      await this.postToConnection(event.connectionId, JSON.stringify({ error: "Missing text field" }));
      return;
    }

    const userId = parsed.userId ?? "anonymous";

    try {
      // Prefer streaming if available
      if (this.config.processMessageStream) {
        for await (const chunk of this.config.processMessageStream(text, userId, "web")) {
          await this.postToConnection(
            event.connectionId,
            JSON.stringify({ type: "chunk", data: chunk }),
          );
        }
      } else if (this.config.processMessage) {
        const response = await this.config.processMessage(text, userId, "web");
        await this.postToConnection(
          event.connectionId,
          JSON.stringify({ type: "response", data: response }),
        );
      }

      // Signal completion
      await this.postToConnection(
        event.connectionId,
        JSON.stringify({ type: "done" }),
      );
    } catch (err) {
      logger.error("Message processing failed", {
        connectionId: event.connectionId,
        error: String(err),
      });
      await this.postToConnection(
        event.connectionId,
        JSON.stringify({ type: "error", error: "Processing failed" }),
      );
    }
  }

  /**
   * Handle a WebSocket disconnection.
   * Cleans up the DynamoDB connection entry.
   */
  async onDisconnect(event: DisconnectEvent): Promise<void> {
    try {
      await this.docClient.send(
        new DeleteCommand({
          TableName: this.config.connectionsTable,
          Key: { connection_id: event.connectionId },
        }),
      );
      logger.info("WebSocket disconnected", {
        connectionId: event.connectionId,
      });
    } catch (err) {
      logger.error("Failed to clean up connection state", {
        connectionId: event.connectionId,
        error: String(err),
      });
    }
  }

  /**
   * Post a message to a WebSocket connection via API Gateway Management API.
   * If the connection is gone (client disconnected), clean up silently.
   */
  private async postToConnection(connectionId: string, data: string): Promise<void> {
    try {
      await this.apiGwClient.send(
        new PostToConnectionCommand({
          ConnectionId: connectionId,
          Data: new TextEncoder().encode(data),
        }),
      );
    } catch (err) {
      if (err instanceof GoneException) {
        logger.warn("Connection gone, cleaning up", { connectionId });
        await this.onDisconnect({ connectionId });
        return;
      }
      throw err;
    }
  }
}
