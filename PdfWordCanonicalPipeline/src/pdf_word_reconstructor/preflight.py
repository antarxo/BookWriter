from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import fitz
from lxml import etree

from .common import compact_text, normalize_text, parse_page_range, write_json

_STOPWORDS = {
    "και", "στο", "στη", "στην", "στις", "στους", "των", "της", "του", "τους",
    "ένα", "μια", "μία", "που", "για", "από", "με", "σε", "ως", "ότι", "είναι",
    "την", "τον", "τα", "το", "οι", "ο", "η", "ή", "θα", "δεν", "αυτό", "αυτή",
}


def _token_coverage_score(pdf_text: str, docx_text: str) -> float:
    def tokens(value: str) -> set[str]:
        return {
            token for token in re.findall(r"[0-9a-zα-ωάέήίόύώϊϋΐΰ]+", normalize_text(value))
            if len(token) >= 4 and token not in _STOPWORDS
        }
    source = tokens(pdf_text)
    target = tokens(docx_text)
    if not source or not target:
        return 0.0
    return round(100.0 * len(source & target) / len(source), 2)


def _docx_text(path: Path) -> str:
    with ZipFile(path) as package:
        xml = package.read("word/document.xml")
    chunks: list[str] = []
    for _event, node in etree.iterparse(BytesIO(xml), events=("end",)):
        if node.tag.endswith("}t") and node.text:
            chunks.append(node.text)
        node.clear()
    return " ".join(chunks)


def _pdf_selected_text(path: Path, pages: list[int]) -> tuple[int, str, str]:
    with fitz.open(path) as doc:
        selected = []
        for page_no in pages[: min(4, len(pages))]:
            selected.append(doc[page_no - 1].get_text("text", sort=True))
        first_text = doc[pages[0] - 1].get_text("text", sort=True)
        return doc.page_count, "\n".join(selected), first_text


def _stem_similarity(pdf: Path, docx: Path) -> float:
    left = normalize_text(pdf.stem)
    right = normalize_text(docx.stem)
    return round(100.0 * SequenceMatcher(None, left, right).ratio(), 2)


def _marker_hits(text: str, markers: list[str]) -> int:
    normalized = normalize_text(text)
    return sum(1 for marker in markers if normalize_text(marker) in normalized)


_IGNORED_NAME_PARTS = {
    "native_page_structure", "candidate_", "probe_", "guided_probe", "structured_draft",
    "reconstructed", "comparison", "report", "calibration",
}
_IGNORED_DIR_PARTS = {"output", "work", "logs", ".venv", "__pycache__", "tests"}


def _source_candidates(folder: Path, suffix: str) -> list[Path]:
    """Find user source files without depending on filename language or case.

    Direct files inside input are preferred. If input contains subfolders, scan them
    recursively. Generated outputs and temporary files are excluded.
    """
    if not folder.is_dir():
        return []

    def accepted(path: Path) -> bool:
        if not path.is_file() or path.suffix.lower() != suffix.lower():
            return False
        if path.name.startswith("~$"):
            return False
        lowered_name = path.name.casefold()
        if any(part in lowered_name for part in _IGNORED_NAME_PARTS):
            return False
        rel_parts = {part.casefold() for part in path.relative_to(folder).parts[:-1]}
        if rel_parts & {part.casefold() for part in _IGNORED_DIR_PARTS}:
            return False
        return True

    direct = sorted((path for path in folder.iterdir() if accepted(path)), key=lambda p: p.name.casefold())
    if direct:
        return direct
    return sorted((path for path in folder.rglob("*") if accepted(path)), key=lambda p: str(p).casefold())


def _resolve_auto_pair(folder: Path, job: dict[str, Any]) -> tuple[Path | None, Path | None, dict[str, Any]]:
    pdfs = _source_candidates(folder, ".pdf")
    docxs = _source_candidates(folder, ".docx")
    detail: dict[str, Any] = {
        "mode": "auto-content-discovery",
        "folder": str(folder),
        "pdf_candidates": [p.name for p in pdfs],
        "docx_candidates": [p.name for p in docxs],
        "evaluated": [],
    }
    if not pdfs or not docxs:
        detail["failure_reason"] = (
            f"Found {len(pdfs)} PDF source file(s) and {len(docxs)} DOCX source file(s) "
            f"under {folder}. Copy the original source pairs into this input folder; "
            "filenames may remain unchanged."
        )
        return None, None, detail

    markers = [str(value) for value in job.get("expected_markers", [])]
    pages_value = str(job.get("pages", "1"))
    pdf_cache: dict[Path, tuple[int, list[int], str, str]] = {}
    docx_cache: dict[Path, str] = {}

    for pdf in pdfs:
        try:
            with fitz.open(pdf) as probe:
                pages = parse_page_range(pages_value, max_pages=probe.page_count)
            page_count, sample, first = _pdf_selected_text(pdf, pages)
            pdf_cache[pdf] = (page_count, pages, sample, first)
        except Exception as exc:
            detail["evaluated"].append({"pdf": pdf.name, "error": str(exc)})

    for docx in docxs:
        try:
            docx_cache[docx] = _docx_text(docx)
        except Exception as exc:
            detail["evaluated"].append({"docx": docx.name, "error": str(exc)})

    best: tuple[tuple[float, ...], Path, Path, dict[str, Any]] | None = None
    for pdf, (_count, _pages, sample, first) in pdf_cache.items():
        pdf_text = first + "\n" + sample
        pdf_hits = _marker_hits(pdf_text, markers)
        for docx, docx_text in docx_cache.items():
            docx_hits = _marker_hits(docx_text, markers)
            both_hits = sum(
                1 for marker in markers
                if normalize_text(marker) in normalize_text(pdf_text)
                and normalize_text(marker) in normalize_text(docx_text)
            )
            pair_score = _token_coverage_score(sample, docx_text)
            stem_score = _stem_similarity(pdf, docx)
            record = {
                "pdf": pdf.name,
                "docx": docx.name,
                "marker_hits_pdf": pdf_hits,
                "marker_hits_docx": docx_hits,
                "marker_hits_both": both_hits,
                "pair_score": pair_score,
                "stem_score": stem_score,
            }
            detail["evaluated"].append(record)
            rank = (float(both_hits), float(pdf_hits + docx_hits), pair_score, stem_score)
            if best is None or rank > best[0]:
                best = (rank, pdf, docx, record)

    if best is None:
        return None, None, detail
    detail["selected"] = best[3]
    return best[1].resolve(), best[2].resolve(), detail


def _resolve_sources(base: Path, job: dict[str, Any]) -> tuple[Path | None, Path | None, dict[str, Any]]:
    folder = (base / str(job["folder"])).resolve()
    pdf_name = str(job.get("pdf", "AUTO"))
    docx_name = str(job.get("docx", "AUTO"))
    if pdf_name.upper() == "AUTO" or docx_name.upper() == "AUTO":
        return _resolve_auto_pair(folder, job)
    return (folder / pdf_name).resolve(), (folder / docx_name).resolve(), {"mode": "explicit"}


def inspect_job(base: Path, job: dict[str, Any]) -> dict[str, Any]:
    folder = (base / str(job["folder"])).resolve()
    pdf, docx, resolution = _resolve_sources(base, job)
    result: dict[str, Any] = {
        "name": str(job.get("name", "job")),
        "role": str(job.get("role", "secondary")),
        "folder": str(folder),
        "pdf": str(pdf) if pdf else "",
        "docx": str(docx) if docx else "",
        "pages": str(job.get("pages", "1")),
        "source_resolution": resolution,
        "checks": [],
        "passed": False,
    }

    if pdf is None:
        result["checks"].append({"check": "pdf_auto_discovery", "passed": False, "detail": resolution})
        return result
    result["checks"].append({"check": "pdf_resolved", "passed": pdf.is_file(), "detail": str(pdf)})
    if not pdf.is_file():
        return result

    if docx is None:
        result["checks"].append({"check": "docx_auto_discovery", "passed": False, "detail": resolution})
        return result
    result["checks"].append({"check": "docx_resolved", "passed": docx.is_file(), "detail": str(docx)})
    if not docx.is_file():
        return result

    try:
        with fitz.open(pdf) as probe:
            pages = parse_page_range(result["pages"], max_pages=probe.page_count)
    except Exception as exc:
        result["checks"].append({"check": "page_range", "passed": False, "detail": str(exc)})
        return result
    result["checks"].append({"check": "page_range", "passed": True, "detail": pages})

    try:
        pdf_page_count, pdf_sample, first_selected_text = _pdf_selected_text(pdf, pages)
        docx_text = _docx_text(docx)
    except Exception as exc:
        result["checks"].append({"check": "read_sources", "passed": False, "detail": str(exc)})
        return result

    norm_pdf = normalize_text(pdf_sample)
    norm_docx = normalize_text(docx_text)
    pair_score = _token_coverage_score(norm_pdf, norm_docx)
    minimum_pair_score = float(job.get("minimum_pair_score", 55))
    pair_ok = pair_score >= minimum_pair_score
    result["checks"].append({
        "check": "pdf_docx_pair_similarity",
        "passed": pair_ok,
        "detail": {"score": pair_score, "minimum": minimum_pair_score},
    })

    expected_markers = [str(value) for value in job.get("expected_markers", [])]
    marker_details = []
    marker_ok = not expected_markers
    selected_norm = normalize_text(first_selected_text + "\n" + pdf_sample)
    for marker in expected_markers:
        normalized_marker = normalize_text(marker)
        in_pdf = normalized_marker in selected_norm
        in_docx = normalized_marker in norm_docx
        marker_details.append({"marker": marker, "in_pdf": in_pdf, "in_docx": in_docx})
        marker_ok = marker_ok or (in_pdf and in_docx)
    result["checks"].append({"check": "job_identity_marker", "passed": marker_ok, "detail": marker_details})

    result.update({
        "pdf_page_count": pdf_page_count,
        "selected_page_count": len(pages),
        "selected_pages": pages,
        "first_selected_pdf_text": compact_text(first_selected_text, 260),
        "docx_text_preview": compact_text(docx_text, 260),
        "pair_score": pair_score,
        "resolved_pdf_name": pdf.name,
        "resolved_docx_name": docx.name,
    })
    result["passed"] = all(bool(check["passed"]) for check in result["checks"])
    return result


def run_preflight(config_path: Path, report_path: Path | None = None) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    base = config_path.parent.resolve()
    jobs = [job for job in config.get("jobs", []) if job.get("enabled", True)]
    results = [inspect_job(base, job) for job in jobs]
    resolved_pairs = [(item.get("pdf"), item.get("docx")) for item in results if item.get("passed")]
    duplicate_sources = len(resolved_pairs) != len(set(resolved_pairs))
    report = {
        "version": str(config.get("version", "unknown")),
        "status": "passed" if results and all(item["passed"] for item in results) and not duplicate_sources else "failed",
        "duplicate_sources": duplicate_sources,
        "config_path": str(config_path.resolve()),
        "project_root": str(base),
        "jobs": results,
    }
    if duplicate_sources:
        report["error"] = "Two jobs resolved to the same PDF/DOCX pair."
    if report_path:
        write_json(report_path, report)
    return report


def _print_report(report: dict[str, Any]) -> None:
    print("\n=== STRICT PREFLIGHT — AUTO FILE DISCOVERY ===\n")
    for job in report.get("jobs", []):
        status = "PASS" if job.get("passed") else "FAIL"
        print(f"[{status}] {job.get('name')} ({job.get('role')})")
        print(f"  input folder: {job.get('folder')}")
        print(f"  selected PDF : {job.get('resolved_pdf_name', job.get('pdf', ''))}")
        print(f"  selected DOCX: {job.get('resolved_docx_name', job.get('docx', ''))}")
        if "pdf_page_count" in job:
            print(f"  PDF pages: {job['pdf_page_count']} | selected: {job['pages']}")
            print(f"  PDF sample: {job.get('first_selected_pdf_text', '')}")
            print(f"  DOCX sample: {job.get('docx_text_preview', '')}")
            print(f"  pair score: {job.get('pair_score')}")
        for check in job.get("checks", []):
            flag = "OK" if check.get("passed") else "FAILED"
            print(f"    {flag}: {check.get('check')} -> {check.get('detail')}")
        print()
    if report.get("duplicate_sources"):
        print("FAILED: two jobs were matched to the same source pair.")
    print(f"PREFLIGHT RESULT: {str(report.get('status')).upper()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto-detect and validate PDF/DOCX jobs before reconstruction")
    parser.add_argument("--jobs", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_preflight(args.jobs, args.report)
    _print_report(report)
    return 0 if report["status"] == "passed" else 11


if __name__ == "__main__":
    raise SystemExit(main())
