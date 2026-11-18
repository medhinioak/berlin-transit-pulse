"""Download the static GTFS zip into a cache, re-downloading only when it has changed."""

import json
import logging
import time
import zipfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


def fetch(url: str, cache_dir: Path, contact: str, max_age_hours: float) -> Path:
    """Return the path of the cached zip for url, downloading it first if needed.

    A cached zip younger than max_age_hours is used without a request: VBB sends no ETag or
    Last-Modified, so every request downloads the full ~84 MB. Older caches are revalidated
    with If-None-Match / If-Modified-Since when the server sent them. If the server can't be
    reached but a cached zip exists, the cached zip is used.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "gtfs.zip"
    meta_path = cache_dir / "gtfs.zip.json"
    meta: dict[str, str] = {}
    if path.exists() and meta_path.exists() and meta_path.read_text().strip():
        meta = json.loads(meta_path.read_text())
    if meta.get("url") != url:
        meta = {}
    if meta and time.time() - path.stat().st_mtime < max_age_hours * 3600:
        log.info("cached zip for %s is younger than %s h, not downloading", url, max_age_hours)
        return path
    headers = {"User-Agent": f"berlin-transit-pulse/0.1 (+{contact})"}
    if "etag" in meta:
        headers["If-None-Match"] = meta["etag"]
    if "last_modified" in meta:
        headers["If-Modified-Since"] = meta["last_modified"]

    part = path.with_name(path.name + ".part")
    try:
        with httpx.stream(
            "GET", url, headers=headers, follow_redirects=True, timeout=300.0
        ) as response:
            if response.status_code == 304:
                log.info("%s unchanged, using the cached zip", url)
                return path
            response.raise_for_status()
            with part.open("wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
            new_meta = {"url": url}
            if etag := response.headers.get("etag"):
                new_meta["etag"] = etag
            if last_modified := response.headers.get("last-modified"):
                new_meta["last_modified"] = last_modified
    except httpx.HTTPError as exc:
        part.unlink(missing_ok=True)
        if meta:
            log.warning("download of %s failed (%s), using the cached zip", url, exc)
            return path
        raise
    zipfile.ZipFile(part).close()  # a broken download fails here and leaves the cache alone
    part.rename(path)
    meta_path.write_text(json.dumps(new_meta))
    log.info("downloaded %s (%d bytes)", url, path.stat().st_size)
    return path
