"""Copy each newly imported GTFS zip to s3://<data bucket>/backups/gtfs/<version_id>.zip."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types_boto3_s3 import S3Client

log = logging.getLogger(__name__)

PREFIX = "backups/gtfs/"


def backup_key(version_id: str) -> str:
    """Return the object key of a version's zip."""
    return f"{PREFIX}{version_id}.zip"


def backup_zip(
    bucket: str, region: str, version_id: str, path: Path, client: "S3Client | None" = None
) -> bool:
    """Upload the zip unless that key exists; return True if it is in the bucket afterwards.

    backups/ refuses overwrites (versioning is off), so the upload sends If-None-Match: *,
    which the instance role requires; 412 means it is already there. Other failures are logged
    and return False: a missing backup must not fail the refresh.
    """
    # Imported here: boto3 adds about 30 MB, which fixtures mode and local runs don't need.
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    key = backup_key(version_id)
    try:
        if client is None:
            client = boto3.client(
                "s3",
                region_name=region,
                config=Config(retries={"mode": "standard"}, connect_timeout=10, read_timeout=120),
            )
        with path.open("rb") as body:
            client.put_object(
                Bucket=bucket, Key=key, Body=body, ContentType="application/zip", IfNoneMatch="*"
            )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "PreconditionFailed":
            log.info("s3://%s/%s exists already", bucket, key)
            return True
        log.warning("backup of %s to s3://%s/%s failed: %s", version_id, bucket, key, exc)
        return False
    except (BotoCoreError, OSError) as exc:
        log.warning("backup of %s to s3://%s/%s failed: %s", version_id, bucket, key, exc)
        return False
    log.info("backed up %s to s3://%s/%s", version_id, bucket, key)
    return True
