"""Read-only S3 operations used by the MCP tool layer.

The service deliberately receives a boto3 client so its AWS calls can be mocked
in tests and future AWS services can be added without coupling them together.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any

from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    EndpointConnectionError,
    NoCredentialsError,
    PartialCredentialsError,
)

logger = logging.getLogger(__name__)


class S3ServiceError(RuntimeError):
    """Represent a safe, user-facing S3 operation failure."""

    def __init__(self, message: str, code: str, retryable: bool = False) -> None:
        """Store an actionable message and safe diagnostic code."""

        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _isoformat(value: datetime | None) -> str | None:
    """Convert an AWS datetime to an ISO-8601 string for MCP JSON output."""

    return value.isoformat() if value else None


def _metadata_from_head(response: dict[str, Any]) -> dict[str, Any]:
    """Select JSON-safe object metadata from an S3 HEAD response."""

    return {
        "content_type": response.get("ContentType"),
        "content_length": response.get("ContentLength"),
        "etag": response.get("ETag"),
        "last_modified": _isoformat(response.get("LastModified")),
        "metadata": response.get("Metadata", {}),
        "storage_class": response.get("StorageClass"),
        "version_id": response.get("VersionId"),
        "server_side_encryption": response.get("ServerSideEncryption"),
        "sse_kms_key_id": response.get("SSEKMSKeyId"),
        "checksum": {
            key.removeprefix("Checksum").lower(): value
            for key, value in response.items()
            if key.startswith("Checksum")
        },
    }


class S3Service:
    """Provide bounded, read-only list, metadata, and object-read operations."""

    def __init__(self, client: Any) -> None:
        """Store an injected boto3 S3 client."""

        self._client = client

    def list_buckets(self) -> dict[str, Any]:
        """List buckets visible to the configured AWS identity.

        Returns:
            A JSON-serializable list containing bucket names and creation dates.
        Raises:
            S3ServiceError: If AWS rejects the account-level bucket listing.
        """

        try:
            response = self._client.list_buckets()
            buckets = [
                {
                    "name": bucket["Name"],
                    "creation_date": _isoformat(bucket.get("CreationDate")),
                }
                for bucket in response.get("Buckets", [])
            ]
            logger.info(
                "S3 bucket listing completed",
                extra={
                    "operation": "list_buckets",
                    "context": {"count": len(buckets)},
                },
            )
            return {"buckets": buckets, "count": len(buckets)}
        except (NoCredentialsError, PartialCredentialsError) as error:
            raise self._credentials_error("list_buckets", "account") from error
        except ClientError as error:
            raise self._operation_error("list_buckets", "account", error) from error
        except EndpointConnectionError as error:
            raise S3ServiceError(
                "Unable to connect to the S3 endpoint. Check the AWS region and network access.",
                "EndpointConnectionError",
                retryable=True,
            ) from error
        except BotoCoreError as error:
            raise S3ServiceError(
                "The AWS SDK could not complete the S3 bucket list operation.",
                type(error).__name__,
                retryable=True,
            ) from error

    def list_objects(
        self,
        bucket: str,
        prefix: str = "",
        max_keys: int = 100,
        include_metadata: bool = False,
        continuation_token: str | None = None,
    ) -> dict[str, Any]:
        """List objects and optionally fetch per-object HEAD metadata.

        Args:
            bucket: S3 bucket name.
            prefix: Optional key prefix.
            max_keys: Maximum objects returned, from 1 through 1,000.
            include_metadata: Make one HEAD request per listed object.
            continuation_token: Token returned by a previous call, if any.
        Returns:
            A JSON-serializable object list and pagination indicator.
        Raises:
            ValueError: If input bounds are invalid.
            S3ServiceError: If AWS rejects the operation.
        """

        if not bucket.strip():
            raise ValueError("bucket must not be empty")
        if not 1 <= max_keys <= 1000:
            raise ValueError("max_keys must be between 1 and 1000")

        try:
            request = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": max_keys}
            if continuation_token:
                request["ContinuationToken"] = continuation_token
            response = self._client.list_objects_v2(**request)
            objects = []
            for item in response.get("Contents", []):
                entry = {
                    "key": item["Key"],
                    "etag": item.get("ETag"),
                    "size": item.get("Size"),
                    "last_modified": _isoformat(item.get("LastModified")),
                    "storage_class": item.get("StorageClass"),
                    "checksum": item.get("ChecksumAlgorithm", []),
                }
                if include_metadata:
                    entry["metadata"] = _metadata_from_head(
                        self._client.head_object(Bucket=bucket, Key=item["Key"])
                    )
                objects.append(entry)
            logger.info(
                "s3 list_objects bucket=%s prefix=%s count=%d metadata=%s",
                bucket,
                prefix,
                len(objects),
                include_metadata,
            )
            return {
                "bucket": bucket,
                "prefix": prefix,
                "objects": objects,
                "is_truncated": response.get("IsTruncated", False),
                "next_continuation_token": response.get("NextContinuationToken"),
            }
        except (NoCredentialsError, PartialCredentialsError) as error:
            raise self._credentials_error("list_objects", bucket) from error
        except ClientError as error:
            raise self._operation_error("list_objects", bucket, error) from error
        except EndpointConnectionError as error:
            raise S3ServiceError(
                "Unable to connect to the S3 endpoint. Check the AWS region and network access.",
                "EndpointConnectionError",
                retryable=True,
            ) from error
        except BotoCoreError as error:
            raise S3ServiceError(
                "The AWS SDK could not complete the S3 list operation.",
                type(error).__name__,
                retryable=True,
            ) from error

    def read_object(self, bucket: str, key: str, max_bytes: int = 1_048_576) -> dict[str, Any]:
        """Read one S3 object with metadata and a bounded content representation.

        Text content is returned as UTF-8 text; binary content is base64 encoded.
        The size bound prevents an MCP response from accidentally loading a large
        object into the model context.
        """

        if not bucket.strip() or not key:
            raise ValueError("bucket and key are required")
        if not 1 <= max_bytes <= 10_485_760:
            raise ValueError("max_bytes must be between 1 and 10485760")

        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
            content_length = response.get("ContentLength", 0)
            if content_length > max_bytes:
                return {
                    "bucket": bucket,
                    "key": key,
                    "content": None,
                    "content_encoding": None,
                    "truncated": True,
                    "metadata": _metadata_from_head(response),
                    "message": "Object exceeds max_bytes; content was not downloaded.",
                }

            body = self._client.get_object(Bucket=bucket, Key=key)["Body"].read(max_bytes + 1)
            truncated = len(body) > max_bytes
            body = body[:max_bytes]
            try:
                content: str = body.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                content = base64.b64encode(body).decode("ascii")
                encoding = "base64"
            logger.info("s3 read_object bucket=%s key=%s bytes=%d", bucket, key, len(body))
            return {
                "bucket": bucket,
                "key": key,
                "content": content,
                "content_encoding": encoding,
                "truncated": truncated,
                "metadata": _metadata_from_head(response),
            }
        except (NoCredentialsError, PartialCredentialsError) as error:
            raise self._credentials_error("read_object", f"{bucket}/{key}") from error
        except ClientError as error:
            raise self._operation_error("read_object", f"{bucket}/{key}", error) from error
        except EndpointConnectionError as error:
            raise S3ServiceError(
                "Unable to connect to the S3 endpoint. Check the AWS region and network access.",
                "EndpointConnectionError",
                retryable=True,
            ) from error
        except BotoCoreError as error:
            raise S3ServiceError(
                "The AWS SDK could not complete the S3 read operation.",
                type(error).__name__,
                retryable=True,
            ) from error

    @staticmethod
    def _operation_error(operation: str, resource: str, error: ClientError) -> S3ServiceError:
        """Convert botocore errors to safe messages without exposing credentials."""

        code = error.response.get("Error", {}).get("Code", "Unknown")
        messages = {
            "AccessDenied": "AWS denied this S3 operation. Check the IAM and bucket policies.",
            "NoSuchBucket": "The requested S3 bucket does not exist or is not accessible.",
            "NoSuchKey": "The requested S3 object key does not exist.",
            "InvalidBucketName": "The supplied S3 bucket name is invalid.",
        }
        message = messages.get(code, f"S3 {operation} failed for the requested resource.")
        logger.error(
            "AWS rejected S3 operation",
            extra={
                "operation": operation,
                "resource": resource,
                "context": {"code": code, "retryable": False},
            },
        )
        return S3ServiceError(message, code, retryable=False)

    @staticmethod
    def _credentials_error(operation: str, resource: str) -> S3ServiceError:
        """Create a clear error for missing or incomplete AWS credentials."""

        logger.error(
            "AWS credentials unavailable for S3 operation",
            extra={
                "operation": operation,
                "resource": resource,
                "context": {"code": "NoCredentials", "retryable": False},
            },
        )
        return S3ServiceError(
            "AWS credentials are unavailable. Set AWS_PROFILE or provide credentials "
            "in the mounted AWS configuration.",
            "NoCredentials",
            retryable=False,
        )
