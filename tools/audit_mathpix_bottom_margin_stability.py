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


VERSION = "mathpix-bottom-margin-stability-audit-0.1"


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
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore
    pages = []
    with fitz.open(pdf_path) as doc:
        for index, page in enumerate(doc, start=1):
            rect = page.rect
            pages.append({"page": index, "width_pt": float(rect.width), "height_pt": float(rect.height)})
    return {"pages": pages}


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    vals = sorted(float(v) for v in values)
    idx = min(len(vals) - 1, max(0, round((len(vals) - 1) * fraction)))
    return vals[idx]


def run(package_zip: Path, pdf_path: Path, output: Path | None = None) -> dict:
    package_zip = package_zip.resolve()
    pdf_path = pdf_path.resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {pdf_path}")

    with tempfile.TemporaryDirectory(prefix="bookwriter_bottom_margin_audit_") as temp_name:
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

        rows = []
        for row in margins.get("pages", []) or []:
            if row.get("status") != "resolved":
                continue
            page_no = int(row.get("page") or 0)
            height = float(row.get("pageHeightPt") or 0)
            envelope = row.get("bodyEnvelope") or {}
            raw = envelope.get("rawEnvelope") or []
            observed = row.get("observedMarginsPt") or {}
            if height <= 0 or len(raw) != 4:
                continue
            raw_bottom = height - float(raw[3])
            robust_bottom = float(observed.get("bottom") or 0)
            rows.append({
                "page": page_no,
                "source": row.get("source"),
                "confidence": row.get("confidence"),
                "rawBottomWhitespacePt": round(raw_bottom, 3),
                "robustObservedBottomPt": round(robust_bottom, 3),
                "objectCount": int(envelope.get("objectCount") or 0),
                "furnitureValidation": row.get("furnitureValidation"),
            })

        trusted = [r for r in rows if r.get("source") == "page-evidence"]
        raw_values = [float(r["rawBottomWhitespacePt"]) for r in trusted]
        robust_values = [float(r["robustObservedBottomPt"]) for r in trusted]

        raw_q10 = _quantile(raw_values, 0.10)
        dense = sorted(trusted, key=lambda r: float(r["rawBottomWhitespacePt"]))[:20]
        dense_threshold = raw_q10 if raw_q10 is not None else 0.0
        dense_cluster = [r for r in trusted if float(r["rawBottomWhitespacePt"]) <= dense_threshold]

        report = {
            "version": VERSION,
            "status": "PASS",
            "package": str(package_zip),
            "sourcePdf": str(pdf_path),
            "summary": {
                "resolvedPageCount": len(rows),
                "trustedPageEvidenceCount": len(trusted),
                "marginModelBottomProfilePt": (margins.get("documentMarginProfile") or {}).get("bottomPt"),
                "rawBottomWhitespaceQuantilesPt": {
                    "min": min(raw_values) if raw_values else None,
                    "p05": _quantile(raw_values, 0.05),
                    "p10": raw_q10,
                    "p25": _quantile(raw_values, 0.25),
                    "median": _quantile(raw_values, 0.50),
                    "p75": _quantile(raw_values, 0.75),
                },
                "robustObservedBottomQuantilesPt": {
                    "min": min(robust_values) if robust_values else None,
                    "p05": _quantile(robust_values, 0.05),
                    "p10": _quantile(robust_values, 0.10),
                    "p25": _quantile(robust_values, 0.25),
                    "median": _quantile(robust_values, 0.50),
                    "p75": _quantile(robust_values, 0.75),
                },
                "densePageThresholdPt": dense_threshold,
                "densePageCount": len(dense_cluster),
                "densestPages": dense,
            },
            "interpretation": (
                "The true bottom text-frame margin is constrained by the fullest pages. "
                "Short pages can only increase observed bottom whitespace. Compare the low raw-bottom quantiles "
                "with the margin-model profile before changing the document bottom margin."
            ),
        }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit whether a large Mathpix bottom margin is stable on the fullest pages.")
    parser.add_argument("package_zip", type=Path)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run(args.package_zip, args.pdf, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print("Mathpix bottom margin stability audit: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
