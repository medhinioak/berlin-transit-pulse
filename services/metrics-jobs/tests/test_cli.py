"""Unit tests for apply-sql: file order and waiting for the processor's tables."""

from pathlib import Path
from typing import Any

import pytest
from psycopg import errors

from btp_metrics_jobs import cli
from btp_metrics_jobs.settings import MetricsJobsSettings


def test_view_files_sorted_by_name(tmp_path: Path) -> None:
    """Files apply in name order; other files are ignored."""
    views = tmp_path / "views"
    views.mkdir()
    for name in ["010_b.sql", "005_a.sql", "README.md", "020_c.sql"]:
        (views / name).write_text("SELECT 1;")
    assert [p.name for p in cli.view_files(tmp_path)] == ["005_a.sql", "010_b.sql", "020_c.sql"]


def test_view_files_empty_is_an_error(tmp_path: Path) -> None:
    """A wrong BTP_METRICS_SQL_DIR fails loudly instead of applying nothing."""
    with pytest.raises(FileNotFoundError):
        cli.view_files(tmp_path)


def test_repo_views_exist() -> None:
    """The default SQL dir points at services/metrics-jobs/sql in the checkout."""
    names = [p.name for p in cli.view_files(MetricsJobsSettings().metrics_sql_dir)]
    assert "010_now_by_mode.sql" in names


class FakeConn:
    """Context manager standing in for a psycopg connection."""

    def __enter__(self) -> "FakeConn":
        """Return itself."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Do nothing."""


def test_waits_for_missing_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """events.* not created yet: retry until the processor has created them."""
    attempts: list[int] = []

    def apply(conn: Any, files: list[Path]) -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise errors.UndefinedTable('relation "events.trip_state" does not exist')

    monkeypatch.setattr(cli, "connect", lambda settings: FakeConn())
    monkeypatch.setattr(cli, "apply_sql", apply)
    cli.apply_with_wait(MetricsJobsSettings(wait_for_tables_s=10), [], sleep=0)
    assert len(attempts) == 3


def test_gives_up_after_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tables still missing after the wait: the error propagates (exit 1)."""

    def apply(conn: Any, files: list[Path]) -> None:
        raise errors.UndefinedTable('relation "events.trip_state" does not exist')

    monkeypatch.setattr(cli, "connect", lambda settings: FakeConn())
    monkeypatch.setattr(cli, "apply_sql", apply)
    with pytest.raises(errors.UndefinedTable):
        cli.apply_with_wait(MetricsJobsSettings(wait_for_tables_s=0), [], sleep=0)
