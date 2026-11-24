"""Turn a scripts/measure_memory.sh CSV into a markdown table per container.

    uv run python scripts/summarise_memory.py run.csv [--summary run.summary.txt]

Per container: samples, limit, median, p95 and peak of the memory readings, the time of the
first peak (UTC and Berlin) and the mean CPU. With --summary, also the cgroup memory.peak and
the OOMKilled flag recorded when the measurement stopped.
"""

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
MIB = 1024 * 1024
HEADER = ["timestamp", "container", "mem_bytes", "mem_limit_bytes", "cpu_percent"]


@dataclass(frozen=True)
class Sample:
    """One reading of one container."""

    at: datetime
    mem_bytes: int
    limit_bytes: int
    cpu_percent: float


@dataclass(frozen=True)
class Stats:
    """Memory statistics of one container over the whole run."""

    samples: int
    limit_bytes: int
    median_bytes: float
    p95_bytes: float
    peak_bytes: int
    peak_at: datetime
    mean_cpu_percent: float


@dataclass(frozen=True)
class Final:
    """What the summary file says about one container when the measurement stopped."""

    memory_peak_bytes: int | None
    oom_killed: str
    note: str


def read_samples(path: Path) -> dict[str, list[Sample]]:
    """Read the CSV into readings per container, skipping header rows (a restart appends one)."""
    by_container: dict[str, list[Sample]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.reader(f):
            if not row or row == HEADER:
                continue
            ts, name, mem, limit, cpu = row
            by_container[name].append(
                Sample(
                    at=datetime.fromisoformat(ts),
                    mem_bytes=int(mem),
                    limit_bytes=int(limit),
                    cpu_percent=float(cpu),
                )
            )
    return dict(by_container)


def summarise(samples: list[Sample]) -> Stats:
    """Compute median, p95, the first peak and mean CPU of a non-empty list of readings."""
    mem = [s.mem_bytes for s in samples]
    p95 = statistics.quantiles(mem, n=20, method="inclusive")[18] if len(mem) > 1 else mem[0]
    peak = max(samples, key=lambda s: s.mem_bytes)  # max keeps the first of equal values
    return Stats(
        samples=len(samples),
        limit_bytes=samples[-1].limit_bytes,
        median_bytes=statistics.median(mem),
        p95_bytes=p95,
        peak_bytes=peak.mem_bytes,
        peak_at=peak.at,
        mean_cpu_percent=statistics.fmean(s.cpu_percent for s in samples),
    )


def read_summary(path: Path) -> dict[str, Final]:
    """Parse the summary file: `name peak_bytes|- oom_killed [note…]`, # lines are comments."""
    finals: dict[str, Final] = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, peak, oom, *note = line.split()
        finals[name] = Final(
            memory_peak_bytes=None if peak == "-" else int(peak),
            oom_killed=oom,
            note=" ".join(note),
        )
    return finals


def _mib(value: float | None) -> str:
    """Format bytes as MiB with one decimal, or a dash."""
    return "–" if value is None else f"{value / MIB:.1f}"


def render_markdown(stats: dict[str, Stats], finals: dict[str, Final] | None = None) -> str:
    """Render one table row per container, sorted by peak memory, largest first."""
    head = ["Container", "Samples", "Limit MiB", "Median MiB", "p95 MiB", "Peak MiB"]
    head += ["Peak at (UTC)", "Peak at (Berlin)", "Mean CPU %"]
    if finals is not None:
        head += ["memory.peak MiB", "OOMKilled"]
    lines = ["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)]
    for name, s in sorted(stats.items(), key=lambda kv: -kv[1].peak_bytes):
        cells = [
            f"`{name}`",
            str(s.samples),
            _mib(s.limit_bytes),
            _mib(s.median_bytes),
            _mib(s.p95_bytes),
            _mib(s.peak_bytes),
            s.peak_at.strftime("%Y-%m-%d %H:%M"),
            s.peak_at.astimezone(BERLIN).strftime("%H:%M"),
            f"{s.mean_cpu_percent:.1f}",
        ]
        if finals is not None:
            final = finals.get(name)
            if final is None:
                cells += ["–", "not in summary"]
            else:
                peak = _mib(final.memory_peak_bytes) + (f" ({final.note})" if final.note else "")
                cells += [peak, final.oom_killed]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the markdown table for a measurement CSV."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv", type=Path, help="CSV written by scripts/measure_memory.sh")
    parser.add_argument("--summary", type=Path, help="its .summary.txt file")
    args = parser.parse_args(argv)
    samples = read_samples(args.csv)
    if not samples:
        print(f"summarise: no readings in {args.csv}", file=sys.stderr)
        return 1
    stats = {name: summarise(rows) for name, rows in samples.items()}
    finals = read_summary(args.summary) if args.summary else None
    print(render_markdown(stats, finals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
