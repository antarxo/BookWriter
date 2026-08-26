from __future__ import annotations

import argparse
import json
import mimetypes
import uuid
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pdf_extraction_probe import _find_pdftotext, run_probe

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime" / "pdf_extraction_probe_runs"
HTML = ROOT / "pdf_extraction_probe.html"


def _json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_file(handler: BaseHTTPRequestHandler, path: Path, *, download_name: str | None = None) -> None:
    if not path.exists() or not path.is_file():
        handler.send_error(404)
        return
    body = path.read_bytes()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    if download_name:
        handler.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
    handler.end_headers()
    handler.wfile.write(body)


def _parse_multipart(handler: BaseHTTPRequestHandler) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Αναμενόταν multipart upload.")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError as exc:
        raise ValueError("Μη έγκυρο Content-Length.") from exc
    if length <= 0:
        raise ValueError("Κενό upload.")

    body = handler.rfile.read(length)
    envelope = (
        f"Content-Type: {content_type}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + body
    message = BytesParser(policy=default).parsebytes(envelope)

    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    if not message.is_multipart():
        raise ValueError("Το multipart body δεν αναγνωρίστηκε.")

    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files[str(name)] = (Path(str(filename)).name, payload)
        else:
            charset = part.get_content_charset() or "utf-8"
            try:
                fields[str(name)] = payload.decode(charset, errors="replace")
            except LookupError:
                fields[str(name)] = payload.decode("utf-8", errors="replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "PdfExtractionProbe/0.3"

    def log_message(self, format: str, *args) -> None:
        print(format % args)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/pdf_extraction_probe.html"}:
            _send_file(self, HTML)
            return
        if path == "/api/status":
            poppler = _find_pdftotext()
            _json(self, {
                "ok": True,
                "popplerFound": bool(poppler),
                "pdftotext": str(poppler) if poppler else None,
            })
            return
        if path.startswith("/api/run/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) != 4:
                self.send_error(404)
                return
            run_id, kind = parts[2], parts[3]
            run_dir = RUNTIME / run_id
            if kind == "txt":
                _send_file(self, run_dir / "output" / "PDF_EXTRACTION_COMPARISON.txt", download_name="PDF_EXTRACTION_COMPARISON.txt")
                return
            if kind == "json":
                _send_file(self, run_dir / "output" / "PDF_EXTRACTION_COMPARISON.json", download_name="PDF_EXTRACTION_COMPARISON.json")
                return
            self.send_error(404)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/compare":
            self.send_error(404)
            return

        poppler = _find_pdftotext()
        if not poppler:
            _json(self, {"ok": False, "error": "Δεν βρέθηκε Poppler/pdftotext.exe."}, 503)
            return

        try:
            fields, files = _parse_multipart(self)
        except Exception as exc:
            _json(self, {"ok": False, "error": str(exc)}, 400)
            return

        pages = str(fields.get("pages") or "20,26,29").strip()
        pdf_upload = files.get("pdf")
        if not pdf_upload:
            _json(self, {"ok": False, "error": "Δεν επιλέχθηκε PDF."}, 400)
            return
        filename, pdf_bytes = pdf_upload
        if Path(filename).suffix.lower() != ".pdf":
            _json(self, {"ok": False, "error": "Το αρχείο πρέπει να είναι PDF."}, 400)
            return
        if not pdf_bytes:
            _json(self, {"ok": False, "error": "Το PDF upload είναι κενό."}, 400)
            return

        run_id = str(uuid.uuid4())
        run_dir = RUNTIME / run_id
        upload_dir = run_dir / "upload"
        output_dir = run_dir / "output"
        upload_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = upload_dir / "source.pdf"
        pdf_path.write_bytes(pdf_bytes)

        try:
            result = run_probe(pdf_path, pages, output_dir, str(poppler))
        except Exception as exc:
            _json(self, {"ok": False, "runId": run_id, "error": str(exc)}, 500)
            return

        summary = []
        for row in result["data"].get("pages", []):
            py = row.get("pymupdf") or {}
            po = row.get("poppler") or {}
            summary.append({
                "page": row.get("page"),
                "pymupdf": {
                    "blocks": py.get("blockCount"),
                    "lines": py.get("lineCount"),
                    "spans": py.get("spanCount"),
                },
                "poppler": {
                    "lines": po.get("lineCount"),
                    "words": po.get("wordCount"),
                },
            })
        _json(self, {
            "ok": True,
            "runId": run_id,
            "filename": filename,
            "pages": pages,
            "summary": summary,
            "txtUrl": f"/api/run/{run_id}/txt",
            "jsonUrl": f"/api/run/{run_id}/json",
        })


def main() -> None:
    parser = argparse.ArgumentParser(description="Local PDF extraction comparison server.")
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8776)
    args = parser.parse_args()

    RUNTIME.mkdir(parents=True, exist_ok=True)
    host = "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), Handler)
    url = f"http://{host}:{args.port}/"
    print(f"PDF extraction probe: {url}", flush=True)
    if args.open_browser:
        webbrowser.open(url, new=2)
    server.serve_forever()


if __name__ == "__main__":
    main()
