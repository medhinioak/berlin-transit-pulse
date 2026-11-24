"""Zip backups to S3 (btp_schedule.backup)."""

from pathlib import Path

import boto3
from botocore.stub import ANY, Stubber

from btp_schedule.backup import backup_zip

BUCKET = "btp-data-test"


def _zip(tmp_path: Path) -> Path:
    """A small file standing in for the zip."""
    path = tmp_path / "gtfs.zip"
    path.write_bytes(b"PK")
    return path


def test_upload_never_overwrites(tmp_path: Path) -> None:
    """The upload asks S3 to refuse an existing key (If-None-Match: *)."""
    client = boto3.client("s3", region_name="eu-north-1")
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": "backups/gtfs/abc123def456.zip",
                "Body": ANY,
                "ContentType": "application/zip",
                "IfNoneMatch": "*",
            },
        )
        assert backup_zip(BUCKET, "eu-north-1", "abc123def456", _zip(tmp_path), client)
        stubber.assert_no_pending_responses()


def test_existing_backup_counts_as_done(tmp_path: Path) -> None:
    """412 Precondition Failed means the zip is there already."""
    client = boto3.client("s3", region_name="eu-north-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("put_object", "PreconditionFailed", http_status_code=412)
        assert backup_zip(BUCKET, "eu-north-1", "abc123def456", _zip(tmp_path), client)


def test_other_errors_dont_raise(tmp_path: Path) -> None:
    """Access denied is logged and reported as False; the refresh goes on."""
    client = boto3.client("s3", region_name="eu-north-1")
    with Stubber(client) as stubber:
        stubber.add_client_error("put_object", "AccessDenied", http_status_code=403)
        assert not backup_zip(BUCKET, "eu-north-1", "abc123def456", _zip(tmp_path), client)
