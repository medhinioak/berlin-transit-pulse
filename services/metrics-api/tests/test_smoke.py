"""Smoke test: the btp_metrics_api package imports."""

import btp_metrics_api


def test_package_imports() -> None:
    assert btp_metrics_api.__doc__
