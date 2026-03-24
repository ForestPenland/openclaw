/**
 * EventBridge Scheduler adapter for agent-created schedules.
 *
 * Provides functions to create and delete EventBridge Scheduler schedules
 * in the `openclaw-agent-schedules` group. One-time schedules are configured
 * with `ActionAfterCompletion: DELETE` so they auto-clean after firing.
 *
 * Requirements: 7.6, 7.7
 */

import {
  SchedulerClient,
  CreateScheduleCommand,
  DeleteScheduleCommand,
  type FlexibleTimeWindowMode,
  type ActionAfterCompletion,
} from "@aws-sdk/client-scheduler";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SCHEDULE_GROUP = "openclaw-agent-schedules";

// ---------------------------------------------------------------------------
// Interfaces
// ---------------------------------------------------------------------------

export interface CreateScheduleParams {
  /** Unique schedule name */
  name: string;
  /** Cron expression (e.g. `cron(0 2 * * ? *)`) or at() expression (e.g. `at(2025-01-01T00:00:00)`) */
  scheduleExpression: string;
  /** ARN of the target to invoke (e.g. Lambda ARN) */
  targetArn: string;
  /** JSON input to pass to the target */
  targetInput: string;
  /** Whether this is a one-time schedule (sets ActionAfterCompletion: DELETE) */
  isOneTime: boolean;
  /** IAM role ARN that EventBridge Scheduler assumes to invoke the target */
  roleArn: string;
}

// ---------------------------------------------------------------------------
// Logger (structured JSON, matching other adapters)
// ---------------------------------------------------------------------------

const logger = {
  info: (msg: string, meta?: Record<string, unknown>) =>
    console.log(JSON.stringify({ level: "info", msg, ...meta })),
  error: (msg: string, meta?: Record<string, unknown>) =>
    console.error(JSON.stringify({ level: "error", msg, ...meta })),
};

// ---------------------------------------------------------------------------
// Adapter
// ---------------------------------------------------------------------------

/**
 * Create an EventBridge Scheduler schedule in the `openclaw-agent-schedules` group.
 *
 * For one-time schedules (`isOneTime: true`), sets `ActionAfterCompletion` to
 * `DELETE` so the schedule auto-cleans after firing.
 */
export async function createSchedule(
  params: CreateScheduleParams,
  client?: SchedulerClient,
): Promise<void> {
  const scheduler = client ?? new SchedulerClient({});

  const actionAfterCompletion: ActionAfterCompletion = params.isOneTime
    ? "DELETE"
    : "NONE";

  const command = new CreateScheduleCommand({
    Name: params.name,
    GroupName: SCHEDULE_GROUP,
    ScheduleExpression: params.scheduleExpression,
    FlexibleTimeWindow: { Mode: "OFF" as FlexibleTimeWindowMode },
    ActionAfterCompletion: actionAfterCompletion,
    Target: {
      Arn: params.targetArn,
      RoleArn: params.roleArn,
      Input: params.targetInput,
    },
  });

  await scheduler.send(command);

  logger.info("Schedule created", {
    name: params.name,
    group: SCHEDULE_GROUP,
    isOneTime: params.isOneTime,
    actionAfterCompletion,
  });
}

/**
 * Delete an EventBridge Scheduler schedule from the `openclaw-agent-schedules` group.
 */
export async function deleteSchedule(
  name: string,
  client?: SchedulerClient,
): Promise<void> {
  const scheduler = client ?? new SchedulerClient({});

  const command = new DeleteScheduleCommand({
    Name: name,
    GroupName: SCHEDULE_GROUP,
  });

  await scheduler.send(command);

  logger.info("Schedule deleted", { name, group: SCHEDULE_GROUP });
}
