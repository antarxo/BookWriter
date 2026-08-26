#!/usr/bin/env python3
"""Shared Mathpix Files API engine for GUI and converter/CLI use."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

import requests

TERMINAL_STATES = {"completed", "error"}
APP_KEY_ENV = "MATHPIX_APP_KEY"
PAGE_RANGES_RE = re.compile(r"^\s*\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)*\s*$")


class MathpixBridgeError(RuntimeError):
    pass


def validate_page_ranges(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not PAGE_RANGES_RE.fullmatch(value):
        raise MathpixBridgeError(
            'Invalid page range. Use forms such as "15", "15-20" or "2,7,12-16".'
        )
    parts: list[str] = []
    for token in value.split(","):
        token = token.strip()
        if "-" in token:
            a, b = token.split("-", 1)
            if int(a) < 1 or int(b) < int(a):
                raise MathpixBridgeError(f"Invalid page range: {token}")
        elif int(token) < 1:
            raise MathpixBridgeError(f"Invalid page number: {token}")
        parts.append(token)
    return ",".join(parts)


def api_base(use_eu: bool) -> str:
    return "https://eu.api.mathpix.com" if use_eu else "https://api.mathpix.com"


def _raise_for_response(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    try:
        body = response.json()
        detail = json.dumps(body, ensure_ascii=False)
    except Exception:
        detail = response.text[:4000]
    raise MathpixBridgeError(f"{action} failed: HTTP {response.status_code}\n{detail}")


def submit_file(
    app_key: str,
    source_file: Path,
    *,
    page_ranges: str | None = None,
    use_eu: bool = True,
    improve_mathpix: bool = False,
) -> str:
    # mmd is always produced. mmd.zip must be explicitly requested so that
    # the result is self-contained and carries all inline images locally.
    opts: dict[str, Any] = {
        "metadata": {"improve_mathpix": improve_mathpix},
        "conversion_formats": {"mmd.zip": True},
    }
    normalized_pages = validate_page_ranges(page_ranges)
    if normalized_pages:
        opts["page_ranges"] = normalized_pages

    with source_file.open("rb") as fh:
        response = requests.post(
            f"{api_base(use_eu)}/files/v1",
            headers={"app_key": app_key},
            files={"file": (source_file.name, fh, "application/pdf")},
            data={"options_json": json.dumps(opts)},
            timeout=120,
        )
    _raise_for_response(response, "Local file submission")
    data = response.json()
    file_id = data.get("file_id")
    if not file_id:
        raise MathpixBridgeError(f"No file_id in submission response: {data}")
    return str(file_id)


def poll_status(
    app_key: str,
    file_id: str,
    *,
    use_eu: bool = True,
    interval: float = 2.0,
    timeout_seconds: float = 900.0,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = requests.get(
            f"{api_base(use_eu)}/files/v1/{file_id}",
            headers={"app_key": app_key},
            timeout=60,
        )
        _raise_for_response(response, "Status poll")
        data = response.json()
        if on_progress:
            on_progress(data)
        status = str(data.get("status", "unknown"))
        if status in TERMINAL_STATES:
            return data
        if time.monotonic() >= deadline:
            raise MathpixBridgeError(f"Timed out after {timeout_seconds:.0f}s waiting for {file_id}")
        time.sleep(max(0.5, interval))


def download_output(app_key: str, file_id: str, extension: str, *, use_eu: bool = True) -> bytes:
    response = requests.get(
        f"{api_base(use_eu)}/files/v1/{file_id}.{extension}",
        headers={"app_key": app_key},
        timeout=180,
    )
    _raise_for_response(response, f"Download {extension}")
    return response.content


def inspect_lines_json(raw: bytes) -> dict[str, Any]:
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


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def default_output_for_pdf(pdf: Path) -> Path:
    return pdf.parent / f"{pdf.stem}_mathpix"


def _safe_extract_zip(zip_path: Path, destination: Path) -> list[str]:
    """Extract a ZIP without allowing paths to escape destination."""
    destination = destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    extracted: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise MathpixBridgeError(
                    f"Unsafe path in Mathpix ZIP: {info.filename}"
                ) from exc
            archive.extract(info, destination)
            if not info.is_dir():
                extracted.append(info.filename.replace("\\", "/"))
    return extracted


def _package_summary(package_dir: Path, members: list[str]) -> dict[str, Any]:
    mmd_files = [name for name in members if name.lower().endswith(".mmd")]
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
    images = [name for name in members if Path(name).suffix.lower() in image_exts]
    return {
        "directory": package_dir.name,
        "file_count": len(members),
        "mmd_files": mmd_files,
        "image_count": len(images),
        "image_files": images,
    }


def run_conversion(
    source: Path,
    app_key: str,
    *,
    output_dir: Path | None = None,
    page_ranges: str | None = None,
    use_eu: bool = True,
    timeout_seconds: float = 900.0,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise MathpixBridgeError(f"Invalid PDF: {source}")
    if not app_key.strip():
        raise MathpixBridgeError("Missing Mathpix APP KEY")
    normalized_pages = validate_page_ranges(page_ranges)
    run_dir = (output_dir or default_output_for_pdf(source)).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    file_id = submit_file(
        app_key.strip(), source,
        page_ranges=normalized_pages,
        use_eu=use_eu,
        improve_mathpix=False,
    )
    status = poll_status(
        app_key.strip(), file_id,
        use_eu=use_eu,
        timeout_seconds=timeout_seconds,
        on_progress=on_progress,
    )
    write_json(run_dir / "status.json", status)
    if status.get("status") != "completed":
        raise MathpixBridgeError("Mathpix ended in error state. See status.json.")

    # Always-produced canonical/diagnostic outputs.
    mmd = download_output(app_key.strip(), file_id, "mmd", use_eu=use_eu)
    (run_dir / "result.mmd").write_bytes(mmd)
    lines = download_output(app_key.strip(), file_id, "lines.json", use_eu=use_eu)
    (run_dir / "result.lines.json").write_bytes(lines)

    # Self-contained Mathpix Markdown package with all referenced images.
    mmd_zip = download_output(app_key.strip(), file_id, "mmd.zip", use_eu=use_eu)
    zip_path = run_dir / "result.mmd.zip"
    zip_path.write_bytes(mmd_zip)
    package_dir = run_dir / "mmd_package"
    members = _safe_extract_zip(zip_path, package_dir)
    package = _package_summary(package_dir, members)

    summary = inspect_lines_json(lines)
    manifest = {
        "bridge_version": 2,
        "status": "completed",
        "execution_path": "Mathpix Files API /files/v1",
        "api_region": "eu" if use_eu else "global",
        "source_pdf": str(source),
        "requested_pages": normalized_pages,
        "output_directory": str(run_dir),
        "file_id": file_id,
        "outputs": {
            "mmd": "result.mmd",
            "mmd_zip": "result.mmd.zip",
            "mmd_package": "mmd_package",
            "lines_json": "result.lines.json",
            "status": "status.json",
        },
        "mmd_package": package,
        "lines_summary": summary,
    }
    write_json(run_dir / "manifest.json", manifest)
    return manifest


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mathpix bridge for BookWriter/converter use")
    parser.add_argument("--input", required=True, type=Path, help="Input PDF")
    parser.add_argument("--pages", default="", help='Page ranges, e.g. "15" or "2,7,12-16"')
    parser.add_argument("--output", type=Path, help="Output directory; default is <pdf>_mathpix")
    parser.add_argument("--global-endpoint", action="store_true", help="Use global instead of EU endpoint")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args(argv)

    app_key = os.environ.get(APP_KEY_ENV, "").strip()
    if not app_key:
        print(f"ERROR: {APP_KEY_ENV} is not set", file=sys.stderr)
        return 3

    try:
        last_marker: tuple[Any, Any] | None = None

        def progress(data: dict[str, Any]) -> None:
            nonlocal last_marker
            marker = (data.get("status"), data.get("percent_done"))
            if marker != last_marker:
                print(f"[Mathpix] status={marker[0]} percent={marker[1]}")
                last_marker = marker

        manifest = run_conversion(
            args.input,
            app_key,
            output_dir=args.output,
            page_ranges=args.pages,
            use_eu=not args.global_endpoint,
            timeout_seconds=max(10.0, args.timeout),
            on_progress=progress,
        )
        print(json.dumps(manifest, ensure_ascii=False))
        return 0
    except MathpixBridgeError as exc:
        text = str(exc)
        print(f"ERROR: {text}", file=sys.stderr)
        if "Invalid PDF" in text or "Invalid page" in text:
            return 2
        if "401" in text or "unauthorized" in text.lower() or "APP KEY" in text:
            return 3
        if "Timed out" in text:
            return 4
        return 1
    except (requests.RequestException, zipfile.BadZipFile) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
