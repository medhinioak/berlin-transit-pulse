"""Smoke test: the btp_schedule package imports."""

import btp_schedule


def test_package_imports() -> None:
    assert btp_schedule.__doc__
