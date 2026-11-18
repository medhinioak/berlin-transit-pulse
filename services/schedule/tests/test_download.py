"""The download cache: no request while fresh, cached zip on network failure. No live calls."""

import json
import os
import time
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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


def _serving(status: int, content: bytes = b"") -> Callable[..., Any]:
    """Stand-in for httpx.stream that answers every request with one response."""

    @contextmanager
    def stream(method: str, url: str, **kwargs: Any) -> Iterator[httpx.Response]:
        yield httpx.Response(status, content=content, request=httpx.Request(method, url))

    return stream


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


def test_invalid_zip_falls_back_to_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 200 with an HTML page instead of a zip keeps the cached zip and leaves no .part file."""
    cached = _cache(tmp_path, age_hours=48)
    before = cached.read_bytes()
    monkeypatch.setattr(httpx, "stream", _serving(200, b"<html>maintenance</html>"))
    assert download.fetch(URL, tmp_path, "contact", max_age_hours=24) == cached
    assert cached.read_bytes() == before
    assert not (tmp_path / "gtfs.zip.part").exists()


def test_invalid_zip_without_cache_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a cache an invalid download is an error and leaves nothing behind."""
    monkeypatch.setattr(httpx, "stream", _serving(200, b"<html>maintenance</html>"))
    with pytest.raises(zipfile.BadZipFile):
        download.fetch(URL, tmp_path, "contact", max_age_hours=24)
    assert list(tmp_path.iterdir()) == []


def test_not_modified_restarts_max_age(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 304 keeps the cached zip and makes it fresh again, so the next run sends no request."""
    cached = _cache(tmp_path, age_hours=48)
    monkeypatch.setattr(httpx, "stream", _serving(304))
    assert download.fetch(URL, tmp_path, "contact", max_age_hours=24) == cached
    assert time.time() - cached.stat().st_mtime < 60
