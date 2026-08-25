#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
import uuid
import zipfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
BOOKWRITER_ROOT = ROOT
PIPELINE_ROOT = ROOT / "PdfWordCanonicalPipeline"
RUNTIME_ROOT = ROOT / "runtime"
REPORT_ROOT = RUNTIME_ROOT / "normalization_reports"
REPORT_ROOT.mkdir(parents=True, exist_ok=True)
MATHPIX_FAILURE_ROOT = RUNTIME_ROOT / "mathpix_failures"
MATHPIX_FAILURE_ROOT.mkdir(parents=True, exist_ok=True)
MATHPIX_ARTIFACT_ROOT = RUNTIME_ROOT / "mathpix_artifacts"
MATHPIX_ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(PIPELINE_ROOT / "src"))
from pdf_word_canonical_pipeline.pipeline import canonicalize, run_fidelity  # noqa: E402

BUILD = "BOOKWRITER-V5-HF55-WORD-TYPOGRAPHY-TRIPLE-PROBE-20260825"
MAX_UPLOAD = 150 * 1024 * 1024
MAX_MATHPIX_UPLOAD = 600 * 1024 * 1024


class GatewayUserError(Exception):
    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


def require_maps_first_contract(
    *,
    architecture_guard: dict | None,
    mapping_fidelity: dict | None,
    conversion_spine: dict | None,
    markdown_pdf_spine: dict | None,
    page_layout_spine: dict | None,
    docx_donor_map: dict | None,
) -> None:
    missing = []
    if architecture_guard is None:
        missing.append("architecture_guard.json")
    if mapping_fidelity is None:
        missing.append("mappingFidelity")
    if conversion_spine is None:
        missing.append("conversionSpine")
    if markdown_pdf_spine is None:
        missing.append("markdownPdfSpine")
    if page_layout_spine is None:
        missing.append("pageLayoutSpine")
    if docx_donor_map is None:
        missing.append("docxDonorMap")
    if missing:
        raise GatewayUserError(
            "Η maps-first μετατροπή σταμάτησε: λείπουν υποχρεωτικοί χάρτες (" + ", ".join(missing) + ").",
            "maps_first_contract_missing",
        )
    if architecture_guard.get("status") == "fail":
        violations = architecture_guard.get("violations") or []
        details = " · ".join(
            str(item.get("message") or item.get("code") or item)
            for item in violations[:4]
        )
        raise GatewayUserError(
            "Η maps-first μετατροπή σταμάτησε από το architecture guard" + (": " + details if details else "."),
            "maps_first_guard_failed",
        )
    if mapping_fidelity.get("status") == "fail":
        violations = mapping_fidelity.get("violations") or []
        details = " · ".join(
            str(item.get("message") or item.get("code") or item)
            for item in violations[:4]
        )
        raise GatewayUserError(
            "Η maps-first μετατροπή σταμάτησε από τον έλεγχο πιστότητας χαρτογράφησης" + (": " + details if details else "."),
            "mapping_fidelity_failed",
        )


def strict_failure_message(output_dir: Path, code: int) -> str:
    recon_dir = output_dir / "01_reconstructed"
    for name in (
        "FAILED_MAPPING_FIDELITY.txt",
        "FAILED_NO_EXACT_PAGE_COUNT.txt",
        "FAILED_NO_RENDERED_CANDIDATE.txt",
        "FAILED_FINAL_OUTPUT_MISSING.txt",
    ):
        path = recon_dir / name
        if path.exists():
            detail = path.read_text(encoding="utf-8", errors="replace").strip()
            if detail:
                return detail
    calibration_path = recon_dir / "analysis" / "calibration.json"
    if calibration_path.exists():
        try:
            calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
            comparison = calibration.get("selected_comparison") or {}
            source_pages = comparison.get("source_page_count") or calibration.get("source_page_count")
            output_pages = comparison.get("output_page_count")
            if source_pages and output_pages:
                return (
                    f"Αποτυχία strict σελιδοποίησης: το PDF έχει {source_pages} σελίδες "
                    f"και ο πλησιέστερος DOCX candidate παρήγαγε {output_pages}."
                )
        except Exception:
            pass
    return f"Το Mathpix fidelity pipeline σταμάτησε με κωδικό {code}."


def runtime_identity() -> dict:
    try:
        book = json.loads((BOOKWRITER_ROOT / "VERSION.json").read_text(encoding="utf-8"))
    except Exception as exc:
        book = {"buildMarker": "", "error": str(exc)}
    try:
        pipe = json.loads((PIPELINE_ROOT / "PIPELINE_VERSION.json").read_text(encoding="utf-8"))
    except Exception as exc:
        pipe = {"build": "", "error": str(exc)}
    book_build = str(book.get("buildMarker") or book.get("build") or "")
    pipeline_build = str(pipe.get("build") or "")
    return {
        "bookWriterBuild": book_build,
        "pipelineBuild": pipeline_build,
        "identityConsistent": None,
        "serverTopology": "repo-root-v5",
        "reconstructorCliHasJsonImport": "import json" in (PIPELINE_ROOT / "src" / "pdf_word_reconstructor" / "cli.py").read_text(encoding="utf-8"),
    }


def safe_filename(value: str) -> str:
    name = Path(unquote(value or "input.docx")).name
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name).strip(" .")
    if not name.lower().endswith(".docx"):
        name += ".docx"
    return name or "input.docx"


def safe_upload_filename(value: str, default: str, allowed_suffixes: set[str]) -> str:
    name = Path(unquote(value or default)).name
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name).strip(" .")
    suffix = Path(name).suffix.lower()
    if suffix not in allowed_suffixes:
        name = f"{Path(name or default).stem or Path(default).stem}{next(iter(allowed_suffixes))}"
    return name or default


def content_disposition_attachment(filename: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(filename or "download").name).strip(" .") or "download"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(Path(filename or 'download').name)}"


def parse_multipart_form(content_type: str, body: bytes) -> dict[str, dict]:
    match = re.search(r'boundary=("?)([^";]+)\1', content_type or "")
    if not match:
        raise ValueError("Missing multipart boundary")
    boundary = match.group(2).encode("latin-1")
    delimiter = b"--" + boundary
    fields: dict[str, dict] = {}
    for raw_part in body.split(delimiter):
        part = raw_part
        if not part:
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"--"):
            part = part[:-2]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if not part:
            continue
        if b"\r\n\r\n" in part:
            header_bytes, payload = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_bytes, payload = part.split(b"\n\n", 1)
        else:
            continue
        headers = header_bytes.decode("latin-1", "replace")
        disposition = ""
        part_type = ""
        for line in headers.splitlines():
            if line.lower().startswith("content-disposition:"):
                disposition = line.split(":", 1)[1].strip()
            elif line.lower().startswith("content-type:"):
                part_type = line.split(":", 1)[1].strip()
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        filename_match = re.search(r'filename="([^"]*)"', disposition)
        fields[name_match.group(1)] = {
            "filename": filename_match.group(1) if filename_match else "",
            "content_type": part_type,
            "content": payload,
            "text": payload.decode("utf-8", "replace").strip(),
        }
    return fields


def extract_first_pdf_from_zip(zip_path: Path, target_dir: Path) -> tuple[Path, str] | None:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            pdf_entries = [
                entry for entry in archive.infolist()
                if not entry.is_dir() and entry.filename.lower().endswith(".pdf")
            ]
            if not pdf_entries:
                return None
            entry = sorted(pdf_entries, key=lambda item: ("/" in item.filename, item.filename.lower()))[0]
            target = target_dir / "source_from_mathpix_package.pdf"
            with archive.open(entry) as source, target.open("wb") as out:
                out.write(source.read())
            return target, Path(entry.filename).name or target.name
    except zipfile.BadZipFile as exc:
        raise GatewayUserError("Το Mathpix αρχείο δεν είναι έγκυρο ZIP.", "bad_zip") from exc


def copy_mathpix_failure_artifacts(temp: Path | None, token: str) -> dict:
    if temp is None or not temp.exists():
        return {}
    target = MATHPIX_FAILURE_ROOT / token
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    patterns = (
        "fidelity/PIPELINE_MANIFEST.json",
        "fidelity/00_input/MATHPIX_INPUT_MANIFEST.json",
        "fidelity/00_input/MATHPIX_INPUT_DIAGNOSTIC.json",
        "fidelity/00_input/SOURCE_PDF.pdf",
        "fidelity/00_input/MARKDOWN_ELEMENT_MAP.json",
        "fidelity/00_input/MARKDOWN_EQUATION_DONORS.json",
        "fidelity/01_reconstructed/logs/run.log",
        "fidelity/01_reconstructed/analysis/*.json",
        "fidelity/01_reconstructed/failed_candidates/*.docx",
        "fidelity/01_reconstructed/failed_candidates/*.pdf",
        "fidelity/02_canonical/*.json",
    )
    for pattern in patterns:
        for source in temp.glob(pattern):
            if not source.is_file():
                continue
            relative = source.relative_to(temp)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
                copied.append(str(relative))
            except OSError:
                pass
    result = {
        "failureArtifactDir": str(target),
        "failureArtifacts": copied,
    }
    failed_docx = target / "fidelity" / "01_reconstructed" / "failed_candidates" / "selected_failed_candidate.docx"
    if failed_docx.exists():
        artifact_dir = MATHPIX_ARTIFACT_ROOT / token
        artifact_dir.mkdir(parents=True, exist_ok=True)
        review_docx = artifact_dir / "reconstructed.docx"
        try:
            shutil.copy2(failed_docx, review_docx)
            result["reconstructedDocxArtifact"] = {
                "fileName": "FAST_REVIEW_STRICT_FAILED_RECONSTRUCTED.docx",
                "downloadUrl": f"/api/mathpix-artifact/{token}/reconstructed-docx",
                "status": "strict-failed-review-candidate",
                "requiresUserReview": True,
                "description": "Fast review DOCX candidate. Δεν πέρασε το strict page gate και δεν θεωρείται τελικό προϊόν.",
            }
            result["reviewCandidateAvailable"] = True
        except OSError:
            pass
    return result


def persist_mathpix_review_assets(report_data: dict | None, artifact_dir: Path, token: str, reconstructed_dir: Path | None = None) -> list[dict]:
    """Copy small visual review assets out of the temporary pipeline folder."""
    if not report_data:
        return []
    fallback_report = report_data.get("fidelityFallbackReport") or {}
    conversion_spine = report_data.get("conversionSpine") or {}
    queues = (
        (conversion_spine.get("decisionQueue") or [],)
        if conversion_spine
        else (
            fallback_report.get("userDecisionQueue") or [],
            fallback_report.get("actionableReviewQueue") or [],
            fallback_report.get("diagnosticReviewQueue") or [],
            fallback_report.get("equationReviewQueue") or [],
        )
    )
    review_dir = artifact_dir / "review_assets"
    copied: list[dict] = []
    seen: dict[str, str] = {}
    counter = 0
    page_sizes: dict[int, dict] = {}
    page_image_urls: dict[int, str] = {}

    if reconstructed_dir:
        page_structure_path = reconstructed_dir / "analysis" / "page_structure.json"
        try:
            page_structure = json.loads(page_structure_path.read_text(encoding="utf-8"))
        except Exception:
            page_structure = {}
        for page in (page_structure or {}).get("pages", []):
            try:
                page_no = int(page.get("page"))
            except Exception:
                continue
            page_sizes[page_no] = {
                "widthPt": page.get("width_pt"),
                "heightPt": page.get("height_pt"),
            }
            render_value = str(page.get("render") or "")
            render_candidates = []
            if render_value:
                render_candidates.extend([
                    reconstructed_dir / render_value,
                    reconstructed_dir / "work" / render_value,
                ])
            render_candidates.extend([
                reconstructed_dir / "work" / "pages" / f"page-{page_no}.png",
                reconstructed_dir / "report" / "assets" / f"page-{page_no}.png",
                reconstructed_dir / "pages" / f"page-{page_no}.png",
            ])
            render_path = next((path for path in render_candidates if path.exists() and path.is_file()), None)
            if render_path:
                target_name = f"pdf-page-{page_no}.png"
                review_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(render_path, review_dir / target_name)
                page_image_urls[page_no] = f"/api/mathpix-artifact/{token}/review-assets/{target_name}"

    def attach_asset(item: dict) -> None:
        nonlocal counter
        raw_path = str(item.get("cropPath") or item.get("previewPath") or "").strip()
        if not raw_path:
            pass
        else:
            source = Path(raw_path)
            if source.exists() and source.is_file():
                key = str(source.resolve())
                if key not in seen:
                    counter += 1
                    suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"} else ".png"
                    target_name = f"review-{counter:03d}{suffix}"
                    review_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, review_dir / target_name)
                    seen[key] = target_name
                    copied.append({
                        "source": raw_path,
                        "fileName": target_name,
                        "url": f"/api/mathpix-artifact/{token}/review-assets/{target_name}",
                    })
                item["previewUrl"] = f"/api/mathpix-artifact/{token}/review-assets/{seen[key]}"
        try:
            page_no = int(item.get("page"))
        except Exception:
            page_no = 0
        if page_no:
            if page_no in page_image_urls:
                item["pdfPagePreviewUrl"] = page_image_urls[page_no]
            if page_no in page_sizes:
                item["pdfPageSize"] = page_sizes[page_no]
        return

    for queue in queues:
        for item in queue:
            if isinstance(item, dict):
                attach_asset(item)
    return copied


class Handler(SimpleHTTPRequestHandler):
    server_version = "BookWriterV5Gateway/1.0"

    def translate_path(self, path: str) -> str:
        raw = urlparse(path).path
        relative = Path(unquote(raw).lstrip("/"))
        candidate = (BOOKWRITER_ROOT / relative).resolve()
        if BOOKWRITER_ROOT.resolve() not in candidate.parents and candidate != BOOKWRITER_ROOT.resolve():
            return str(BOOKWRITER_ROOT / "__blocked__")
        return str(candidate)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-BookWriter-Build", BUILD)
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_canonicalize_mathpix(self, response_product: str = "canonical") -> None:
        requested_token = (self.headers.get("X-BookWriter-Progress-Token") or "").strip().lower()
        token = requested_token if re.fullmatch(r"[a-f0-9-]{36}", requested_token) else str(uuid.uuid4())
        report_path = REPORT_ROOT / f"{token}.json"
        progress_path = REPORT_ROOT / f"{token}.progress.json"
        temp: Path | None = None
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Empty PDF/Mathpix upload"})
            return
        if length > MAX_MATHPIX_UPLOAD:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "PDF/Mathpix upload exceeds 600 MB"})
            return
        try:
            fields = parse_multipart_form(self.headers.get("Content-Type", ""), self.rfile.read(length))
            pdf_part = fields.get("pdf")
            source_part = fields.get("source")
            markdown_part = fields.get("markdown")
            docx_part = fields.get("docx")
            pages = (fields.get("pages") or {}).get("text", "").strip()
            render_fidelity = (fields.get("renderFidelity") or {}).get("text", "").strip().lower() in {"1", "true", "yes", "on"}
            requested_calibration = (fields.get("calibration") or {}).get("text", "").strip().lower()
            calibration = requested_calibration if requested_calibration in {"none", "fast", "full"} else ("fast" if render_fidelity else "none")
            if not pages:
                raise GatewayUserError("Λείπει το εύρος σελίδων PDF, π.χ. 17-64.", "missing_pages")
            temp = Path(tempfile.mkdtemp(prefix="bookwriter_mathpix_gateway_"))
            output_dir = temp / "fidelity"
            input_mode = "all-formats-zip"
            if source_part and source_part.get("content"):
                source_name = safe_upload_filename(source_part.get("filename", "mathpix.zip"), "mathpix.zip", {".zip"})
                mathpix_source = temp / source_name
                mathpix_source.write_bytes(source_part["content"])
            elif markdown_part and markdown_part.get("content") and docx_part and docx_part.get("content"):
                input_mode = "separate-pdf-markdown-docx"
                source_dir = temp / "mathpix_sources"
                source_dir.mkdir(parents=True, exist_ok=True)
                original_markdown_name = safe_upload_filename(markdown_part.get("filename", "mathpix_markdown.zip"), "mathpix_markdown.zip", {".zip"})
                original_docx_name = safe_upload_filename(docx_part.get("filename", "mathpix.docx"), "mathpix.docx", {".docx"})
                markdown_name = "mathpix_markdown.zip"
                docx_name = "mathpix_donor.docx"
                (source_dir / markdown_name).write_bytes(markdown_part["content"])
                (source_dir / docx_name).write_bytes(docx_part["content"])
                source_name = f"{original_markdown_name} + {original_docx_name}"
                mathpix_source = source_dir
            elif markdown_part and markdown_part.get("content"):
                raise GatewayUserError("Λείπει το Mathpix DOCX donor. Σήμερα ο pipeline το χρειάζεται για OMML, μορφοποίηση και αντιστοίχιση.", "missing_docx")
            else:
                raise GatewayUserError("Λείπει το Mathpix source. Δώσε είτε all-formats ZIP είτε Markdown ZIP + DOCX donor.", "missing_source")
            if pdf_part and pdf_part.get("content"):
                pdf_name = safe_upload_filename(pdf_part.get("filename", "source.pdf"), "source.pdf", {".pdf"})
                source_pdf = temp / pdf_name
                source_pdf.write_bytes(pdf_part["content"])
            elif mathpix_source.is_file() and mathpix_source.suffix.lower() == ".zip":
                embedded_pdf = extract_first_pdf_from_zip(mathpix_source, temp)
                if not embedded_pdf:
                    raise GatewayUserError(
                        "Το Mathpix ZIP δεν περιέχει PDF. Διάλεξε και το αρχικό PDF για να συνεχιστεί η μετατροπή.",
                        "missing_pdf",
                    )
                source_pdf, pdf_name = embedded_pdf
            else:
                raise GatewayUserError("Λείπει το αρχικό PDF.", "missing_pdf")
            code = run_fidelity(argparse.Namespace(
                pdf=source_pdf,
                source=mathpix_source,
                pages=pages,
                output=output_dir,
                calibration=calibration,
                strict_page_count=bool(render_fidelity and calibration != "none"),
                no_render=not render_fidelity,
                progress_report=progress_path,
            ))
            logging.shutdown()
            if code:
                if int(code) == 26:
                    raise GatewayUserError(strict_failure_message(output_dir, int(code)), "mapping_fidelity_failed")
                if int(code) in {23, 24, 25}:
                    raise GatewayUserError(strict_failure_message(output_dir, int(code)), "strict_page_count_failed")
                raise RuntimeError(f"Το Mathpix fidelity pipeline σταμάτησε με κωδικό {code}.")
            manifest_path = output_dir / "PIPELINE_MANIFEST.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            reconstructed = Path(manifest["reconstructedDocx"])
            canonical = Path(manifest["canonicalDocx"])
            normalization_report = Path(manifest["normalizationReport"])
            fidelity_fallback_report_value = manifest.get("fidelityFallbackReport")
            fidelity_fallback_report_path = Path(fidelity_fallback_report_value) if fidelity_fallback_report_value else None
            fidelity_fallback_report = (
                json.loads(fidelity_fallback_report_path.read_text(encoding="utf-8"))
                if fidelity_fallback_report_path and fidelity_fallback_report_path.exists()
                else None
            )
            conversion_spine_value = manifest.get("conversionSpine")
            conversion_spine_path = Path(conversion_spine_value) if conversion_spine_value else None
            conversion_spine = (
                json.loads(conversion_spine_path.read_text(encoding="utf-8"))
                if conversion_spine_path and conversion_spine_path.exists()
                else None
            )
            docx_donor_map_value = manifest.get("docxDonorMap")
            docx_donor_map_path = Path(docx_donor_map_value) if docx_donor_map_value else None
            docx_donor_map = (
                json.loads(docx_donor_map_path.read_text(encoding="utf-8"))
                if docx_donor_map_path and docx_donor_map_path.exists()
                else None
            )
            page_layout_spine_value = manifest.get("pageLayoutSpine")
            page_layout_spine_path = Path(page_layout_spine_value) if page_layout_spine_value else None
            page_layout_spine = (
                json.loads(page_layout_spine_path.read_text(encoding="utf-8"))
                if page_layout_spine_path and page_layout_spine_path.exists()
                else None
            )
            architecture_benchmark_value = manifest.get("architectureBenchmark")
            architecture_benchmark_path = Path(architecture_benchmark_value) if architecture_benchmark_value else None
            architecture_benchmark = (
                json.loads(architecture_benchmark_path.read_text(encoding="utf-8"))
                if architecture_benchmark_path and architecture_benchmark_path.exists()
                else None
            )
            architecture_guard_value = manifest.get("architectureGuard")
            architecture_guard_path = Path(architecture_guard_value) if architecture_guard_value else None
            architecture_guard = (
                json.loads(architecture_guard_path.read_text(encoding="utf-8"))
                if architecture_guard_path and architecture_guard_path.exists()
                else None
            )
            mapping_fidelity_value = manifest.get("mappingFidelity")
            mapping_fidelity_path = Path(mapping_fidelity_value) if mapping_fidelity_value else None
            mapping_fidelity = (
                json.loads(mapping_fidelity_path.read_text(encoding="utf-8"))
                if mapping_fidelity_path and mapping_fidelity_path.exists()
                else None
            )
            calibration_report_path = output_dir / "01_reconstructed" / "analysis" / "calibration.json"
            calibration_report = (
                json.loads(calibration_report_path.read_text(encoding="utf-8"))
                if calibration_report_path.exists()
                else None
            )
            markdown_pdf_spine_path = output_dir / "01_reconstructed" / "analysis" / "markdown_pdf_spine.json"
            markdown_pdf_spine = (
                json.loads(markdown_pdf_spine_path.read_text(encoding="utf-8"))
                if markdown_pdf_spine_path.exists()
                else None
            )
            require_maps_first_contract(
                architecture_guard=architecture_guard,
                mapping_fidelity=mapping_fidelity,
                conversion_spine=conversion_spine,
                markdown_pdf_spine=markdown_pdf_spine,
                page_layout_spine=page_layout_spine,
                docx_donor_map=docx_donor_map,
            )
            result_for_user = json.loads(normalization_report.read_text(encoding="utf-8")) if normalization_report.exists() else {}
            artifact_dir = MATHPIX_ARTIFACT_ROOT / token
            artifact_dir.mkdir(parents=True, exist_ok=True)
            reconstructed_name = f"{Path(pdf_name).stem}_PDF_MATHPIX_RECONSTRUCTED.docx"
            reconstructed_artifact = artifact_dir / "reconstructed.docx"
            shutil.copy2(reconstructed, reconstructed_artifact)
            fidelity_summary_for_status = (fidelity_fallback_report or {}).get("summary", {}) if fidelity_fallback_report else {}
            conversion_summary_for_status = (conversion_spine or {}).get("summary", {}) if conversion_spine else {}
            maps_first_run = conversion_spine is not None
            effective_user_decisions = int(
                conversion_summary_for_status.get("decisionRequiredCount", 0)
                if maps_first_run
                else fidelity_summary_for_status.get("userDecisionQueueCount", 0)
                or 0
            )
            page_fidelity_exact = bool(
                calibration_report
                and calibration_report.get(
                    "selected_page_fidelity_exact",
                    calibration_report.get("selected_page_count_exact"),
                ) is True
            )
            render_review_required = bool(render_fidelity and not page_fidelity_exact)
            architecture_guard_failed = bool((architecture_guard or {}).get("status") == "fail")
            requires_user_review = bool(
                effective_user_decisions
                or render_review_required
                or architecture_guard_failed
            )
            result_for_user.update({
                "originalFileName": pdf_name,
                "mathpixSourceFileName": source_name,
                "mathpixInputMode": input_mode,
                "canonicalFileName": canonical.name,
                "reconstructedDocxFileName": reconstructed_name,
                "reconstructedDocxArtifact": {
                    "fileName": reconstructed_name,
                    "downloadUrl": f"/api/mathpix-artifact/{token}/reconstructed-docx",
                    "status": "draft" if requires_user_review else "approved-candidate",
                    "requiresUserReview": requires_user_review,
                    "description": "Ανακατασκευασμένο PDF→DOCX πριν την BookWriter κανονικοποίηση.",
                },
                "reportToken": token,
                "progressUrl": f"/api/mathpix-progress/{token}",
                "serverBuild": BUILD,
                "sourceType": "pdf-mathpix",
                "converterProduct": response_product,
                "renderFidelityEnabled": render_fidelity,
                "reconstructedCalibration": calibration_report,
                "markdownPdfSpine": markdown_pdf_spine,
                "conversionSpine": conversion_spine,
                "docxDonorMap": docx_donor_map,
                "pageLayoutSpine": page_layout_spine,
                "architectureBenchmark": architecture_benchmark,
                "architectureGuard": architecture_guard,
                "mappingFidelity": mapping_fidelity,
                "pdfPipelineManifest": manifest,
                "fidelityFallbackReport": fidelity_fallback_report,
            })
            result_for_user["reviewAssets"] = persist_mathpix_review_assets(result_for_user, artifact_dir, token, output_dir / "01_reconstructed")
            report_path.write_text(json.dumps(result_for_user, ensure_ascii=False, indent=2), encoding="utf-8")
            response_docx = reconstructed if response_product == "reconstructed" else canonical
            response_filename = reconstructed_name if response_product == "reconstructed" else canonical.name
            data = response_docx.read_bytes()
            warning_count = len(result_for_user.get("signature", {}).get("warnings", []))
            composite_info = result_for_user.get("compositeRasterization", {}) or {}
            fidelity_summary = (fidelity_fallback_report or {}).get("summary", {}) if fidelity_fallback_report else {}
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", content_disposition_attachment(response_filename))
            self.send_header("X-BookWriter-Canonicalization", "mathpix-fidelity-canonicalized")
            self.send_header("X-BookWriter-Strategy", str(result_for_user.get("strategy", "pdf-mathpix-fidelity")))
            self.send_header("X-BookWriter-Sections", str(result_for_user.get("output_section_count", 0)))
            self.send_header("X-BookWriter-Warnings", str(warning_count))
            self.send_header("X-BookWriter-Composite-Rasterized", str(composite_info.get("rasterizedCount", 0)))
            self.send_header("X-BookWriter-Composite-Equation-Overlays", str(composite_info.get("equationOverlayCount", 0)))
            self.send_header("X-BookWriter-Composite-Editable", str(composite_info.get("compositesWithEditableEquations", 0)))
            self.send_header("X-BookWriter-Composite-Clean-Backgrounds", str(composite_info.get("cleanBackgroundCount", 0)))
            self.send_header("X-BookWriter-Composite-Policy", str(result_for_user.get("compositePolicy", "off")))
            self.send_header("X-BookWriter-Mathpix-Equations", str(fidelity_summary.get("finalEquationCount", 0)))
            self.send_header("X-BookWriter-Mathpix-Native-Equations", str((fidelity_summary.get("finalEquationStatusCounts") or {}).get("native-word-math", 0)))
            self.send_header("X-BookWriter-Mathpix-Raster-Equations", str(fidelity_summary.get("rasterEquationFallbacks", 0)))
            self.send_header("X-BookWriter-Mathpix-User-Decisions", str(effective_user_decisions))
            self.send_header("X-BookWriter-Mathpix-Human-Review", str(0 if maps_first_run else fidelity_summary.get("actionableReviewQueueCount", fidelity_summary.get("humanReviewQueueCount", 0))))
            self.send_header("X-BookWriter-Mathpix-Diagnostics", str(0 if maps_first_run else fidelity_summary.get("diagnosticReviewQueueCount", 0)))
            self.send_header("X-BookWriter-Mathpix-Markdown-Elements", str(((manifest.get("inputPackage") or {}).get("markdownElementCount")) or 0))
            self.send_header("X-BookWriter-Mathpix-Render-Fidelity", "1" if render_fidelity else "0")
            self.send_header("X-BookWriter-Mathpix-Product", response_product)
            if calibration_report:
                self.send_header("X-BookWriter-Mathpix-Page-Fidelity", str(calibration_report.get("status", "")))
                self.send_header("X-BookWriter-Mathpix-Page-Count-Exact", "1" if calibration_report.get("selected_page_count_exact") else "0")
            self.send_header("X-BookWriter-Report-Token", token)
            self.send_header("X-BookWriter-Progress-Token", token)
            self.send_header("X-BookWriter-Canonical-Filename", canonical.name.encode("ascii", "ignore").decode("ascii") or "mathpix_BOOKWRITER.docx")
            self.send_header("X-BookWriter-Mathpix-Reconstructed-Filename", reconstructed_name.encode("ascii", "ignore").decode("ascii") or "mathpix_RECONSTRUCTED.docx")
            self.send_header("X-BookWriter-Mathpix-Reconstructed-Url", f"/api/mathpix-artifact/{token}/reconstructed-docx")
            self.send_header("X-BookWriter-Mathpix-Reconstructed-Status", "draft" if requires_user_review else "approved-candidate")
            self.send_header("X-BookWriter-Mathpix-Reconstructed-Requires-Review", "1" if requires_user_review else "0")
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:
            code = getattr(exc, "code", "pipeline_error")
            artifacts = copy_mathpix_failure_artifacts(temp, token)
            failure = {
                "ok": False,
                "action": "mathpix-fidelity-failed",
                "error": str(exc),
                "exceptionType": type(exc).__name__,
                "code": code,
                "reportToken": token,
                "progressUrl": f"/api/mathpix-progress/{token}",
                "serverBuild": BUILD,
                "traceback": traceback.format_exc(),
                **artifacts,
            }
            report_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log_error("Mathpix fidelity failed: %s", traceback.format_exc())
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {
                "ok": False,
                "error": str(exc),
                "exceptionType": type(exc).__name__,
                "code": code,
                "reportToken": token,
                "reportUrl": f"/api/normalization-report/{token}",
                "progressUrl": f"/api/mathpix-progress/{token}",
                **artifacts,
            })
        finally:
            logging.shutdown()
            if temp is not None:
                shutil.rmtree(temp, ignore_errors=True)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/status":
            pipeline_identity = runtime_identity()
            self._json(HTTPStatus.OK, {
                "ok": True,
                "build": BUILD,
                "canonicalProfile": "canonical-word-v1",
                "pipeline": pipeline_identity.get("pipelineBuild") or "",
                **pipeline_identity,
            })
            return
        if path.startswith("/api/normalization-report/"):
            token = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[a-f0-9-]{36}", token):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid report token"})
                return
            report = REPORT_ROOT / f"{token}.json"
            if not report.exists():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Report not found"})
                return
            data = report.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/api/mathpix-progress/"):
            token = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[a-f0-9-]{36}", token):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid progress token"})
                return
            progress = REPORT_ROOT / f"{token}.progress.json"
            if not progress.exists():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Progress not available yet"})
                return
            data = progress.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/api/mathpix-artifact/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) not in {4, 5}:
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid artifact path"})
                return
            _api, _kind, token, artifact_name = parts[:4]
            if not re.fullmatch(r"[a-f0-9-]{36}", token):
                self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid report token"})
                return
            if artifact_name == "review-assets" and len(parts) == 5:
                asset_name = safe_upload_filename(parts[4], "review.png", {".png", ".jpg", ".jpeg", ".webp", ".gif"})
                artifact = (MATHPIX_ARTIFACT_ROOT / token / "review_assets" / asset_name).resolve()
                root = (MATHPIX_ARTIFACT_ROOT / token / "review_assets").resolve()
                if root not in artifact.parents or not artifact.exists() or not artifact.is_file():
                    self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Artifact not found"})
                    return
                content_type = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                }.get(artifact.suffix.lower(), "image/png")
                data = artifact.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if artifact_name != "reconstructed-docx":
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Artifact not found"})
                return
            report = REPORT_ROOT / f"{token}.json"
            artifact = MATHPIX_ARTIFACT_ROOT / token / "reconstructed.docx"
            if not report.exists() or not artifact.exists():
                self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Artifact not found"})
                return
            try:
                report_data = json.loads(report.read_text(encoding="utf-8"))
            except Exception:
                report_data = {}
            filename = (
                (report_data.get("reconstructedDocxArtifact") or {}).get("fileName")
                or report_data.get("reconstructedDocxFileName")
                or "mathpix_RECONSTRUCTED.docx"
            )
            data = artifact.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", content_disposition_attachment(str(filename)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/convert-mathpix-docx":
            self._handle_canonicalize_mathpix(response_product="reconstructed")
            return
        if path == "/api/canonicalize-mathpix":
            self._handle_canonicalize_mathpix()
            return
        if path != "/api/canonicalize-docx":
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Unknown API endpoint"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Empty DOCX upload"})
            return
        if length > MAX_UPLOAD:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "DOCX exceeds 150 MB"})
            return
        filename = safe_filename(self.headers.get("X-BookWriter-Filename", "input.docx"))
        payload = self.rfile.read(length)
        token = str(uuid.uuid4())
        try:
            with tempfile.TemporaryDirectory(prefix="bookwriter_gateway_") as td:
                temp = Path(td)
                source = temp / filename
                output = temp / f"{source.stem}_BOOKWRITER.docx"
                local_report = temp / "normalization_report.json"
                source.write_bytes(payload)
                report_path = REPORT_ROOT / f"{token}.json"
                try:
                    result = canonicalize(source, output, "all", local_report, composite_policy="off")
                except Exception as exc:
                    if local_report.exists():
                        failure = json.loads(local_report.read_text(encoding="utf-8"))
                    else:
                        failure = {
                            "version": "0.4.7e-standalone-equations-native-floating-tables",
                            "action": "canonicalization-failed",
                            "error": str(exc),
                        }
                    failure.update({
                        "originalFileName": filename,
                        "reportToken": token,
                        "serverBuild": BUILD,
                    })
                    report_path.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.log_error("Canonicalization failed: %s", exc)
                    self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {
                        "ok": False,
                        "error": str(exc),
                        "originalFileName": filename,
                        "reportToken": token,
                        "reportUrl": f"/api/normalization-report/{token}",
                    })
                    return
                result_for_user = dict(result)
                result_for_user["originalFileName"] = filename
                result_for_user["canonicalFileName"] = output.name
                result_for_user["reportToken"] = token
                result_for_user["serverBuild"] = BUILD
                report_path.write_text(json.dumps(result_for_user, ensure_ascii=False, indent=2), encoding="utf-8")
                data = output.read_bytes()
                warning_count = len(result.get("signature", {}).get("warnings", []))
                composite_info = result.get("compositeRasterization", {}) or {}
                page_map_info = result.get("wordPageMap", {}) or {}
                vector_info = result.get("vectorPreviewConversion", {}) or {}
                composite_count = int(composite_info.get("rasterizedCount", 0) or 0)
                overlay_count = int(composite_info.get("equationOverlayCount", 0) or 0)
                editable_composites = int(composite_info.get("compositesWithEditableEquations", 0) or 0)
                clean_backgrounds = int(composite_info.get("cleanBackgroundCount", 0) or 0)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-BookWriter-Canonicalization", str(result.get("action", "unknown")))
                self.send_header("X-BookWriter-Strategy", str(result.get("strategy", "unknown")))
                self.send_header("X-BookWriter-Sections", str(result.get("output_section_count", 0)))
                self.send_header("X-BookWriter-Warnings", str(warning_count))
                self.send_header("X-BookWriter-Composite-Rasterized", str(composite_count))
                self.send_header("X-BookWriter-Composite-Equation-Overlays", str(overlay_count))
                self.send_header("X-BookWriter-Composite-Editable", str(editable_composites))
                self.send_header("X-BookWriter-Composite-Clean-Backgrounds", str(clean_backgrounds))
                self.send_header("X-BookWriter-Composite-Policy", str(result.get("compositePolicy", "auto")))
                self.send_header("X-BookWriter-Word-Page-Map", str(page_map_info.get("status", "unknown")))
                self.send_header("X-BookWriter-Word-Rendered-Pages", str(page_map_info.get("pageCount", 0)))
                self.send_header("X-BookWriter-Spanning-Table-Rows", str(page_map_info.get("spanningTableRows", 0)))
                self.send_header("X-BookWriter-Vector-Preview-Status", str(vector_info.get("status", "not-needed")))
                self.send_header("X-BookWriter-Vector-Previews-Converted", str(vector_info.get("convertedMedia", 0)))
                self.send_header("X-BookWriter-OLE-Equation-DSMT4", str(vector_info.get("equationDsmt4OleObjects", 0)))
                self.send_header("X-BookWriter-Report-Token", token)
                self.send_header("X-BookWriter-Canonical-Filename", output.name.encode("ascii", "ignore").decode("ascii") or "canonical.docx")
                self.end_headers()
                self.wfile.write(data)
        except Exception as exc:
            self.log_error("Canonicalization failed: %s", exc)
            self._json(HTTPStatus.UNPROCESSABLE_ENTITY, {
                "ok": False,
                "error": str(exc),
                "originalFileName": filename,
                "reportToken": token,
            })


def clean_old_reports(max_age_days: int = 14) -> None:
    cutoff = time.time() - max_age_days * 86400
    for path in REPORT_ROOT.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="BookWriter Studio static server + DOCX canonicalization gateway")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()
    if not BOOKWRITER_ROOT.exists():
        raise SystemExit(f"Missing BookWriter folder: {BOOKWRITER_ROOT}")
    clean_old_reports()
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    print("=" * 68)
    print("BookWriter v5 local server + DOCX gateway")
    print(f"Build:   {BUILD}")
    print(f"Folder:  {ROOT}")
    print(f"URL:     http://{args.bind}:{args.port}/author/index.html?v={BUILD}")
    print("Gateway: DOCX -> canonical-word-v1 -> existing v5/HF27 Author importer")
    print("=" * 68)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
