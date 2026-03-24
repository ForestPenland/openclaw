import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  SupervisorAgent,
  generateSubAgentSessionId,
  classifyMessage,
  type IncomingMessage,
  type SupervisorConfig,
  type SpecialistType,
} from "../../../agents/supervisor.ts";
import type { ModelAdapter, ConverseInput, ConverseOutput } from "../../../adapters/bedrock-model.ts";
import type { ModelRouter } from "../../../adapters/model-router.ts";

// ---------------------------------------------------------------------------
// Mocks / Helpers
// ---------------------------------------------------------------------------

function createMockModelAdapter(responseText = "mock response"): ModelAdapter {
  return {
    invoke: vi.fn().mockResolvedValue({
      content: responseText,
      usage: { inputTokens: 10, outputTokens: 20 },
      modelId: "us.anthropic.claude-sonnet-4-20250514-v1:0",
      stopReason: "end_turn",
    } satisfies ConverseOutput),
    invokeStream: vi.fn(),
  };
}

function createMockModelRouter(): ModelRouter {
  return {
    selectModel: vi.fn().mockReturnValue("us.anthropic.claude-sonnet-4-20250514-v1:0"),
  };
}

function createMessage(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return {
    text: "Hello, how are you?",
    channel: "telegram",
    userId: "user-1",
    sessionId: "sess-abc123",
    ...overrides,
  };
}

function createConfig(overrides: Partial<SupervisorConfig> = {}): SupervisorConfig {
  return {
    modelAdapter: createMockModelAdapter(),
    modelRouter: createMockModelRouter(),
    systemPrompt: "You are a helpful assistant.",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("generateSubAgentSessionId", () => {
  it("produces the correct format: {rootSessionId}-{agentType}-{step}", () => {
    expect(generateSubAgentSessionId("sess-abc123", "infrastructure", 1))
      .toBe("sess-abc123-infrastructure-1");
  });

  it("works with different specialist types and steps", () => {
    expect(generateSubAgentSessionId("root", "code", 2)).toBe("root-code-2");
    expect(generateSubAgentSessionId("root", "communications", 1)).toBe("root-communications-1");
    expect(generateSubAgentSessionId("root", "research", 3)).toBe("root-research-3");
  });
});

describe("classifyMessage", () => {
  it("returns 'infrastructure' for deployment-related messages", () => {
    expect(classifyMessage("Please deploy the new stack")).toBe("infrastructure");
    expect(classifyMessage("Provision a new Lambda function")).toBe("infrastructure");
  });

  it("returns 'code' for code-related messages", () => {
    expect(classifyMessage("Write code for the API handler")).toBe("code");
    expect(classifyMessage("Please refactor the auth module")).toBe("code");
  });

  it("returns 'communications' for messaging-related messages", () => {
    expect(classifyMessage("Send message to the team")).toBe("communications");
    expect(classifyMessage("Notify the operator about the issue")).toBe("communications");
  });

  it("returns 'research' for research-related messages", () => {
    expect(classifyMessage("Research the best database option")).toBe("research");
    expect(classifyMessage("Investigate the performance issue")).toBe("research");
  });

  it("returns undefined for unclassifiable messages", () => {
    expect(classifyMessage("Hello, how are you?")).toBeUndefined();
    expect(classifyMessage("What time is it?")).toBeUndefined();
  });
});
describe("SupervisorAgent", () => {
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

  describe("direct handling", () => {
    it("handles simple messages directly when no specialist matches", async () => {
      const config = createConfig();
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Hello, how are you?" });

      const response = await agent.process(message);

      expect(response.handledDirectly).toBe(true);
      expect(response.text).toBe("mock response");
      expect(response.sessionId).toBe("sess-abc123");
      expect(response.delegatedTo).toBeUndefined();
      expect(config.modelAdapter.invoke).toHaveBeenCalledOnce();
    });

    it("handles messages directly when no invokeSpecialist callback is provided", async () => {
      const config = createConfig({ invokeSpecialist: undefined });
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Deploy the new stack" });

      const response = await agent.process(message);

      expect(response.handledDirectly).toBe(true);
      expect(config.modelAdapter.invoke).toHaveBeenCalledOnce();
    });

    it("passes system prompt to the model adapter", async () => {
      const modelAdapter = createMockModelAdapter();
      const config = createConfig({ modelAdapter, systemPrompt: "Be helpful." });
      const agent = new SupervisorAgent(config);

      await agent.process(createMessage());

      const invokeCall = (modelAdapter.invoke as ReturnType<typeof vi.fn>).mock.calls[0][0] as ConverseInput;
      expect(invokeCall.system).toEqual([{ text: "Be helpful." }]);
    });

    it("uses model router to select the model", async () => {
      const modelRouter = createMockModelRouter();
      const config = createConfig({ modelRouter });
      const agent = new SupervisorAgent(config);

      await agent.process(createMessage({ text: "Hello", channel: "heartbeat" }));

      expect(modelRouter.selectModel).toHaveBeenCalledWith("Hello", {
        source: "heartbeat",
        charCount: 5,
      });
    });
  });

  describe("delegation to specialists", () => {
    it("delegates infrastructure tasks to the infrastructure specialist", async () => {
      const invokeSpecialist = vi.fn().mockResolvedValue("Deployed successfully");
      const config = createConfig({ invokeSpecialist });
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Deploy the new Lambda function" });

      const response = await agent.process(message);

      expect(response.handledDirectly).toBe(false);
      expect(response.delegatedTo).toBe("infrastructure");
      expect(response.text).toBe("Deployed successfully");
      expect(invokeSpecialist).toHaveBeenCalledWith(
        "infrastructure",
        "Deploy the new Lambda function",
        "sess-abc123-infrastructure-1",
      );
    });

    it("generates correct sub-agent session IDs", async () => {
      const invokeSpecialist = vi.fn().mockResolvedValue("Done");
      const config = createConfig({ invokeSpecialist });
      const agent = new SupervisorAgent(config);
      const message = createMessage({
        text: "Write code for the handler",
        sessionId: "root-session",
      });

      await agent.process(message);

      expect(invokeSpecialist).toHaveBeenCalledWith(
        "code",
        "Write code for the handler",
        "root-session-code-1",
      );
    });
  });

  describe("fallback on specialist failure", () => {
    it("falls back to direct handling after 2 specialist retries", async () => {
      const invokeSpecialist = vi.fn().mockRejectedValue(new Error("Specialist down"));
      const modelAdapter = createMockModelAdapter("fallback response");
      const config = createConfig({ invokeSpecialist, modelAdapter });
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Deploy the stack" });

      const response = await agent.process(message);

      expect(invokeSpecialist).toHaveBeenCalledTimes(2);
      expect(response.handledDirectly).toBe(true);
      expect(response.text).toBe("fallback response");
      expect(modelAdapter.invoke).toHaveBeenCalledOnce();
    });

    it("succeeds on second retry without fallback", async () => {
      const invokeSpecialist = vi.fn()
        .mockRejectedValueOnce(new Error("Temporary failure"))
        .mockResolvedValueOnce("Success on retry");
      const config = createConfig({ invokeSpecialist });
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Deploy the stack" });

      const response = await agent.process(message);

      expect(invokeSpecialist).toHaveBeenCalledTimes(2);
      expect(response.handledDirectly).toBe(false);
      expect(response.text).toBe("Success on retry");
    });
  });

  describe("delegation depth enforcement", () => {
    it("respects maxDelegationDepth=2 (default)", async () => {
      const invokeSpecialist = vi.fn().mockResolvedValue("Done");
      const config = createConfig({ invokeSpecialist, maxDelegationDepth: 2 });
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Deploy the stack" });

      // Depth starts at 0, delegation to specialist would be depth 1 — allowed
      const response = await agent.process(message);
      expect(response.handledDirectly).toBe(false);
      expect(invokeSpecialist).toHaveBeenCalled();
    });

    it("handles directly when delegation depth would exceed limit", async () => {
      const invokeSpecialist = vi.fn().mockResolvedValue("Done");
      // maxDelegationDepth=1 means supervisor (depth 0) cannot delegate (depth 0+1 >= 1)
      const config = createConfig({ invokeSpecialist, maxDelegationDepth: 1 });
      const agent = new SupervisorAgent(config);
      const message = createMessage({ text: "Deploy the stack" });

      const response = await agent.process(message);
      expect(response.handledDirectly).toBe(true);
      expect(invokeSpecialist).not.toHaveBeenCalled();
    });
  });

  describe("proactive notifications", () => {
    it("sends notification via operator Telegram chat ID from SSM", async () => {
      const sendNotification = vi.fn().mockResolvedValue(undefined);
      const mockSsmClient = {
        send: vi.fn().mockResolvedValue({
          Parameter: { Value: "123456789" },
        }),
      } as any;

      const config = createConfig({
        sendNotification,
        ssmClient: mockSsmClient,
        operatorChatIdParam: "/openclaw/operator/telegram-chat-id",
      });
      const agent = new SupervisorAgent(config);

      await agent.notifyOperator("Deployment complete!", "sess-1");

      expect(mockSsmClient.send).toHaveBeenCalledOnce();
      expect(sendNotification).toHaveBeenCalledWith("123456789", "Deployment complete!");
    });

    it("caches the operator chat ID after first retrieval", async () => {
      const sendNotification = vi.fn().mockResolvedValue(undefined);
      const mockSsmClient = {
        send: vi.fn().mockResolvedValue({
          Parameter: { Value: "123456789" },
        }),
      } as any;

      const config = createConfig({ sendNotification, ssmClient: mockSsmClient });
      const agent = new SupervisorAgent(config);

      await agent.notifyOperator("First", "sess-1");
      await agent.notifyOperator("Second", "sess-2");

      // SSM should only be called once (cached)
      expect(mockSsmClient.send).toHaveBeenCalledOnce();
      expect(sendNotification).toHaveBeenCalledTimes(2);
    });

    it("skips notification when no sendNotification callback is configured", async () => {
      const config = createConfig({ sendNotification: undefined });
      const agent = new SupervisorAgent(config);

      // Should not throw
      await agent.notifyOperator("Test", "sess-1");
    });

    it("handles SSM retrieval failure gracefully", async () => {
      const sendNotification = vi.fn();
      const mockSsmClient = {
        send: vi.fn().mockRejectedValue(new Error("SSM unavailable")),
      } as any;

      const config = createConfig({ sendNotification, ssmClient: mockSsmClient });
      const agent = new SupervisorAgent(config);

      await agent.notifyOperator("Test", "sess-1");

      expect(sendNotification).not.toHaveBeenCalled();
    });
  });
});
