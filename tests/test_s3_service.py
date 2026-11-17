"""Unit tests for S3 listing, metadata, content, bounds, and errors."""

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from aws_mcp_server.s3_service import S3Service, S3ServiceError


def test_list_objects_can_include_head_metadata() -> None:
    client = Mock()
    client.list_objects_v2.return_value = {
        "Contents": [
            {
                "Key": "docs/a.txt",
                "ETag": '"abc"',
                "Size": 5,
                "LastModified": datetime(2026, 1, 1, tzinfo=UTC),
                "StorageClass": "STANDARD",
            }
        ],
        "IsTruncated": False,
    }
    client.head_object.return_value = {"ContentType": "text/plain", "Metadata": {"owner": "test"}}

    result = S3Service(client).list_objects("example", "docs/", include_metadata=True)

    assert result["objects"][0]["key"] == "docs/a.txt"
    assert result["objects"][0]["metadata"]["content_type"] == "text/plain"
    client.head_object.assert_called_once_with(Bucket="example", Key="docs/a.txt")


def test_list_buckets_returns_names_and_creation_dates() -> None:
    client = Mock()
    client.list_buckets.return_value = {
        "Buckets": [
            {"Name": "example-a", "CreationDate": datetime(2026, 1, 1, tzinfo=UTC)},
            {"Name": "example-b"},
        ]
    }

    result = S3Service(client).list_buckets()

    assert result == {
        "buckets": [
            {"name": "example-a", "creation_date": "2026-01-01T00:00:00+00:00"},
            {"name": "example-b", "creation_date": None},
        ],
        "count": 2,
    }


def test_list_objects_passes_continuation_token() -> None:
    client = Mock()
    client.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

    S3Service(client).list_objects("example", continuation_token="next-page")

    client.list_objects_v2.assert_called_once_with(
        Bucket="example", Prefix="", MaxKeys=100, ContinuationToken="next-page"
    )


def test_read_object_returns_utf8_and_metadata() -> None:
    client = Mock()
    client.head_object.return_value = {"ContentLength": 5, "ContentType": "text/plain"}
    client.get_object.return_value = {"Body": Mock(read=Mock(return_value=b"hello"))}

    result = S3Service(client).read_object("example", "hello.txt")

    assert result["content"] == "hello"
    assert result["content_encoding"] == "utf-8"
    assert result["metadata"]["content_type"] == "text/plain"


def test_read_object_base64_encodes_binary_content() -> None:
    client = Mock()
    client.head_object.return_value = {"ContentLength": 2}
    client.get_object.return_value = {"Body": Mock(read=Mock(return_value=b"\x00\xff"))}

    result = S3Service(client).read_object("example", "data.bin")

    assert result["content"] == "AP8="
    assert result["content_encoding"] == "base64"


def test_read_object_does_not_download_oversized_objects() -> None:
    client = Mock()
    client.head_object.return_value = {"ContentLength": 20}

    result = S3Service(client).read_object("example", "large.bin", max_bytes=10)

    assert result["truncated"] is True
    client.get_object.assert_not_called()


def test_invalid_bounds_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_keys"):
        S3Service(Mock()).list_objects("example", max_keys=0)

    with pytest.raises(ValueError, match="max_bytes"):
        S3Service(Mock()).read_object("example", "key", max_bytes=0)


def test_aws_error_is_sanitized() -> None:
    client = Mock()
    client.list_objects_v2.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "secret details"}},
        "ListObjectsV2",
    )

    with pytest.raises(S3ServiceError) as error:
        S3Service(client).list_objects("example")

    assert error.value.code == "AccessDenied"
    assert "secret details" not in str(error.value)
