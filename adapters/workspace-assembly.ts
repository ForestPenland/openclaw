import type { WorkspaceAdapter } from "./s3-workspace.ts";

/**
 * Workspace file assembly order matching OpenClaw core.
 * IDENTITY → SOUL → AGENTS → USER → MEMORY → TOOLS
 */
export const WORKSPACE_FILE_ORDER = [
  "IDENTITY.md",
  "SOUL.md",
  "AGENTS.md",
  "USER.md",
  "MEMORY.md",
  "TOOLS.md",
] as const;

/**
 * Assemble workspace files into a single system prompt string.
 * Reads each file in WORKSPACE_FILE_ORDER via the provided adapter,
 * concatenating with double newlines. Missing files are silently skipped.
 */
export async function assembleSystemPrompt(
  adapter: WorkspaceAdapter,
): Promise<string> {
  const sections: string[] = [];

  for (const filename of WORKSPACE_FILE_ORDER) {
    try {
      const content = await adapter.readFile(filename);
      if (content) {
        sections.push(content);
      }
    } catch {
      // File not found — skip silently
    }
  }

  return sections.join("\n\n");
}
