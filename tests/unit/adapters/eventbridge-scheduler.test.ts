import { describe, expect, it, vi } from "vitest";
import {
  createSchedule,
  deleteSchedule,
  type CreateScheduleParams,
} from "../../../adapters/eventbridge-scheduler.ts";

// ---------------------------------------------------------------------------
// Mock SchedulerClient
// ---------------------------------------------------------------------------

function createMockClient(sendResult: unknown = {}) {
  return { send: vi.fn().mockResolvedValue(sendResult) } as any;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function baseParams(overrides?: Partial<CreateScheduleParams>): CreateScheduleParams {
  return {
    name: "test-schedule",
    scheduleExpression: "cron(0 12 * * ? *)",
    targetArn: "arn:aws:lambda:us-east-1:123456789012:function:my-fn",
    targetInput: JSON.stringify({ key: "value" }),
    isOneTime: false,
    roleArn: "arn:aws:iam::123456789012:role/scheduler-role",
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("createSchedule", () => {
  it("creates a recurring schedule with ActionAfterCompletion NONE", async () => {
    const client = createMockClient();

    await createSchedule(baseParams({ isOneTime: false }), client);

    expect(client.send).toHaveBeenCalledOnce();
    const command = client.send.mock.calls[0][0];
    expect(command.input).toMatchObject({
      Name: "test-schedule",
      GroupName: "openclaw-agent-schedules",
      ScheduleExpression: "cron(0 12 * * ? *)",
      ActionAfterCompletion: "NONE",
      FlexibleTimeWindow: { Mode: "OFF" },
      Target: {
        Arn: "arn:aws:lambda:us-east-1:123456789012:function:my-fn",
        RoleArn: "arn:aws:iam::123456789012:role/scheduler-role",
        Input: JSON.stringify({ key: "value" }),
      },
    });
  });

  it("creates a one-time schedule with ActionAfterCompletion DELETE", async () => {
    const client = createMockClient();

    await createSchedule(
      baseParams({
        isOneTime: true,
        scheduleExpression: "at(2025-07-01T12:00:00)",
      }),
      client,
    );

    expect(client.send).toHaveBeenCalledOnce();
    const command = client.send.mock.calls[0][0];
    expect(command.input.ActionAfterCompletion).toBe("DELETE");
  });

  it("uses the openclaw-agent-schedules group", async () => {
    const client = createMockClient();

    await createSchedule(baseParams(), client);

    const command = client.send.mock.calls[0][0];
    expect(command.input.GroupName).toBe("openclaw-agent-schedules");
  });

  it("passes target input through unchanged", async () => {
    const client = createMockClient();
    const input = JSON.stringify({ agentId: "agent-1", action: "remind" });

    await createSchedule(baseParams({ targetInput: input }), client);

    const command = client.send.mock.calls[0][0];
    expect(command.input.Target.Input).toBe(input);
  });

  it("propagates SDK errors", async () => {
    const client = { send: vi.fn().mockRejectedValue(new Error("AccessDenied")) } as any;

    await expect(createSchedule(baseParams(), client)).rejects.toThrow("AccessDenied");
  });
});

describe("deleteSchedule", () => {
  it("deletes a schedule from the openclaw-agent-schedules group", async () => {
    const client = createMockClient();

    await deleteSchedule("my-schedule", client);

    expect(client.send).toHaveBeenCalledOnce();
    const command = client.send.mock.calls[0][0];
    expect(command.input).toMatchObject({
      Name: "my-schedule",
      GroupName: "openclaw-agent-schedules",
    });
  });

  it("propagates SDK errors", async () => {
    const client = { send: vi.fn().mockRejectedValue(new Error("ResourceNotFound")) } as any;

    await expect(deleteSchedule("missing", client)).rejects.toThrow("ResourceNotFound");
  });
});
