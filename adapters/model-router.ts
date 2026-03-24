// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

export interface TaskMetadata {
  source: 'heartbeat' | 'consolidation' | 'user' | 'builder';
  charCount: number;
}

export interface ModelRouter {
  /** Select model ID based on task text and metadata */
  selectModel(taskText: string, metadata?: TaskMetadata): string;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const SIMPLE_PATTERNS = /\b(summarize|classify|extract|translate|format)\b/i;
export const COMPLEX_PATTERNS = /\b(architect|design|strategy|plan.?infrastructure|build|deploy)\b/i;
export const CHAR_THRESHOLD = 500;

export const MODEL_MAP = {
  simple: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
  complex: 'us.anthropic.claude-opus-4-5-20251101-v1:0',
  default: 'us.anthropic.claude-sonnet-4-20250514-v1:0',
} as const;

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

export class DefaultModelRouter implements ModelRouter {
  /**
   * Select the most cost-effective Bedrock model based on task complexity.
   *
   * Routing rules (evaluated in order):
   * 1. Consolidation source → Haiku (cheapest for batch summarization)
   * 2. Simple patterns + under char threshold → Haiku
   * 3. Complex patterns → Opus
   * 4. Default → Sonnet
   */
  selectModel(taskText: string, metadata?: TaskMetadata): string {
    // Rule 1: consolidation always uses Haiku
    if (metadata?.source === 'consolidation') {
      return MODEL_MAP.simple;
    }

    const charCount = metadata?.charCount ?? taskText.length;

    // Rule 2: simple patterns + short text → Haiku
    if (SIMPLE_PATTERNS.test(taskText) && charCount < CHAR_THRESHOLD) {
      return MODEL_MAP.simple;
    }

    // Rule 3: complex patterns → Opus
    if (COMPLEX_PATTERNS.test(taskText)) {
      return MODEL_MAP.complex;
    }

    // Rule 4: default → Sonnet
    return MODEL_MAP.default;
  }
}
