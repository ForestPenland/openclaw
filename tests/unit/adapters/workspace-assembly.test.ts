import { describe, expect, it, vi } from "vitest";
import type { WorkspaceAdapter } from "../../../adapters/s3-workspace.ts";
import {
  WORKSPACE_FILE_ORDER,
  assembleSystemPrompt,
} from "../../../adapters/workspace-assembly.ts";

function createMockAdapter(
  files: Record<string, string>,
): WorkspaceAdapter {
  return {
    syncFromS3: vi.fn(),
    readFile: vi.fn(async (filename: string) => {
      if (filename in files) return files[filename];
      throw new Error(`File not found: ${filename}`);
    }),
    writeFile: vi.fn(),
    listFiles: vi.fn(),
  };
}

describe("WORKSPACE_FILE_ORDER", () => {
  it("has exactly 6 files in the correct order", () => {
    expect(WORKSPACE_FILE_ORDER).toEqual([
      "IDENTITY.md",
      "SOUL.md",
      "AGENTS.md",
      "USER.md",
      "MEMORY.md",
      "TOOLS.md",
    ]);
  });
});

describe("assembleSystemPrompt", () => {
  it("concatenates all workspace files in order with double newlines", async () => {
    const adapter = createMockAdapter({
      "IDENTITY.md": "I am the agent",
      "SOUL.md": "Be helpful",
      "AGENTS.md": "Supervisor delegates",
      "USER.md": "User preferences",
      "MEMORY.md": "Remember this",
      "TOOLS.md": "Available tools",
    });

    const result = await assembleSystemPrompt(adapter);

    expect(result).toBe(
      "I am the agent\n\nBe helpful\n\nSupervisor delegates\n\nUser preferences\n\nRemember this\n\nAvailable tools",
    );
  });

  it("reads files in IDENTITY → SOUL → AGENTS → USER → MEMORY → TOOLS order", async () => {
    const readOrder: string[] = [];
    const adapter: WorkspaceAdapter = {
      syncFromS3: vi.fn(),
      readFile: vi.fn(async (filename: string) => {
        readOrder.push(filename);
        return `content of ${filename}`;
      }),
      writeFile: vi.fn(),
      listFiles: vi.fn(),
    };

    await assembleSystemPrompt(adapter);

    expect(readOrder).toEqual([
      "IDENTITY.md",
      "SOUL.md",
      "AGENTS.md",
      "USER.md",
      "MEMORY.md",
      "TOOLS.md",
    ]);
  });

  it("skips missing files without error", async () => {
    const adapter = createMockAdapter({
      "IDENTITY.md": "I am the agent",
      "MEMORY.md": "Remember this",
    });

    const result = await assembleSystemPrompt(adapter);

    expect(result).toBe("I am the agent\n\nRemember this");
  });

  it("returns empty string when no files exist", async () => {
    const adapter = createMockAdapter({});

    const result = await assembleSystemPrompt(adapter);

    expect(result).toBe("");
  });

  it("preserves markdown content exactly", async () => {
    const markdownContent = "# Title\n\n- item 1\n- item 2\n\n```code```\n\nUnicode: 日本語 🦞";
    const adapter = createMockAdapter({
      "IDENTITY.md": markdownContent,
    });

    const result = await assembleSystemPrompt(adapter);

    expect(result).toBe(markdownContent);
  });
});
