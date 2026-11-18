"""Smoke test: the btp_processor package imports."""

import btp_processor


def test_package_imports() -> None:
    assert btp_processor.__doc__
