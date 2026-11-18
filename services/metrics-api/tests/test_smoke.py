"""Smoke test: the btp_metrics_api package imports."""

import btp_metrics_api


def test_package_imports() -> None:
    """The package imports and has a docstring."""
    assert btp_metrics_api.__doc__
