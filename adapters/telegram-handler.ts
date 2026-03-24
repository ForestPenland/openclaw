import {
  TranscribeClient,
  StartTranscriptionJobCommand,
  GetTranscriptionJobCommand,
} from "@aws-sdk/client-transcribe";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Subset of a Telegram Update object relevant to message handling. */
export interface TelegramUpdate {
  update_id: number;
  message?: TelegramMessage;
}

export interface TelegramMessage {
  message_id: number;
  from?: { id: number; username?: string };
  chat: { id: number };
  text?: string;
  voice?: { file_id: string; duration: number; mime_type?: string };
}

/** Callback to check whether a Telegram user ID is authorized. */
export type AuthorizationCheck = (userId: number) => Promise<boolean>;

/** Callback to route a message to the Supervisor Agent and get a response. */
export type ProcessMessage = (text: string, userId: string, channel: "telegram") => Promise<string>;

/** Callback to send a text message to a Telegram chat. */
export type SendTelegramMessage = (chatId: number, text: string) => Promise<void>;

/** Callback to download a Telegram voice file and return the audio buffer + S3 URI. */
export type DownloadVoiceFile = (fileId: string) => Promise<{ s3Uri: string; mediaFormat: string }>;

export interface TelegramHandlerConfig {
  /** Check if a user is authorized */
  isAuthorized: AuthorizationCheck;
  /** Route text to the Supervisor Agent */
  processMessage: ProcessMessage;
  /** Send a message back to Telegram */
  sendMessage: SendTelegramMessage;
  /** Download a voice file and upload to S3, returning the URI */
  downloadVoice: DownloadVoiceFile;
  /** Optional Transcribe client (injected for testing) */
  transcribeClient?: TranscribeClient;
  /** S3 bucket for transcription output */
  transcriptionOutputBucket: string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const TELEGRAM_MAX_LENGTH = 4096;
const CHUNK_SEND_DELAY_MS = 100;

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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Split a message into chunks of at most `maxLength` characters.
 * Prefers splitting at newline boundaries when possible.
 * The concatenation of all chunks equals the original string.
 *
 * Exported standalone for testability.
 */
export function chunkMessage(text: string, maxLength: number = TELEGRAM_MAX_LENGTH): string[] {
  if (text.length <= maxLength) {
    return [text];
  }

  const chunks: string[] = [];
  let remaining = text;

  while (remaining.length > 0) {
    if (remaining.length <= maxLength) {
      chunks.push(remaining);
      break;
    }

    // Try to find a newline to split at within the allowed range
    const slice = remaining.slice(0, maxLength);
    const lastNewline = slice.lastIndexOf("\n");

    let splitAt: number;
    if (lastNewline > 0) {
      // Split after the newline (include it in the current chunk)
      splitAt = lastNewline + 1;
    } else {
      // No newline found — hard split at maxLength
      splitAt = maxLength;
    }

    chunks.push(remaining.slice(0, splitAt));
    remaining = remaining.slice(splitAt);
  }

  return chunks;
}

// ---------------------------------------------------------------------------
// TelegramHandler
// ---------------------------------------------------------------------------

export class TelegramHandler {
  private readonly config: TelegramHandlerConfig;
  private readonly transcribe: TranscribeClient;

  constructor(config: TelegramHandlerConfig) {
    this.config = config;
    this.transcribe = config.transcribeClient ?? new TranscribeClient({});
  }

  /**
   * Handle an incoming Telegram webhook update.
   * Parses the payload, checks authorization, routes text or voice to the
   * Supervisor Agent, and sends the response back (chunked if needed).
   */
  async handleMessage(update: TelegramUpdate): Promise<void> {
    const message = update.message;
    if (!message) {
      logger.warn("Received update without message", { updateId: update.update_id });
      return;
    }

    const userId = message.from?.id;
    const chatId = message.chat.id;

    if (!userId) {
      logger.warn("Message has no sender", { chatId });
      return;
    }

    // Authorization check
    const authorized = await this.config.isAuthorized(userId);
    if (!authorized) {
      logger.warn("Unauthorized user", { userId, chatId });
      return;
    }

    let messageText: string | undefined;

    // Handle voice notes
    if (message.voice) {
      try {
        messageText = await this.transcribeVoice(message.voice.file_id);
      } catch (err) {
        logger.error("Voice transcription failed", {
          fileId: message.voice.file_id,
          error: String(err),
        });
        await this.config.sendMessage(chatId, "Sorry, I couldn't process your voice message.");
        return;
      }
    } else {
      messageText = message.text;
    }

    if (!messageText) {
      logger.warn("No text content to process", { chatId, messageId: message.message_id });
      return;
    }

    // Route to Supervisor Agent
    let response: string;
    try {
      response = await this.config.processMessage(messageText, String(userId), "telegram");
    } catch (err) {
      logger.error("Supervisor Agent processing failed", {
        userId,
        chatId,
        error: String(err),
      });
      await this.config.sendMessage(chatId, "Sorry, something went wrong processing your message.");
      return;
    }

    // Send response (chunked if needed)
    await this.sendResponse(chatId, response);
  }

  /**
   * Send a response to a Telegram chat, splitting into chunks if >4096 chars.
   * Chunks are sent sequentially with a 100ms delay between them.
   */
  async sendResponse(chatId: number, text: string): Promise<void> {
    const chunks = chunkMessage(text);

    for (let i = 0; i < chunks.length; i++) {
      if (i > 0) {
        await sleep(CHUNK_SEND_DELAY_MS);
      }
      await this.config.sendMessage(chatId, chunks[i]);
    }
  }

  /**
   * Download a voice note, transcribe it via Amazon Transcribe, and return the text.
   */
  private async transcribeVoice(fileId: string): Promise<string> {
    // Download voice file and upload to S3
    const { s3Uri, mediaFormat } = await this.config.downloadVoice(fileId);

    const jobName = `openclaw-voice-${fileId}-${Date.now()}`;

    // Start transcription job
    await this.transcribe.send(
      new StartTranscriptionJobCommand({
        TranscriptionJobName: jobName,
        LanguageCode: "en-US",
        Media: { MediaFileUri: s3Uri },
        MediaFormat: mediaFormat as "ogg" | "mp3" | "wav" | "mp4",
        OutputBucketName: this.config.transcriptionOutputBucket,
      }),
    );

    // Poll for completion
    const transcribedText = await this.pollTranscriptionJob(jobName);
    return transcribedText;
  }

  /**
   * Poll Amazon Transcribe until the job completes or fails.
   */
  private async pollTranscriptionJob(jobName: string): Promise<string> {
    const maxAttempts = 60; // ~60 seconds max
    for (let attempt = 0; attempt < maxAttempts; attempt++) {
      const result = await this.transcribe.send(
        new GetTranscriptionJobCommand({ TranscriptionJobName: jobName }),
      );

      const status = result.TranscriptionJob?.TranscriptionJobStatus;

      if (status === "COMPLETED") {
        const transcriptUri = result.TranscriptionJob?.Transcript?.TranscriptFileUri;
        if (!transcriptUri) {
          throw new Error(`Transcription job ${jobName} completed but no transcript URI`);
        }
        // Fetch the transcript JSON from the URI
        const response = await fetch(transcriptUri);
        const data = (await response.json()) as {
          results?: { transcripts?: { transcript?: string }[] };
        };
        const transcript = data.results?.transcripts?.[0]?.transcript;
        if (!transcript) {
          throw new Error(`Transcription job ${jobName} produced empty transcript`);
        }
        return transcript;
      }

      if (status === "FAILED") {
        const reason = result.TranscriptionJob?.FailureReason ?? "unknown";
        throw new Error(`Transcription job ${jobName} failed: ${reason}`);
      }

      // Still in progress — wait 1 second before polling again
      await sleep(1000);
    }

    throw new Error(`Transcription job ${jobName} timed out after ${maxAttempts}s`);
  }
}
