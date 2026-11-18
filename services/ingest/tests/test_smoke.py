"""Smoke test: the btp_ingest package imports."""

import btp_ingest


def test_package_imports() -> None:
    assert btp_ingest.__doc__
