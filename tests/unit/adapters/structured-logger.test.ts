import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  StructuredLogger,
  logRequest,
} from "../../../adapters/structured-logger.ts";

describe("logRequest", () => {
  it("creates a log entry with all required fields", () => {
    const entry = logRequest({
      sessionId: "sess-123",
      agentId: "agent-1",
      channel: "telegram",
      responseLatency: 250,
    });

    expect(entry.sessionId).toBe("sess-123");
    expect(entry.agentId).toBe("agent-1");
    expect(entry.channel).toBe("telegram");
    expect(entry.responseLatency).toBe(250);
    expect(entry.timestamp).toBeDefined();
    expect(entry.level).toBe("info");
    expect(entry.message).toBe("request");
  });

  it("uses custom message when provided", () => {
    const entry = logRequest({
      sessionId: "s1",
      agentId: "a1",
      channel: "web",
      responseLatency: 100,
      message: "custom message",
    });

    expect(entry.message).toBe("custom message");
  });

  it("merges extra fields into the entry", () => {
    const entry = logRequest({
      sessionId: "s1",
      agentId: "a1",
      channel: "slack",
      responseLatency: 50,
      extra: { userId: "u-42", action: "deploy" },
    });

    expect(entry.userId).toBe("u-42");
    expect(entry.action).toBe("deploy");
  });

  it("produces valid JSON when serialized", () => {
    const entry = logRequest({
      sessionId: "s1",
      agentId: "a1",
      channel: "telegram",
      responseLatency: 300,
    });

    const json = JSON.stringify(entry);
    const parsed = JSON.parse(json);

    expect(parsed.sessionId).toBe("s1");
    expect(parsed.agentId).toBe("a1");
    expect(parsed.channel).toBe("telegram");
    expect(parsed.responseLatency).toBe(300);
  });
});

describe("StructuredLogger", () => {
  let consoleSpy: ReturnType<typeof vi.spyOn>;
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleSpy.mockRestore();
    consoleErrorSpy.mockRestore();
  });

  it("emits JSON to stdout on request()", () => {
    const logger = new StructuredLogger("agent-1");

    logger.request({
      sessionId: "sess-1",
      channel: "telegram",
      responseLatency: 200,
    });

    expect(consoleSpy).toHaveBeenCalledOnce();
    const output = JSON.parse(consoleSpy.mock.calls[0][0] as string);
    expect(output.sessionId).toBe("sess-1");
    expect(output.agentId).toBe("agent-1");
    expect(output.channel).toBe("telegram");
    expect(output.responseLatency).toBe(200);
  });

  it("emits JSON to stdout on info()", () => {
    const logger = new StructuredLogger("agent-2");

    logger.info("processing complete", {
      sessionId: "sess-2",
      channel: "web",
      responseLatency: 150,
    });

    expect(consoleSpy).toHaveBeenCalledOnce();
    const output = JSON.parse(consoleSpy.mock.calls[0][0] as string);
    expect(output.message).toBe("processing complete");
    expect(output.level).toBe("info");
  });

  it("emits JSON to stderr on error()", () => {
    const logger = new StructuredLogger("agent-3");

    logger.error(
      "something failed",
      { sessionId: "sess-3", channel: "slack", responseLatency: 500 },
      new Error("boom"),
    );

    expect(consoleErrorSpy).toHaveBeenCalledOnce();
    const output = JSON.parse(consoleErrorSpy.mock.calls[0][0] as string);
    expect(output.message).toBe("something failed");
    expect(output.level).toBe("error");
    expect(output.error).toContain("boom");
  });
});
