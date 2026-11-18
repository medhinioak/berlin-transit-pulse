"""Smoke test: the btp_processor package imports."""

import btp_processor


def test_package_imports() -> None:
    """The package imports and has a docstring."""
    assert btp_processor.__doc__
