"""Unit tests for structured MCP tool error responses."""

from aws_mcp_server.s3_service import S3ServiceError
from aws_mcp_server.server import _run_s3_tool


def test_s3_service_error_is_returned_as_actionable_result() -> None:
    """Expose safe service codes and messages instead of a generic MCP failure."""

    result = _run_s3_tool(
        "list_objects",
        "example",
        lambda: (_ for _ in ()).throw(
            S3ServiceError("AWS credentials are unavailable.", "NoCredentials")
        ),
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "NoCredentials",
            "message": "AWS credentials are unavailable.",
            "retryable": False,
        },
    }


def test_invalid_input_is_returned_as_actionable_result() -> None:
    """Return validation failures with a stable, non-retryable error code."""

    result = _run_s3_tool(
        "list_objects",
        "example",
        lambda: (_ for _ in ()).throw(ValueError("bucket must not be empty")),
    )

    assert result["error"] == {
        "code": "InvalidInput",
        "message": "bucket must not be empty",
        "retryable": False,
    }
