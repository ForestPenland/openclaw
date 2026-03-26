#!/usr/bin/env python3
"""Seed workspace files to S3.

Reads templates from workspace-seeds/ and uploads them to the configured
S3 bucket under {tenant-id}/{agent-id}/ prefix.

Requirements: 18.4, 19.3
"""

import argparse
import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

WORKSPACE_SEEDS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "workspace-seeds"
)

SEED_FILES = [
    "SOUL.md",
    "MEMORY.md",
    "HEARTBEAT.md",
    "AGENTS.md",
    "TOOLS.md",
    "USER.md",
    "IDENTITY.md",
    "config-overlay.json",
]

SKILLS_DIR = os.path.join(WORKSPACE_SEEDS_DIR, "skills")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload workspace seed files to S3"
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument("--tenant-id", required=True, help="Tenant identifier")
    parser.add_argument("--agent-id", required=True, help="Agent identifier")
    parser.add_argument(
        "--profile", default=None, help="AWS CLI profile (default: use default profile)"
    )
    parser.add_argument("--region", default=None, help="AWS region")
    return parser.parse_args()


def create_s3_client(
    profile: str | None, region: str | None
) -> "boto3.client":
    session_kwargs: dict = {}
    if profile:
        session_kwargs["profile_name"] = profile
    if region:
        session_kwargs["region_name"] = region
    session = boto3.Session(**session_kwargs)
    return session.client("s3")


def seed_workspace(
    s3_client: "boto3.client",
    bucket: str,
    tenant_id: str,
    agent_id: str,
) -> None:
    seeds_dir = os.path.normpath(WORKSPACE_SEEDS_DIR)
    if not os.path.isdir(seeds_dir):
        print(f"ERROR: workspace-seeds directory not found: {seeds_dir}", file=sys.stderr)
        sys.exit(1)

    for filename in SEED_FILES:
        local_path = os.path.join(seeds_dir, filename)
        if not os.path.isfile(local_path):
            print(f"WARNING: seed file not found, skipping: {local_path}", file=sys.stderr)
            continue

        s3_key = f"{tenant_id}/{agent_id}/{filename}"
        print(f"Uploading {filename} -> s3://{bucket}/{s3_key}")

        with open(local_path, "r", encoding="utf-8") as f:
            content = f.read()

        s3_client.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
        )

    print(f"Done — {len(SEED_FILES)} workspace files seeded to s3://{bucket}/{tenant_id}/{agent_id}/")

    # Upload skills (recursive directory walk)
    skills_dir = os.path.normpath(SKILLS_DIR)
    if os.path.isdir(skills_dir):
        skill_count = 0
        for dirpath, _dirnames, filenames in os.walk(skills_dir):
            for filename in filenames:
                local_path = os.path.join(dirpath, filename)
                # Relative path from workspace-seeds/skills/ → skills/...
                rel_path = os.path.relpath(local_path, seeds_dir)
                s3_key = f"{tenant_id}/{agent_id}/{rel_path}"
                print(f"Uploading {rel_path} -> s3://{bucket}/{s3_key}")

                with open(local_path, "r", encoding="utf-8") as f:
                    content = f.read()

                s3_client.put_object(
                    Bucket=bucket,
                    Key=s3_key,
                    Body=content.encode("utf-8"),
                    ContentType="text/markdown" if filename.endswith(".md") else "application/octet-stream",
                )
                skill_count += 1

        if skill_count > 0:
            print(f"Done — {skill_count} skill files seeded")
    else:
        print("No skills directory found — skipping skill upload")


def main() -> None:
    args = parse_args()
    try:
        s3_client = create_s3_client(args.profile, args.region)
        seed_workspace(s3_client, args.bucket, args.tenant_id, args.agent_id)
    except (BotoCoreError, ClientError) as exc:
        print(f"ERROR: AWS operation failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
