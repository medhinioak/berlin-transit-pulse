"""Tests for reading, summarising and rendering memory measurements."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from summarise_memory import Sample, read_samples, read_summary, render_markdown, summarise

MIB = 1024 * 1024
HEADER = "timestamp,container,mem_bytes,mem_limit_bytes,cpu_percent\n"


def _sample(minute: int, mib: int, limit_mib: int = 256, hour: int = 16) -> Sample:
    """Build a reading at <hour>:<minute> UTC on 2026-11-24 of the given size."""
    at = datetime(2026, 11, 24, hour, minute, tzinfo=UTC)
    return Sample(at, mib * MIB, limit_mib * MIB, 1.0)


def test_percentiles_of_a_known_series() -> None:
    """Median and p95 of 1..20 MiB follow the inclusive quantile definition."""
    stats = summarise([_sample(i, i + 1) for i in range(20)])
    assert stats.samples == 20
    assert stats.median_bytes == 10.5 * MIB
    assert stats.p95_bytes == pytest.approx(19.05 * MIB)
    assert stats.peak_bytes == 20 * MIB


def test_single_sample_is_its_own_median_p95_and_peak() -> None:
    """One reading needs no quantiles."""
    stats = summarise([_sample(0, 42)])
    assert stats.median_bytes == stats.p95_bytes == stats.peak_bytes == 42 * MIB


def test_peak_time_is_the_first_of_equal_peaks() -> None:
    """When the peak repeats, the earliest time is reported."""
    stats = summarise([_sample(0, 10), _sample(1, 50), _sample(2, 50), _sample(3, 20)])
    assert stats.peak_at.minute == 1


def test_limit_is_the_largest_seen() -> None:
    """A changed limit in the last reading doesn't hide the one in force before."""
    assert summarise([_sample(0, 10, 512), _sample(1, 10, 384)]).limit_bytes == 512 * MIB


def test_interleaved_containers_and_a_restarted_run(tmp_path: Path) -> None:
    """Rows are grouped per container; a header appended by a restart is skipped."""
    csv = tmp_path / "run.csv"
    csv.write_text(
        HEADER
        + "2026-11-24T16:00:00Z,kafka,100,800,5.0\n"
        + "2026-11-24T16:00:00Z,postgres,50,500,1.5\n"
        + HEADER
        + "2026-11-24T16:00:30Z,kafka,120,800,3.0\n"
    )
    samples, skipped = read_samples(csv)
    assert skipped == 0
    assert {name: len(rows) for name, rows in samples.items()} == {"kafka": 2, "postgres": 1}
    assert samples["kafka"][1].at == datetime(2026, 11, 24, 16, 0, 30, tzinfo=UTC)
    assert summarise(samples["kafka"]).mean_cpu_percent == 4.0


def test_rows_that_are_not_readings_are_skipped_and_counted(tmp_path: Path) -> None:
    """Restarting containers (0, -1, --) and a row cut off by Ctrl-C don't stop the summary."""
    csv = tmp_path / "run.csv"
    csv.write_text(
        HEADER
        + "2026-11-24T16:00:00Z,kafka,100,800,5.0\n"
        + "2026-11-24T16:00:30Z,processor,0,0,0.00\n"
        + "2026-11-24T16:00:30Z,processor,-1,-1,--\n"
        + "2026-11-24T16:00:30Z,processor,100,800,--\n"
        + "2026-11-24T16:01:00Z,kafka,12"
    )
    samples, skipped = read_samples(csv)
    assert skipped == 4
    assert {name: len(rows) for name, rows in samples.items()} == {"kafka": 1}


def test_summary_file_is_merged_into_the_table(tmp_path: Path) -> None:
    """memory.peak, event counts, restarts and notes show up; summary-only containers too."""
    summary = tmp_path / "run.summary.txt"
    summary.write_text(
        "# 2026-11-24T19:30:00Z project=btp-live\n"
        "# container memory_peak_bytes limit_hits oom_kills restarts oom_killed note\n"
        f"btp-live-kafka-1 {600 * MIB} 3 1 2 false\n"
        "btp-live-kafka-init-1 - - - 0 false skipped: exited\n"
        "btp-live-x-1 - - - 1 true skipped: exited\n"
    )
    finals = read_summary(summary)
    kafka = finals["btp-live-kafka-1"]
    assert (kafka.memory_peak_bytes, kafka.limit_hits, kafka.oom_kills) == (600 * MIB, 3, 1)
    assert kafka.restarts == 2
    assert finals["btp-live-kafka-init-1"].limit_hits is None
    assert finals["btp-live-kafka-init-1"].note == "skipped: exited"

    stats = {
        "btp-live-kafka-1": summarise([_sample(0, 500)]),
        "btp-live-web-1": summarise([_sample(0, 9)]),
    }
    table = render_markdown(stats, finals).splitlines()
    assert table[2].startswith("| `btp-live-kafka-1` | 1 | 256.0 | 500.0 | 500.0 | 500.0 |")
    assert table[2].endswith("| 600.0 | 3 | 1 | 2 | false |")
    assert table[3].startswith("| `btp-live-web-1` |")
    assert table[3].endswith("| – | – | – | – | not in summary |")
    # Containers without readings follow, so an early OOM kill still reaches the table.
    assert table[4].startswith("| `btp-live-kafka-init-1` | 0 | – |")
    assert table[5].startswith("| `btp-live-x-1` | 0 |")
    assert table[5].endswith("| – (skipped: exited) | – | – | 1 | true |")


def test_berlin_peak_time_carries_its_own_date() -> None:
    """23:30 UTC is 00:30 the next day in Berlin, and the table says so."""
    table = render_markdown({"kafka": summarise([_sample(30, 500, hour=23)])})
    assert "| 2026-11-24 23:30 | 2026-11-25 00:30 |" in table
