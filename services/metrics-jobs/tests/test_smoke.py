"""Smoke test: the btp_metrics_jobs package imports."""

import btp_metrics_jobs


def test_package_imports() -> None:
    """The package imports and has a docstring."""
    assert btp_metrics_jobs.__doc__
