#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
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


def safe_name(value: str, default: str, suffix: str) -> str:
    name = Path(unquote(value or default)).name
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name).strip(" .") or default
    if not name.lower().endswith(suffix):
        name = Path(name).stem + suffix
    return name


class Handler(SimpleHTTPRequestHandler):
    server_version = "DonorlessReconstructionGateway/1.0"

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
        match = re.fullmatch(r"/api/donorless-run/([a-f0-9-]{36})/(report|docx)", path)
        if match:
            token, kind = match.groups()
            run_dir = RUNTIME_ROOT / token
            target = run_dir / ("DONORLESS_REPORT.json" if kind == "report" else "reconstructed.docx")
            if not target.exists():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            data = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            if kind == "report":
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
            pdf_path = upload_dir / safe_name(pdf_part.get("filename", "source.pdf"), "source.pdf", ".pdf")
            markdown_path = upload_dir / safe_name(markdown_part.get("filename", "markdown.zip"), "markdown.zip", ".zip")
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
            self.json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False,
                "error": str(exc),
                "type": type(exc).__name__,
            })


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
