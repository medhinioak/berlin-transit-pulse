"""The download cache: no request while fresh, cached zip on network failure. No live calls."""

import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from btp_schedule import download

URL = "https://example.invalid/gtfs.zip"


def _cache(tmp_path: Path, age_hours: float) -> Path:
    """Put a valid zip and its metadata in a cache folder, aged by age_hours."""
    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("agency.txt", "agency_id\n1\n")
    (tmp_path / "gtfs.zip.json").write_text(json.dumps({"url": URL}))
    mtime = time.time() - age_hours * 3600
    os.utime(path, (mtime, mtime))
    return path


def _no_network(*args: Any, **kwargs: Any) -> Any:
    """Stand-in for httpx.stream that always fails to connect."""
    raise httpx.ConnectError("no network in tests")


def test_fresh_cache_is_used_without_a_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A zip younger than the max age is returned without calling the server."""
    cached = _cache(tmp_path, age_hours=1)
    monkeypatch.setattr(httpx, "stream", _no_network)
    assert download.fetch(URL, tmp_path, "contact", max_age_hours=24) == cached


def test_stale_cache_is_used_when_download_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old cache is revalidated; if the server can't be reached, the cache is used."""
    cached = _cache(tmp_path, age_hours=48)
    monkeypatch.setattr(httpx, "stream", _no_network)
    assert download.fetch(URL, tmp_path, "contact", max_age_hours=24) == cached


def test_no_cache_and_no_network_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a cache a failed download is an error, not an empty import."""
    monkeypatch.setattr(httpx, "stream", _no_network)
    with pytest.raises(httpx.ConnectError):
        download.fetch(URL, tmp_path, "contact", max_age_hours=24)
