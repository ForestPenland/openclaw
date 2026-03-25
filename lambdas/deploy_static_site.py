"""AgentCore Gateway Lambda Tool: Deploy Static Website to S3 + CloudFront.

Receives a tool call from AgentCore Gateway (MCP format) and creates:
1. An S3 bucket configured for static website hosting
2. A CloudFront distribution pointing to the bucket
3. Uploads the provided HTML content

Returns the CloudFront URL where the site is accessible.
"""

import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
RESOURCE_PREFIX = "agent-"


def lambda_handler(event, context):
    """Handle tool invocation from AgentCore Gateway.

    Expected event format (from Gateway Lambda target):
    {
        "tool_name": "deploy_static_site",
        "arguments": {
            "site_name": "my-portfolio",
            "html_content": "<html>...</html>",
            "index_document": "index.html"
        }
    }
    """
    logger.info("Received event: %s", json.dumps(event, default=str))

    tool_name = event.get("tool_name", "")
    arguments = event.get("arguments", {})

    if tool_name == "deploy_static_site":
        return deploy_static_site(arguments)
    elif tool_name == "list_deployed_sites":
        return list_deployed_sites()
    elif tool_name == "delete_static_site":
        return delete_static_site(arguments)
    else:
        return {"error": f"Unknown tool: {tool_name}"}


def deploy_static_site(args: dict) -> dict:
    """Create S3 bucket + CloudFront distribution with the provided HTML."""
    site_name = args.get("site_name", "")
    html_content = args.get("html_content", "<html><body><h1>Hello from OpenClaw!</h1></body></html>")
    index_document = args.get("index_document", "index.html")

    if not site_name:
        return {"error": "site_name is required"}

    # Sanitize and prefix the bucket name
    bucket_name = f"{RESOURCE_PREFIX}{site_name}-{int(time.time())}"
    bucket_name = bucket_name.lower().replace("_", "-")[:63]

    s3 = boto3.client("s3", region_name=REGION)
    cf = boto3.client("cloudfront", region_name=REGION)

    try:
        # 1. Create S3 bucket
        create_params = {"Bucket": bucket_name}
        if REGION != "us-east-1":
            create_params["CreateBucketConfiguration"] = {
                "LocationConstraint": REGION
            }
        s3.create_bucket(**create_params)
        logger.info("Created bucket: %s", bucket_name)

        # 2. Disable block public access (needed for CloudFront OAC)
        s3.delete_public_access_block(Bucket=bucket_name)

        # 3. Upload HTML content
        s3.put_object(
            Bucket=bucket_name,
            Key=index_document,
            Body=html_content.encode("utf-8"),
            ContentType="text/html",
        )
        logger.info("Uploaded %s to %s", index_document, bucket_name)

        # 4. Create CloudFront Origin Access Control
        oac_response = cf.create_origin_access_control(
            OriginAccessControlConfig={
                "Name": f"{bucket_name}-oac",
                "Description": f"OAC for {site_name}",
                "SigningProtocol": "sigv4",
                "SigningBehavior": "always",
                "OriginAccessControlOriginType": "s3",
            }
        )
        oac_id = oac_response["OriginAccessControl"]["Id"]

        # 5. Create CloudFront distribution
        dist_config = {
            "CallerReference": str(time.time()),
            "Comment": f"Static site: {site_name} (deployed by OpenClaw agent)",
            "DefaultCacheBehavior": {
                "TargetOriginId": bucket_name,
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                "ForwardedValues": {
                    "QueryString": False,
                    "Cookies": {"Forward": "none"},
                },
                "MinTTL": 0,
                "DefaultTTL": 86400,
                "MaxTTL": 31536000,
                "Compress": True,
            },
            "Origins": {
                "Quantity": 1,
                "Items": [
                    {
                        "Id": bucket_name,
                        "DomainName": f"{bucket_name}.s3.{REGION}.amazonaws.com",
                        "S3OriginConfig": {"OriginAccessIdentity": ""},
                        "OriginAccessControlId": oac_id,
                    }
                ],
            },
            "Enabled": True,
            "DefaultRootObject": index_document,
            "PriceClass": "PriceClass_100",
        }

        dist_response = cf.create_distribution(DistributionConfig=dist_config)
        dist_id = dist_response["Distribution"]["Id"]
        dist_domain = dist_response["Distribution"]["DomainName"]
        dist_arn = dist_response["Distribution"]["ARN"]
        logger.info("Created CloudFront distribution: %s (%s)", dist_id, dist_domain)

        # 6. Add bucket policy allowing CloudFront access
        bucket_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AllowCloudFrontServicePrincipal",
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudfront.amazonaws.com"},
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{bucket_name}/*",
                    "Condition": {
                        "StringEquals": {
                            "AWS:SourceArn": dist_arn
                        }
                    },
                }
            ],
        }
        s3.put_bucket_policy(
            Bucket=bucket_name, Policy=json.dumps(bucket_policy)
        )

        return {
            "status": "success",
            "site_name": site_name,
            "bucket_name": bucket_name,
            "distribution_id": dist_id,
            "url": f"https://{dist_domain}",
            "message": f"Static site '{site_name}' deployed successfully. "
                       f"It will be available at https://{dist_domain} within 5-10 minutes "
                       f"(CloudFront distribution is deploying).",
        }

    except ClientError as e:
        logger.error("AWS error: %s", str(e))
        return {"error": str(e)}
    except Exception as e:
        logger.error("Unexpected error: %s", str(e))
        return {"error": str(e)}


def list_deployed_sites() -> dict:
    """List all agent-deployed static sites (S3 buckets with agent- prefix)."""
    s3 = boto3.client("s3", region_name=REGION)

    try:
        response = s3.list_buckets()
        agent_buckets = [
            b["Name"]
            for b in response.get("Buckets", [])
            if b["Name"].startswith(RESOURCE_PREFIX)
        ]
        return {
            "status": "success",
            "sites": agent_buckets,
            "count": len(agent_buckets),
        }
    except ClientError as e:
        return {"error": str(e)}


def delete_static_site(args: dict) -> dict:
    """Delete an agent-deployed static site (S3 bucket + CloudFront distribution)."""
    bucket_name = args.get("bucket_name", "")

    if not bucket_name:
        return {"error": "bucket_name is required"}

    if not bucket_name.startswith(RESOURCE_PREFIX):
        return {"error": f"Can only delete agent-deployed resources (prefix: {RESOURCE_PREFIX})"}

    s3 = boto3.client("s3", region_name=REGION)

    try:
        # Delete all objects in the bucket
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            objects = page.get("Contents", [])
            if objects:
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
                )

        # Delete the bucket
        s3.delete_bucket(Bucket=bucket_name)

        return {
            "status": "success",
            "message": f"Deleted bucket {bucket_name} and all its contents. "
                       "Note: The associated CloudFront distribution may need to be "
                       "disabled and deleted separately.",
        }
    except ClientError as e:
        return {"error": str(e)}
