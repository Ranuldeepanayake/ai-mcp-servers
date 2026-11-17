"""MCP server entrypoint and registration point for modular AWS tools."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError
from mcp.server import MCPServer

from aws_mcp_server.logging_config import configure_logging
from aws_mcp_server.s3_service import S3Service, S3ServiceError

logger = logging.getLogger(__name__)
server = MCPServer("aws-mcp-server", version="0.1.0")
s3_service: S3Service | None = None
aws_session: boto3.Session | None = None

_STARTUP_ENVIRONMENT_VARIABLES = (
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_SHARED_CREDENTIALS_FILE",
    "AWS_CONFIG_FILE",
    "AWS_SDK_LOAD_CONFIG",
    "AWS_EC2_METADATA_DISABLED",
    "MCP_TRANSPORT",
    "MCP_HOST",
    "MCP_PORT",
)


def _environment_status() -> dict[str, bool]:
    """Return only whether startup environment variables are present."""

    return {name: bool(os.getenv(name)) for name in _STARTUP_ENVIRONMENT_VARIABLES}


def _initialize_aws() -> None:
    """Create the boto3 session and log safe credential-resolution diagnostics."""

    global aws_session, s3_service
    logger.info(
        "application startup configuration",
        extra={
            "operation": "startup",
            "context": {"environment_variables_set": _environment_status()},
        },
    )
    aws_session = boto3.Session()
    try:
        credentials_resolved = aws_session.get_credentials() is not None
        logger.log(
            logging.INFO if credentials_resolved else logging.WARNING,
            "AWS SDK credential resolution completed",
            extra={
                "operation": "startup",
                "context": {
                    "credentials_resolved": credentials_resolved,
                    "region_configured": bool(aws_session.region_name),
                },
            },
        )
    except BotoCoreError as error:
        logger.warning(
            "AWS SDK credential resolution raised an SDK error",
            extra={
                "operation": "startup",
                "context": {"credentials_resolved": False, "error_type": type(error).__name__},
            },
        )
    try:
        s3_service = S3Service(aws_session.client("s3"))
    except BotoCoreError as error:
        logger.error(
            "S3 client initialization failed",
            extra={
                "operation": "startup",
                "context": {"error_type": type(error).__name__},
            },
        )


def _get_s3_service() -> S3Service:
    """Return the initialized S3 service or raise an actionable startup error."""

    if s3_service is None:
        raise S3ServiceError(
            "The AWS S3 client is not initialized. Check the startup logs and AWS configuration.",
            "AWSClientInitializationError",
        )
    return s3_service


def _run_s3_tool(
    operation: str,
    resource: str,
    action: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Execute an S3 action and return structured, model-readable errors."""

    try:
        return action()
    except S3ServiceError as error:
        logger.error(
            "S3 tool operation failed",
            extra={
                "operation": operation,
                "resource": resource,
                "context": {"code": error.code, "retryable": error.retryable},
            },
        )
        return {
            "ok": False,
            "error": {
                "code": error.code,
                "message": str(error),
                "retryable": error.retryable,
            },
        }
    except ValueError as error:
        logger.warning(
            "S3 tool input validation failed",
            extra={
                "operation": operation,
                "resource": resource,
                "context": {"code": "InvalidInput"},
            },
        )
        return {
            "ok": False,
            "error": {"code": "InvalidInput", "message": str(error), "retryable": False},
        }
    except Exception:
        logger.exception(
            "Unexpected S3 tool failure",
            extra={
                "operation": operation,
                "resource": resource,
                "context": {"code": "InternalError"},
            },
        )
        return {
            "ok": False,
            "error": {
                "code": "InternalError",
                "message": "The S3 operation failed unexpectedly; check the server logs.",
                "retryable": False,
            },
        }


@server.tool()
def list_s3_buckets() -> dict[str, Any]:
    """List S3 buckets visible to the configured AWS identity."""

    return _run_s3_tool("list_buckets", "account", lambda: _get_s3_service().list_buckets())


@server.tool()
def list_s3_objects(
    bucket: str,
    prefix: str = "",
    max_keys: int = 100,
    include_metadata: bool = False,
    continuation_token: str | None = None,
) -> dict[str, Any]:
    """List S3 objects and optionally retrieve each object's metadata."""

    return _run_s3_tool(
        "list_objects",
        bucket,
        lambda: _get_s3_service().list_objects(
            bucket, prefix, max_keys, include_metadata, continuation_token
        ),
    )


@server.tool()
def read_s3_object(bucket: str, key: str, max_bytes: int = 1_048_576) -> dict[str, Any]:
    """Read an S3 object and return its metadata plus text or base64 content."""

    return _run_s3_tool(
        "read_object",
        f"{bucket}/{key}",
        lambda: _get_s3_service().read_object(bucket, key, max_bytes),
    )


def main() -> None:
    """Configure logging and run the MCP server using the selected transport.

    ``stdio`` remains the safe default for desktop MCP hosts. A container can
    set ``MCP_TRANSPORT=streamable-http`` and ``MCP_HOST=0.0.0.0`` to expose
    the same tools through the forwarded HTTP endpoint.
    """

    configure_logging()
    _initialize_aws()
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("MCP_PORT", "8000"))
    except ValueError as error:
        logger.error(
            "MCP_PORT must be an integer",
            extra={"operation": "startup", "context": {"code": "InvalidConfiguration"}},
        )
        raise SystemExit(2) from error
    logger.info("starting aws-mcp-server transport=%s host=%s port=%d", transport, host, port)
    if transport == "stdio":
        server.run()
    else:
        server.run(transport=transport, host=host, port=port)


if __name__ == "__main__":
    main()
