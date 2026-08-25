from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .preflight import run_preflight


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("_") or "job"


def _load_calibration(output: Path) -> dict[str, Any]:
    path = output / "analysis" / "calibration.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _strict_success(output: Path, expected_pages: int) -> tuple[bool, str]:
    calibration = _load_calibration(output)
    if not calibration:
        return False, "missing calibration.json"
    if calibration.get("status") != "completed":
        return False, f"calibration status={calibration.get('status')}"
    if calibration.get("selected_page_count_exact") is not True:
        return False, "selected_page_count_exact is not true"
    comparison = calibration.get("selected_comparison") or {}
    if int(comparison.get("output_page_count", -1)) != expected_pages:
        return False, f"output pages={comparison.get('output_page_count')} expected={expected_pages}"
    if not list(output.glob("native_page_structure_*.docx")):
        return False, "missing final DOCX"
    if not list(output.glob("native_page_structure_*.pdf")):
        return False, "missing final PDF"
    return True, "exact page-count gate passed"


def _failure_detail(job: dict[str, Any]) -> str:
    failed = [check for check in job.get("checks", []) if not check.get("passed")]
    if failed:
        item = failed[0]
        detail = item.get("detail")
        if isinstance(detail, dict) and detail.get("failure_reason"):
            return f"{item.get('check')}: {detail.get('failure_reason')}"
        return f"{item.get('check')}: {detail}"
    resolution = job.get("source_resolution") or {}
    if resolution.get("failure_reason"):
        return str(resolution["failure_reason"])
    return "preflight failed before reconstruction"


def _write_summary(path: Path, records: list[dict[str, Any]], preflight: dict[str, Any]) -> None:
    display_records = list(records)
    if not display_records:
        for job in preflight.get("jobs", []):
            resolution = job.get("source_resolution") or {}
            selected = resolution.get("selected") or {}
            files = " / ".join(filter(None, [
                job.get("resolved_pdf_name") or selected.get("pdf"),
                job.get("resolved_docx_name") or selected.get("docx"),
            ]))
            display_records.append({
                "name": str(job.get("name", "job")),
                "role": str(job.get("role", "secondary")),
                "pages": str(job.get("pages", "")),
                "status": "PREFLIGHT PASS" if job.get("passed") else "PREFLIGHT FAIL",
                "detail": _failure_detail(job),
                "output": "",
                "files_text": files,
            })

    rows = []
    for record in display_records:
        links = []
        output_value = str(record.get("output") or "")
        if output_value:
            output = Path(output_value)
            report = output / "report" / "index.html"
            if report.exists():
                links.append(f'<a href="{html.escape(report.as_uri())}">report</a>')
            docx = list(output.glob("native_page_structure_*.docx"))
            pdf = list(output.glob("native_page_structure_*.pdf"))
            if docx:
                links.append(f'<a href="{html.escape(docx[0].as_uri())}">DOCX</a>')
            if pdf:
                links.append(f'<a href="{html.escape(pdf[0].as_uri())}">PDF</a>')
        files_text = str(record.get("files_text") or "")
        file_cell = " · ".join(links) if links else html.escape(files_text)
        css = "pass" if "PASS" in str(record.get("status")) and "FAIL" not in str(record.get("status")) else "fail"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(record.get('name', '')))}</td>"
            f"<td>{html.escape(str(record.get('role', '')))}</td>"
            f"<td>{html.escape(str(record.get('pages', '')))}</td>"
            f"<td class='{css}'>{html.escape(str(record.get('status', '')))}</td>"
            f"<td>{html.escape(str(record.get('detail', '')))}</td>"
            f"<td>{file_cell}</td>"
            "</tr>"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    duplicate_note = ""
    if preflight.get("duplicate_sources"):
        duplicate_note = "<p class='fail'><strong>Two jobs resolved to the same source pair.</strong></p>"
    root_note = f"<p>Project root: <code>{html.escape(str(preflight.get('project_root', '')))}</code></p>"
    path.write_text(
        "<!doctype html><meta charset='utf-8'><title>Strict regression</title>"
        "<style>body{font-family:Arial;margin:24px}table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #bbb;padding:8px;text-align:left;vertical-align:top}th{background:#eee}"
        ".pass{color:#087a23;font-weight:700}.fail{color:#b00020;font-weight:700}code{background:#f2f2f2;padding:2px 4px}</style>"
        "<h1>PDF-guided DOCX reconstruction — strict regression v0.8.5 auto diagnostic</h1>"
        f"<p>Preflight: <strong>{html.escape(str(preflight.get('status')).upper())}</strong></p>"
        + root_note + duplicate_note
        + "<table><tr><th>Job</th><th>Role</th><th>Pages</th><th>Status</th><th>Gate detail</th><th>Files</th></tr>"
        + "".join(rows)
        + "</table>",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run strict folder-based multi-document regression")
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.output_root.mkdir(parents=True, exist_ok=True)
    preflight_path = args.output_root / "preflight_report.json"
    preflight = run_preflight(args.jobs, preflight_path)
    if preflight.get("status") != "passed":
        print("\nPRECHECK FAILED. No reconstruction job was started.")
        print(f"Report: {preflight_path}")
        _write_summary(args.output_root / "batch_summary.html", [], preflight)
        return 11

    config = json.loads(args.jobs.read_text(encoding="utf-8"))
    base = args.jobs.parent.resolve()
    jobs = [job for job in config.get("jobs", []) if job.get("enabled", True)]
    jobs.sort(key=lambda job: 0 if str(job.get("role")) == "golden" else 1)
    records: list[dict[str, Any]] = []
    golden_passed = False

    for position, job in enumerate(jobs, start=1):
        role = str(job.get("role", "secondary"))
        name = _safe_name(str(job.get("name") or f"job_{position}"))
        pages = str(job.get("pages", "1"))
        expected_pages = len(_expand_pages(pages))
        preflight_job = next((item for item in preflight.get("jobs", []) if item.get("name") == job.get("name")), None)
        if not preflight_job or not preflight_job.get("passed"):
            records.append({
                "name": name, "role": role, "pages": pages,
                "status": "FAIL", "detail": "missing passed preflight resolution",
                "output": str((args.output_root / name).resolve()),
            })
            if role == "golden":
                golden_passed = False
            continue
        pdf = Path(str(preflight_job["pdf"])).resolve()
        docx = Path(str(preflight_job["docx"])).resolve()
        output = (args.output_root / name).resolve()

        if role != "golden" and not golden_passed:
            records.append({
                "name": name, "role": role, "pages": pages,
                "status": "SKIPPED", "detail": "golden job did not pass",
                "output": str(output),
            })
            continue

        cmd = [
            sys.executable, "-m", "pdf_word_reconstructor.cli",
            "--pdf", str(pdf), "--docx", str(docx),
            "--pages", pages, "--output", str(output),
            "--calibration", str(job.get("calibration", "fast")),
            "--strict-page-count",
        ]
        print(f"\n=== [{position}] {name} ({role}) pages {pages} ===")
        completed = subprocess.run(cmd, check=False)
        passed, detail = _strict_success(output, expected_pages)
        if completed.returncode != 0:
            passed = False
            detail = f"process exit={completed.returncode}; {detail}"
        status = "PASS" if passed else "FAIL"
        records.append({
            "name": name, "role": role, "pages": pages,
            "status": status, "detail": detail, "output": str(output),
        })
        if role == "golden":
            golden_passed = passed
            if not passed:
                print("\nGOLDEN JOB FAILED. Secondary jobs will not run.")

    summary = args.output_root / "batch_summary.html"
    _write_summary(summary, records, preflight)
    print(f"\nStrict regression summary: {summary}")
    return 0 if records and all(item["status"] == "PASS" for item in records) else 21


def _expand_pages(value: str) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start, end = int(left), int(right)
            if start > end:
                start, end = end, start
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(pages)


if __name__ == "__main__":
    raise SystemExit(main())
