from __future__ import annotations

import argparse
import json
import sys
import tempfile
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SRC = REPO_ROOT / "PdfWordCanonicalPipeline" / "src"
if str(PIPELINE_SRC) not in sys.path:
    sys.path.insert(0, str(PIPELINE_SRC))

from pdf_word_reconstructor.mathpix_lines_input import build_mathpix_line_layout_map, find_mathpix_lines_json  # noqa: E402
from pdf_word_reconstructor.mathpix_page_geometry_adapter import build_mathpix_page_geometry_evidence  # noqa: E402
from pdf_word_reconstructor.mathpix_reserved_page_zones import build_reserved_page_zone_profile  # noqa: E402
from pdf_word_reconstructor.mathpix_margin_model import build_mathpix_margin_model  # noqa: E402


def _extract_recursive(source_zip: Path, target: Path) -> None:
    with zipfile.ZipFile(source_zip) as archive:
        archive.extractall(target)
    nested_root = target / "__nested__"
    for index, nested_zip in enumerate(sorted(target.rglob("*.zip")), start=1):
        nested_target = nested_root / f"zip_{index:03d}"
        nested_target.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(nested_zip) as archive:
                archive.extractall(nested_target)
        except zipfile.BadZipFile:
            continue


def _pdf_size_analysis(pdf_path: Path) -> dict:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    pages = []
    with fitz.open(pdf_path) as doc:
        for index, page in enumerate(doc, start=1):
            rect = page.rect
            pages.append({"page": index, "width_pt": float(rect.width), "height_pt": float(rect.height)})
    return {"pages": pages}


def run(package_zip: Path, pdf_path: Path, output: Path | None = None) -> dict:
    package_zip = package_zip.resolve()
    pdf_path = pdf_path.resolve()
    if not package_zip.exists():
        raise FileNotFoundError(package_zip)
    if not pdf_path.exists():
        raise FileNotFoundError(pdf_path)

    with tempfile.TemporaryDirectory(prefix="bookwriter_margin_audit_") as temp_name:
        package_dir = Path(temp_name) / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        _extract_recursive(package_zip, package_dir)
        lines_path = find_mathpix_lines_json(package_dir)
        if lines_path is None:
            raise FileNotFoundError("result.lines.json not found")

        line_map = build_mathpix_line_layout_map(lines_path, _pdf_size_analysis(pdf_path))
        geometry = build_mathpix_page_geometry_evidence(line_map)
        reserved = build_reserved_page_zone_profile(line_map, geometry)
        margins = build_mathpix_margin_model(line_map, geometry, reserved)

        pages = margins.get("pages") or []
        furniture_violations = [
            int(row.get("page") or 0)
            for row in pages
            if isinstance(row.get("furnitureValidation"), dict)
            and not row["furnitureValidation"].get("valid", False)
        ]
        unresolved = [int(row.get("page") or 0) for row in pages if row.get("status") != "resolved"]
        inherited = [int(row.get("page") or 0) for row in pages if row.get("source") == "document-profile"]
        page_evidence = [int(row.get("page") or 0) for row in pages if row.get("source") == "page-evidence"]

        report = {
            "status": "PASS" if not furniture_violations else "REVIEW",
            "package": str(package_zip),
            "sourcePdf": str(pdf_path),
            "summary": {
                **(margins.get("summary") or {}),
                "resolvedPageCount": sum(1 for row in pages if row.get("status") == "resolved"),
                "unresolvedPageCount": len(unresolved),
                "pageEvidenceCount": len(page_evidence),
                "documentProfileInheritedCount": len(inherited),
                "furnitureInsideMarginViolationCount": len(furniture_violations),
                "unresolvedPages": unresolved,
                "documentProfileInheritedPages": inherited,
                "furnitureInsideMarginViolationPages": furniture_violations,
                "reservedZoneSummary": reserved.get("summary") or {},
            },
            "policy": "header/footer are objects inside top/bottom margins; they validate margins but never add to them",
            "marginModel": margins,
            "reservedPageZones": reserved,
        }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit body-derived margins with header/footer as internal witnesses.")
    parser.add_argument("package_zip", type=Path)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args.package_zip, args.pdf, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Mathpix margin model audit: {report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
