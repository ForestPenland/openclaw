/**
 * Role Manager — task-scoped IAM role lifecycle with permission boundary enforcement.
 *
 * Creates short-lived IAM roles prefixed with `agent-task-` for elevated
 * permissions. Every role is constrained by the `agent-permission-boundary`
 * managed policy. Roles are tracked in DynamoDB and auto-cleaned after 24h.
 *
 * Requirements: 7.1, 7.2, 7.3, 7.5, 7.6, 7.7, 7.9
 */

import {
  IAMClient,
  CreateRoleCommand,
  DeleteRoleCommand,
  PutRolePolicyCommand,
  DeleteRolePolicyCommand,
  ListAttachedRolePoliciesCommand,
} from "@aws-sdk/client-iam";
import { STSClient, AssumeRoleCommand } from "@aws-sdk/client-sts";
import {
  DynamoDBClient,
  PutItemCommand,
  QueryCommand,
  UpdateItemCommand,
  ScanCommand,
} from "@aws-sdk/client-dynamodb";

// ── Types ──────────────────────────────────────────────────────

export interface RoleConfig {
  /** Human-readable description of why the role is needed */
  purpose: string;
  /** JSON IAM policy document for the role's inline policy */
  policyDocument: string;
  /** STS session duration in seconds (default: 3600, max: 14400) */
  sessionDurationSeconds?: number;
  /** Optional link to an execution environment */
  environmentId?: string;
}

export interface RoleHandle {
  roleName: string;
  roleArn: string;
  createdAt: number;
  expiresAt: number;
}

export interface RoleRecord {
  roleName: string;
  roleArn: string;
  purpose: string;
  createdAt: number;
  expiresAt: number;
  environmentId?: string;
  status: "active" | "expired" | "deleted";
}

export interface RoleProvisionCheck {
  allowed: boolean;
  activeRoleCount: number;
  maxConcurrentRoles: number;
  reason?: string;
}

export interface STSCredentials {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  expiration: number;
}

export interface RoleManagerDeps {
  iam: IAMClient;
  sts: STSClient;
  dynamo: DynamoDBClient;
  accountId: string;
  /** DynamoDB table name for role tracking */
  tableName: string;
  /** ARN of the permission boundary managed policy */
  permissionBoundaryArn: string;
  /** Max concurrent active roles (default: 5) */
  maxConcurrentRoles?: number;
  /** Role expiry in hours (default: 24) */
  roleExpiryHours?: number;
}

// ── Constants ──────────────────────────────────────────────────

const ROLE_PREFIX 
: deps.roleExpiryHours ?? DEFAULT_EXPIRY_HOURS,
    };
  }

  /** Check if a new role can be created (concurrent role limit). */
  async canCreateRole(): Promise<RoleProvisionCheck> {
    const active = await this.listActiveRoles();
    const count = active.length;
    const max = this.deps.maxConcurrentRoles;
    if (count >= max) {
      return {
        allowed: false,
        activeRoleCount: count,
        maxConcurrentRoles: max,
        reason: `Role limit reached: ${count}/${max} active roles`,
      };
    }
    return { allowed: true, activeRoleCount: count, maxConcurrentRoles: max };
  }

  /** Create a task-scoped IAM role with permission boundary. */
  async createRole(config: RoleConfig): Promise<RoleHandle> {
    // Enforce concurrent limit
    const check = await this.canCreateRole();
    if (!check.allowed) {
      throw new Error(check.reason ?? "Cannot create role");
    }

    const timestamp = Math.floor(Date.now() / 1000);
    const suffix = Math.random().toString(36).slice(2, 6);
    const roleName = `${ROLE_PREFIX}${timestamp}-${suffix}`;
    const now = Date.now();
    const expiresAt = now + this.deps.roleExpiryHours * 3600 * 1000;

    // Trust policy: allow the current account to assume this role
    const trustPolicy = JSON.stringify({
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Principal: { AWS: `arn:aws:iam::${this.deps.accountId}:root` },
          Action: "sts:AssumeRole",
        },
      ],
    });

    // Create the IAM role with permission boundary
    const createResult = await this.deps.iam.send(
      new CreateRoleCommand({
        RoleName: roleName,
        AssumeRolePolicyDocument: trustPolicy,
        PermissionsBoundary: this.deps.permissionBoundaryArn,
        Tags: [
          { Key: "Purpose", Value: config.purpose.slice(0, 256) },
          { Key: "CreatedBy", Value: "openclaw-agent" },
          { Key: "ExpiresAt", Value: new Date(expiresAt).toISOString() },
        ],
      }),
    );

    const roleArn = createResult.Role?.Arn;
    if (!roleArn) throw new Error("IAM CreateRole did not return an ARN");

    // Attach inline policy
    await this.deps.iam.send(
      new PutRolePolicyCommand({
        RoleName: roleName,
        PolicyName: INLINE_POLICY_NAME,
        PolicyDocument: config.policyDocument,
      }),
    );

    // Track in DynamoDB
    await this.deps.dynamo.send(
      new PutItemCommand({
        TableName: this.deps.tableName,
        Item: {
          roleName: { S: roleName },
          roleArn: { S: roleArn },
          purpose: { S: config.purpose },
          createdAt: { N: String(now) },
          expiresAt: { N: String(expiresAt) },
          status: { S: "active" },
          ...(config.environmentId ? { environmentId: { S: config.environmentId } } : {}),
        },
      }),
    );

    return { roleName, roleArn, createdAt: now, expiresAt };
  }

  /** Assume a task-scoped role via STS. */
  async assumeRole(
    roleArn: string,
    sessionDurationSeconds?: number,
  ): Promise<STSCredentials> {
    const duration = Math.min(
      sessionDurationSeconds ?? DEFAULT_SESSION_SECONDS,
      MAX_SESSION_SECONDS,
    );

    const result = await this.deps.sts.send(
      new AssumeRoleCommand({
        RoleArn: roleArn,
        RoleSessionName: "openclaw-agent-session",
        DurationSeconds: duration,
      }),
    );

    const creds = result.Credentials;
    if (!creds?.AccessKeyId || !creds.SecretAccessKey || !creds.SessionToken) {
      throw new Error("STS AssumeRole did not return credentials");
    }

    return {
      accessKeyId: creds.AccessKeyId,
      secretAccessKey: creds.SecretAccessKey,
      sessionToken: creds.SessionToken,
      expiration: creds.Expiration?.getTime() ?? Date.now() + duration * 1000,
    };
  }

  /** List active agent-created roles from DynamoDB. */
  async listActiveRoles(): Promise<RoleRecord[]> {
    const result = await this.deps.dynamo.send(
      new ScanCommand({
        TableName: this.deps.tableName,
        FilterExpression: "#s = :active",
        ExpressionAttributeNames: { "#s": "status" },
        ExpressionAttributeValues: { ":active": { S: "active" } },
      }),
    );

    return (result.Items ?? []).map((item) => ({
      roleName: item.roleName?.S ?? "",
      roleArn: item.roleArn?.S ?? "",
      purpose: item.purpose?.S ?? "",
      createdAt: Number(item.createdAt?.N ?? 0),
      expiresAt: Number(item.expiresAt?.N ?? 0),
      environmentId: item.environmentId?.S,
      status: "active" as const,
    }));
  }

  /** Delete an agent-created role and update DynamoDB. */
  async deleteRole(roleName: string): Promise<void> {
    // Remove inline policy first (required before role deletion)
    try {
      await this.deps.iam.send(
        new DeleteRolePolicyCommand({
          RoleName: roleName,
          PolicyName: INLINE_POLICY_NAME,
        }),
      );
    } catch {
      // Policy may not exist — continue with role deletion
    }

    // Delete the IAM role
    await this.deps.iam.send(new DeleteRoleCommand({ RoleName: roleName }));

    // Update DynamoDB status
    await this.deps.dynamo.send(
      new UpdateItemCommand({
        TableName: this.deps.tableName,
        Key: { roleName: { S: roleName } },
        UpdateExpression: "SET #s = :deleted",
        ExpressionAttributeNames: { "#s": "status" },
        ExpressionAttributeValues: { ":deleted": { S: "deleted" } },
      }),
    );
  }
}
