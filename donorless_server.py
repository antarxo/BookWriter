#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
PIPELINE_ROOT = ROOT / "PdfWordCanonicalPipeline"
RUNTIME_ROOT = ROOT / "runtime" / "donorless_runs"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(PIPELINE_ROOT / "src"))

from pdf_word_reconstructor.donorless_runner import run_donorless_reconstruction  # noqa: E402

BUILD = "DONORLESS-PDF-MARKDOWN-BASELINE-20260826"
MAX_UPLOAD = 700 * 1024 * 1024


def parse_multipart_form(content_type: str, body: bytes) -> dict[str, dict]:
    match = re.search(r'boundary=("?)([^";]+)\1', content_type or "")
    if not match:
        raise ValueError("Missing multipart boundary")
    delimiter = b"--" + match.group(2).encode("latin-1")
    fields: dict[str, dict] = {}
    for raw in body.split(delimiter):
        part = raw.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        headers_raw, payload = part.split(b"\r\n\r\n", 1)
        headers = headers_raw.decode("latin-1", "replace")
        disp = next((line for line in headers.splitlines() if line.lower().startswith("content-disposition:")), "")
        name_match = re.search(r'name="([^"]+)"', disp)
        if not name_match:
            continue
        filename_match = re.search(r'filename="([^"]*)"', disp)
        fields[name_match.group(1)] = {
            "filename": filename_match.group(1) if filename_match else "",
            "content": payload.rstrip(b"\r\n"),
            "text": payload.decode("utf-8", "replace").strip(),
        }
    return fields


def _read_json(path: Path) -> dict:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except Exception:
        pass
    return {}


def _benchmark_error_suffix(benchmark: dict) -> str:
    samples = (((benchmark.get("unresolved") or {}).get("nonEquationSamples")) or [])
    if not samples:
        return ""

    selected: list[dict] = []
    per_kind: dict[str, int] = {}
    for sample in samples:
        kind = str(sample.get("outputKind") or "unknown")
        if per_kind.get(kind, 0) >= 2:
            continue
        selected.append(sample)
        per_kind[kind] = per_kind.get(kind, 0) + 1
        if len(selected) >= 8:
            break

    parts: list[str] = []
    for sample in selected:
        text = re.sub(r"\s+", " ", str(sample.get("textPreview") or "")).strip()
        if len(text) > 110:
            text = text[:109] + "…"
        page = sample.get("page") or 0
        parts.append(
            f"{sample.get('outputKind')}/{sample.get('markdownType')} "
            f"{sample.get('markdownId')}@p{page} "
            f"slot={sample.get('slotId') or '∅'} {text!r}"
        )
    return " | non-equation samples: " + " || ".join(parts) if parts else ""


def _visual_error_suffix(visual_binding: dict) -> str:
    pages = visual_binding.get("pages") if isinstance(visual_binding, dict) else []
    parts: list[str] = []
    for row in pages or []:
        md = int(row.get("unplacedMarkdownVisualCount") or 0)
        if md <= 0:
            continue
        groups = int(row.get("pdfFigureGroupCount") or 0)
        bound = int(row.get("boundCount") or 0)
        parts.append(f"p{row.get('page')}:MD={md}/groups={groups}/bound={bound}")
        if len(parts) >= 12:
            break
    return " | visual groups: " + "; ".join(parts) if parts else ""


def _write_failure_bundle(run_dir: Path, token: str, exc: Exception) -> dict:
    analysis_dir = run_dir / "analysis"
    benchmark = _read_json(analysis_dir / "BENCHMARK_REPORT.json")
    build_contract = _read_json(analysis_dir / "build_contract.json")
    equation_audit = _read_json(analysis_dir / "equation_classification_audit.json")
    page_layout_spine = _read_json(analysis_dir / "page_layout_spine.json")
    visual_binding = page_layout_spine.get("visualGroupBinding") if isinstance(page_layout_spine.get("visualGroupBinding"), dict) else {}
    page_alignment = _read_json(analysis_dir / "markdown_pdf_page_alignment.json")
    if not page_alignment:
        spine = _read_json(analysis_dir / "markdown_pdf_spine.json")
        page_alignment = spine.get("pageAlignmentFallback") if isinstance(spine.get("pageAlignmentFallback"), dict) else {}

    failure = {
        "version": "donorless-failure-0.2",
        "runId": token,
        "build": BUILD,
        "exception": {
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
        "benchmark": benchmark,
        "buildContractSummary": build_contract.get("summary") or {},
        "equationAudit": {
            "version": equation_audit.get("version"),
            "unresolvedDisplayEquationCount": equation_audit.get("unresolvedDisplayEquationCount"),
            "equationGroupBinding": equation_audit.get("equationGroupBinding") or {},
        } if equation_audit else {},
        "visualGroupBinding": visual_binding,
        "pageAlignmentFallback": page_alignment or {},
    }
    (run_dir / "DONORLESS_FAILURE.json").write_text(
        json.dumps(failure, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return failure


class Handler(SimpleHTTPRequestHandler):
    server_version = "DonorlessReconstructionGateway/1.2"

    def translate_path(self, path: str) -> str:
        relative = Path(unquote(urlparse(path).path).lstrip("/"))
        candidate = (ROOT / relative).resolve()
        if ROOT.resolve() not in candidate.parents and candidate != ROOT.resolve():
            return str(ROOT / "__blocked__")
        return str(candidate)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Donorless-Build", BUILD)
        super().end_headers()

    def json_response(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/donorless-status":
            self.json_response(HTTPStatus.OK, {"ok": True, "build": BUILD, "mode": "pdf-markdown-donorless-baseline"})
            return
        match = re.fullmatch(r"/api/donorless-run/([a-f0-9-]{36})/(report|failure|docx)", path)
        if match:
            token, kind = match.groups()
            run_dir = RUNTIME_ROOT / token
            names = {
                "report": "DONORLESS_REPORT.json",
                "failure": "DONORLESS_FAILURE.json",
                "docx": "reconstructed.docx",
            }
            target = run_dir / names[kind]
            if not target.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            if kind in {"report", "failure"}:
                self.send_header("Content-Type", "application/json; charset=utf-8")
            else:
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote('reconstructed.docx')}")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/donorless-convert":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        token: str | None = None
        run_dir: Path | None = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPLOAD:
                raise ValueError("Invalid upload size")
            fields = parse_multipart_form(self.headers.get("Content-Type", ""), self.rfile.read(length))
            pdf_part = fields.get("pdf")
            markdown_part = fields.get("markdown")
            pages = (fields.get("pages") or {}).get("text", "").strip()
            if not pdf_part or not pdf_part.get("content"):
                raise ValueError("Λείπει το αρχικό PDF.")
            if not markdown_part or not markdown_part.get("content"):
                raise ValueError("Λείπει το Mathpix Markdown ZIP.")
            if not pages:
                raise ValueError("Λείπει το εύρος σελίδων.")

            token = str(uuid.uuid4())
            run_dir = RUNTIME_ROOT / token
            upload_dir = run_dir / "_uploads"
            upload_dir.mkdir(parents=True, exist_ok=True)

            pdf_path = upload_dir / "source.pdf"
            markdown_path = upload_dir / "markdown.zip"
            pdf_path.write_bytes(pdf_part["content"])
            markdown_path.write_bytes(markdown_part["content"])

            report = run_donorless_reconstruction(
                pdf_path=pdf_path,
                markdown_zip=markdown_path,
                pages_spec=pages,
                output_dir=run_dir,
            )
            self.json_response(HTTPStatus.OK, {
                "ok": True,
                "token": token,
                "mode": "pdf-markdown-donorless-baseline",
                "outputName": "reconstructed.docx",
                "downloadUrl": f"/api/donorless-run/{token}/docx",
                "reportUrl": f"/api/donorless-run/{token}/report",
                "report": report,
            })
        except Exception as exc:
            failure: dict = {}
            if token and run_dir:
                try:
                    failure = _write_failure_bundle(run_dir, token, exc)
                except Exception:
                    failure = {}
            benchmark = failure.get("benchmark") if isinstance(failure.get("benchmark"), dict) else {}
            visual_binding = failure.get("visualGroupBinding") if isinstance(failure.get("visualGroupBinding"), dict) else {}
            error = str(exc) + _benchmark_error_suffix(benchmark) + _visual_error_suffix(visual_binding)
            payload = {
                "ok": False,
                "error": error,
                "type": type(exc).__name__,
            }
            if token:
                payload["token"] = token
                payload["failureUrl"] = f"/api/donorless-run/{token}/failure"
            self.json_response(HTTPStatus.INTERNAL_SERVER_ERROR, payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"Donorless PDF/Markdown gateway: http://{args.bind}:{args.port}/mathpix-converter/donorless.html")
    print(f"Build: {BUILD}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
