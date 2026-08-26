#!/usr/bin/env python3
"""
Mathpix Files API diagnostic probe.

Purpose:
  PDF/URL -> Mathpix async processing -> MMD + lines.json + status metadata

This is intentionally isolated from the BookWriter production converter.
It is a diagnostic-only execution path for validating Mathpix output before
we wire anything into the canonical pipeline.

Authentication:
  Set MATHPIX_APP_KEY in the environment. Do NOT commit the key.

Examples (PowerShell):
  $env:MATHPIX_APP_KEY="your-secret-key"
  python tools/mathpix_probe.py --url https://cdn.mathpix.com/examples/cs229-notes1.pdf

  # Local-file upload path (Files API multipart):
  python tools/mathpix_probe.py --file "E:\\path\\sample.pdf"

Outputs:
  mathpix_output/<name>/
    status.json
    result.mmd
    result.lines.json
    manifest.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: requests. Install it with: py -m pip install requests"
    ) from exc


API_BASE = os.environ.get("MATHPIX_API_BASE", "https://api.mathpix.com").rstrip("/")
APP_KEY_ENV = "MATHPIX_APP_KEY"
TERMINAL_STATES = {"completed", "error"}


class MathpixProbeError(RuntimeError):
    pass


def _raise_for_response(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    body = response.text[:4000]
    raise MathpixProbeError(
        f"{action} failed: HTTP {response.status_code}\n{body}"
    )


def _headers(app_key: str) -> dict[str, str]:
    return {"app_key": app_key}


def submit_url(app_key: str, source_url: str) -> str:
    """Submit a public/presigned URL through Files API."""
    response = requests.post(
        f"{API_BASE}/files/v1/uri",
        headers={**_headers(app_key), "Content-Type": "application/json"},
        json={"source_uri": source_url},
        timeout=60,
    )
    _raise_for_response(response, "URL submission")
    data = response.json()
    file_id = data.get("file_id")
    if not file_id:
        raise MathpixProbeError(f"No file_id in submission response: {data}")
    return str(file_id)


def submit_file(app_key: str, source_file: Path) -> str:
    """
    Submit a local document through Files API direct multipart upload.

    Mathpix documents POST /files/v1 as the direct-upload counterpart of
    POST /files/v1/uri. No conversion format is requested because MMD and
    lines.json are diagnostic outputs downloaded after completion.
    """
    with source_file.open("rb") as fh:
        response = requests.post(
            f"{API_BASE}/files/v1",
            headers=_headers(app_key),
            files={"file": (source_file.name, fh, "application/pdf")},
            timeout=120,
        )
    _raise_for_response(response, "Local file submission")
    data = response.json()
    file_id = data.get("file_id")
    if not file_id:
        raise MathpixProbeError(f"No file_id in submission response: {data}")
    return str(file_id)


def poll_status(
    app_key: str,
    file_id: str,
    interval: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last_status: str | None = None

    while True:
        response = requests.get(
            f"{API_BASE}/files/v1/{file_id}",
            headers=_headers(app_key),
            timeout=60,
        )
        _raise_for_response(response, "Status poll")
        data = response.json()
        status = str(data.get("status", "unknown"))
        percent = data.get("percent_done")

        if status != last_status or percent is not None:
            suffix = f" ({percent}%)" if percent is not None else ""
            print(f"[Mathpix] status={status}{suffix}")
            last_status = status

        if status in TERMINAL_STATES:
            return data

        if time.monotonic() >= deadline:
            raise MathpixProbeError(
                f"Timed out after {timeout_seconds:.0f}s waiting for {file_id}"
            )
        time.sleep(interval)


def download_output(app_key: str, file_id: str, extension: str) -> bytes:
    response = requests.get(
        f"{API_BASE}/files/v1/{file_id}.{extension}",
        headers=_headers(app_key),
        timeout=120,
    )
    _raise_for_response(response, f"Download {extension}")
    return response.content


def inspect_lines_json(raw: bytes) -> dict[str, Any]:
    """Return a small schema-oriented summary without mutating the source JSON."""
    data = json.loads(raw.decode("utf-8"))
    pages = data.get("pages") if isinstance(data, dict) else None
    pages = pages if isinstance(pages, list) else []

    total_lines = 0
    types: dict[str, int] = {}
    fields: set[str] = set()

    for page in pages:
        if not isinstance(page, dict):
            continue
        lines = page.get("lines")
        if not isinstance(lines, list):
            continue
        total_lines += len(lines)
        for item in lines:
            if not isinstance(item, dict):
                continue
            fields.update(item.keys())
            item_type = str(item.get("type", "<missing>"))
            types[item_type] = types.get(item_type, 0) + 1

    return {
        "page_count": len(pages),
        "line_object_count": total_lines,
        "line_types": dict(sorted(types.items(), key=lambda x: (-x[1], x[0]))),
        "observed_line_fields": sorted(fields),
    }


def safe_output_name(source: str) -> str:
    name = Path(source).stem if "://" not in source else source.rstrip("/").split("/")[-1]
    name = Path(name).stem or "mathpix_probe"
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    cleaned = "".join(c if c in allowed else "_" for c in name)
    return cleaned[:80] or "mathpix_probe"


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic Mathpix Files API probe: get MMD + lines.json"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Local PDF/document path")
    source.add_argument("--url", help="Public or presigned document URL")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("mathpix_output"),
        help="Root output directory (default: mathpix_output)",
    )
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    args = parser.parse_args()

    app_key = os.environ.get(APP_KEY_ENV, "").strip()
    if not app_key:
        print(
            f"ERROR: environment variable {APP_KEY_ENV} is not set.\n"
            "PowerShell example:\n"
            '  $env:MATHPIX_APP_KEY="<paste key locally>"',
            file=sys.stderr,
        )
        return 2

    if args.file:
        source_file = args.file.expanduser().resolve()
        if not source_file.is_file():
            print(f"ERROR: file not found: {source_file}", file=sys.stderr)
            return 2
        source_label = str(source_file)
    else:
        source_file = None
        source_label = str(args.url)

    run_dir = args.output_dir / safe_output_name(source_label)
    run_dir.mkdir(parents=True, exist_ok=True)

    try:
        print("[Mathpix] submitting document...")
        if source_file is not None:
            file_id = submit_file(app_key, source_file)
        else:
            file_id = submit_url(app_key, str(args.url))

        print(f"[Mathpix] file_id={file_id}")
        status = poll_status(
            app_key,
            file_id,
            interval=max(0.5, args.poll_seconds),
            timeout_seconds=max(10.0, args.timeout_seconds),
        )
        write_json(run_dir / "status.json", status)

        if status.get("status") != "completed":
            raise MathpixProbeError(
                "Mathpix ended in error state. See status.json for details."
            )

        print("[Mathpix] downloading result.mmd...")
        mmd = download_output(app_key, file_id, "mmd")
        (run_dir / "result.mmd").write_bytes(mmd)

        print("[Mathpix] downloading result.lines.json...")
        lines = download_output(app_key, file_id, "lines.json")
        (run_dir / "result.lines.json").write_bytes(lines)

        lines_summary = inspect_lines_json(lines)
        manifest = {
            "probe_version": 1,
            "execution_path": "Mathpix Files API /files/v1",
            "source": source_label,
            "file_id": file_id,
            "outputs": {
                "mmd": "result.mmd",
                "lines_json": "result.lines.json",
                "status": "status.json",
            },
            "lines_summary": lines_summary,
        }
        write_json(run_dir / "manifest.json", manifest)

        print("[Mathpix] DONE")
        print(f"[Mathpix] output: {run_dir.resolve()}")
        print(
            f"[Mathpix] pages={lines_summary['page_count']} "
            f"line_objects={lines_summary['line_object_count']}"
        )
        if lines_summary["line_types"]:
            print("[Mathpix] observed types:")
            for key, count in lines_summary["line_types"].items():
                print(f"  {key}: {count}")
        return 0

    except (MathpixProbeError, requests.RequestException, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
