"""Zip backups to S3 (btp_schedule.backup)."""

from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

from btp_schedule import cli
from btp_schedule.backup import backup_zip
from btp_schedule.importer import ImportResult
from btp_schedule.settings import ScheduleSettings

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


def test_failed_backup_is_retried_once_then_remembered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed upload is tried again on the next run; after a success, never again."""
    calls: list[str] = []
    results = iter([False, True])

    def fake_backup(bucket: str, region: str, version_id: str, path: Path) -> bool:
        calls.append(version_id)
        return next(results)

    monkeypatch.setattr(cli, "backup_zip", fake_backup)
    settings = ScheduleSettings(raw_bucket=BUCKET, schedule_cache_dir=tmp_path / "cache")
    zip_path = _zip(tmp_path)
    for imported in (True, False, False):  # the import, then two later no-op runs
        cli._backup(ImportResult("abc123def456", imported), zip_path, settings)  # noqa: SLF001
    assert calls == ["abc123def456", "abc123def456"]
    assert (tmp_path / "cache" / "backed-up" / "abc123def456").exists()


def test_no_backup_without_bucket_or_for_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixtures (a folder) and runs without a bucket upload nothing."""
    monkeypatch.setattr(cli, "backup_zip", lambda *a: pytest.fail("no upload expected"))
    result = ImportResult("abc123def456", True)
    cli._backup(result, _zip(tmp_path), ScheduleSettings(raw_bucket=None))  # noqa: SLF001
    cli._backup(result, tmp_path, ScheduleSettings(raw_bucket=BUCKET))  # noqa: SLF001
