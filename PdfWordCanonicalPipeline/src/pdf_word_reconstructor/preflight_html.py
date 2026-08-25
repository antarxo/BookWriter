from __future__ import annotations

import argparse
from pathlib import Path

from .preflight import run_preflight, _print_report
from .strict_batch import _write_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run preflight only and write a detailed HTML report")
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args(argv)
    args.output_root.mkdir(parents=True, exist_ok=True)
    report = run_preflight(args.jobs, args.output_root / "preflight_report.json")
    _print_report(report)
    _write_summary(args.output_root / "batch_summary.html", [], report)
    return 0 if report.get("status") == "passed" else 11


if __name__ == "__main__":
    raise SystemExit(main())
