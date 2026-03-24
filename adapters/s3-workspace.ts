import {
  GetObjectCommand,
  ListObjectsV2Command,
  PutObjectCommand,
  S3Client,
} from "@aws-sdk/client-s3";
import { mkdir, readFile as fsReadFile, writeFile as fsWriteFile } from "node:fs/promises";
import path from "node:path";

/**
 * Interface for workspace file operations backed by S3.
 */
export interface WorkspaceAdapter {
  /** Sync all workspace files from S3 to local filesystem on startup */
  syncFromS3(bucket: string, prefix: string, localPath: string): Promise<void>;

  /** Read a workspace file — returns local copy, falls back to S3 */
  readFile(filename: string): Promise<string>;

  /** Write a mutable workspace file to both local FS and S3 within 5 seconds */
  writeFile(filename: string, content: string): Promise<void>;

  /** List all workspace files under the tenant/agent prefix */
  listFiles(prefix: string): Promise<string[]>;
}

export interface S3WorkspaceConfig {
  bucket: string;
  tenantId: string;
  agentId: string;
  localPath: string;
}

const RETRY_BASE_MS = 1000;
const MAX_RETRIES = 3;
/** Simple console logger for fire-and-forget error logging */
const logger = {
  info: (msg: string, meta?: Record<string, unknown>) =>
    console.log(JSON.stringify({ level: "info", msg, ...meta })),
  warn: (msg: string, meta?: Record<string, unknown>) =>
    console.warn(JSON.stringify({ level: "warn", msg, ...meta })),
  error: (msg: string, meta?: Record<string, unknown>) =>
    console.error(JSON.stringify({ level: "error", msg, ...meta })),
};

/**
 * Build the S3 key for a file under the tenant/agent prefix.
 */
export function buildS3Key(tenantId: string, agentId: string, filename: string): string {
  return `${tenantId}/${agentId}/${filename}`;
}

/**
 * Sleep helper for exponential backoff.
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Retry a function with exponential backoff.
 * Base delay = RETRY_BASE_MS, doubles each attempt, up to MAX_RETRIES attempts.
 */
async function withRetry<T>(fn: () => Promise<T>, context: string): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (attempt < MAX_RETRIES) {
        const delayMs = RETRY_BASE_MS * 2 ** attempt;
        logger.warn(`Retry ${attempt + 1}/${MAX_RETRIES} for ${context}`, {
          delayMs,
          error: String(err),
        });
        await sleep(delayMs);
      }
    }
  }
  throw lastError;
}
export class S3WorkspaceAdapter implements WorkspaceAdapter {
  private readonly s3: S3Client;
  private readonly bucket: string;
  private readonly tenantId: string;
  private readonly agentId: string;
  private readonly localPath: string;

  constructor(config: S3WorkspaceConfig, s3Client?: S3Client) {
    this.bucket = config.bucket;
    this.tenantId = config.tenantId;
    this.agentId = config.agentId;
    this.localPath = config.localPath;
    this.s3 = s3Client ?? new S3Client({});
  }

  /** Get the S3 key prefix for this tenant/agent */
  private get prefix(): string {
    return `${this.tenantId}/${this.agentId}/`;
  }

  /**
   * Sync all workspace files from S3 to local filesystem on startup.
   * Uses exponential backoff retry on S3 errors.
   */
  async syncFromS3(bucket: string, prefix: string, localPath: string): Promise<void> {
    const keys = await withRetry(
      () => this.listS3Objects(bucket, prefix),
      `listObjects ${bucket}/${prefix}`,
    );

    for (const key of keys) {
      const filename = key.slice(prefix.length);
      if (!filename) continue;

      await withRetry(async () => {
        const response = await this.s3.send(
          new GetObjectCommand({ Bucket: bucket, Key: key }),
        );
        const body = await response.Body?.transformToString("utf-8");
        if (body === undefined) return;

        const localFilePath = path.join(localPath, filename);
        await mkdir(path.dirname(localFilePath), { recursive: true });
        await fsWriteFile(localFilePath, body, "utf-8");
        logger.info(`Synced ${key} → ${localFilePath}`);
      }, `getObject ${key}`);
    }
  }

  /**
   * Read a workspace file. Returns local copy first; falls back to S3 if local read fails.
   */
  async readFile(filename: string): Promise<string> {
    const localFilePath = path.join(this.localPath, filename);
    try {
      return await fsReadFile(localFilePath, "utf-8");
    } catch {
      // Local file not found — fall back to S3
      const key = buildS3Key(this.tenantId, this.agentId, filename);
      const response = await this.s3.send(
        new GetObjectCommand({ Bucket: this.bucket, Key: key }),
      );
      const body = await response.Body?.transformToString("utf-8");
      if (body === undefined) {
        throw new Error(`File not found: ${filename}`);
      }
      return body;
    }
  }

  /**
   * Write a mutable workspace file to both local FS and S3.
   * Local write happens first; S3 write is fire-and-forget with error logging.
   */
  async writeFile(filename: string, content: string): Promise<void> {
    // Write local first
    const localFilePath = path.join(this.localPath, filename);
    await mkdir(path.dirname(localFilePath), { recursive: true });
    await fsWriteFile(localFilePath, content, "utf-8");

    // Fire-and-forget S3 write with error logging
    const key = buildS3Key(this.tenantId, this.agentId, filename);
    this.s3
      .send(
        new PutObjectCommand({
          Bucket: this.bucket,
          Key: key,
          Body: content,
          ContentType: "text/plain; charset=utf-8",
        }),
      )
      .catch((err: unknown) => {
        logger.error(`Failed to write ${key} to S3`, {
          bucket: this.bucket,
          key,
          error: String(err),
        });
      });
  }

  /**
   * List all workspace files under the given prefix.
   */
  async listFiles(prefix: string): Promise<string[]> {
    return this.listS3Objects(this.bucket, prefix);
  }

  /**
   * Internal helper to list S3 object keys under a prefix, handling pagination.
   */
  private async listS3Objects(bucket: string, prefix: string): Promise<string[]> {
    const keys: string[] = [];
    let continuationToken: string | undefined;

    do {
      const response = await this.s3.send(
        new ListObjectsV2Command({
          Bucket: bucket,
          Prefix: prefix,
          ContinuationToken: continuationToken,
        }),
      );

      if (response.Contents) {
        for (const obj of response.Contents) {
          if (obj.Key) keys.push(obj.Key);
        }
      }

      continuationToken = response.IsTruncated ? response.NextContinuationToken : undefined;
    } while (continuationToken);

    return keys;
  }
}
