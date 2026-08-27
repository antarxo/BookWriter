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

from pdf_word_reconstructor.mathpix_lines_input import (  # noqa: E402
    build_mathpix_line_layout_map,
    find_mathpix_lines_json,
)
from pdf_word_reconstructor.mathpix_package_enrichment import enrich_with_mathpix_package  # noqa: E402
from pdf_word_reconstructor.mathpix_package_input import build_mathpix_package_map  # noqa: E402


VERSION = "mathpix-package-enrichment-audit-0.2"


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


def _assert(checks: list[dict], name: str, ok: bool, detail: object = None) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})


def run_audit(package_zip: Path, output_json: Path | None = None) -> dict:
    package_zip = package_zip.resolve()
    checks: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="bookwriter_mathpix_audit_") as temp_name:
        package_dir = Path(temp_name) / "package"
        package_dir.mkdir(parents=True, exist_ok=True)
        _extract_recursive(package_zip, package_dir)

        lines_path = find_mathpix_lines_json(package_dir)
        _assert(checks, "lines-json-found", lines_path is not None, str(lines_path) if lines_path else None)
        if lines_path is None:
            report = {"version": VERSION, "status": "FAIL", "checks": checks}
            if output_json:
                output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            return report

        line_map = build_mathpix_line_layout_map(lines_path)
        package_map = build_mathpix_package_map(package_dir, line_map)
        audit = package_map.get("audit") or {}
        pages = line_map.get("pages") or []
        assets = package_map.get("assets") or []
        markdown = package_map.get("markdown") or {}

        _assert(checks, "line-pages-present", len(pages) > 0, len(pages))
        _assert(
            checks,
            "all-line-pages-preserve-raw-page",
            all("rawPage" in page and isinstance(page.get("rawPage"), dict) for page in pages),
            sum(1 for page in pages if "rawPage" in page),
        )
        _assert(
            checks,
            "all-line-pages-expose-page-envelope",
            all(
                all(key in page for key in ("page", "image_id", "languages_detected", "page_width_px", "page_height_px"))
                for page in pages
            ),
            len(pages),
        )
        _assert(checks, "line-map-preserves-top-level", "rawTopLevel" in line_map, sorted((line_map.get("rawTopLevel") or {}).keys()))

        object_count = sum(len(page.get("objects") or []) for page in pages)
        raw_object_count = sum(
            1 for page in pages for obj in (page.get("objects") or [])
            if isinstance(obj.get("raw"), dict)
        )
        _assert(checks, "all-line-objects-preserve-raw", raw_object_count == object_count, {"objects": object_count, "raw": raw_object_count})

        packaged_ref_count = int(audit.get("packagedMmdImageReferenceCount") or 0)
        packaged_resolved = int(audit.get("packagedMmdResolvedReferenceCount") or 0)
        canonical_ref_count = int(audit.get("canonicalMmdImageReferenceCount") or 0)
        canonical_resolved = int(audit.get("canonicalMmdResolvedReferenceCount") or 0)
        pair_count = int(audit.get("pairedReferenceCount") or 0)
        pair_resolved = int(audit.get("resolvedReferencePairCount") or 0)
        pair_geometry_matched = int(audit.get("geometryMatchingReferencePairCount") or 0)

        _assert(checks, "package-audit-complete", audit.get("status") == "complete", audit.get("status"))
        _assert(checks, "manifest-image-count-matches", bool(audit.get("manifestImageCountMatches")), {"manifest": audit.get("manifestImageCount"), "assets": len(assets)})
        _assert(checks, "page-counts-agree", bool(audit.get("pageCountsAgree")), {"status": audit.get("statusPageCount"), "lines": audit.get("linesPageCount")})
        _assert(checks, "all-packaged-mmd-refs-resolve", packaged_ref_count == packaged_resolved, {"refs": packaged_ref_count, "resolved": packaged_resolved})
        _assert(checks, "all-canonical-mmd-refs-resolve", canonical_ref_count == canonical_resolved, {"refs": canonical_ref_count, "resolved": canonical_resolved})
        _assert(checks, "all-reference-pairs-resolve", pair_count == pair_resolved, {"pairs": pair_count, "resolved": pair_resolved})
        _assert(checks, "all-reference-pairs-geometry-match", pair_count == pair_geometry_matched, {"pairs": pair_count, "geometryMatched": pair_geometry_matched})

        _assert(checks, "all-assets-have-hash", all(bool(asset.get("sha256")) for asset in assets), len(assets))
        _assert(
            checks,
            "all-assets-have-geometry",
            all(isinstance(asset.get("geometry"), dict) for asset in assets),
            sum(1 for asset in assets if isinstance(asset.get("geometry"), dict)),
        )
        _assert(
            checks,
            "all-assets-have-lines-geometry-witness",
            all(bool(asset.get("relatedLineObjects")) for asset in assets),
            {"assets": len(assets), "withLineWitness": sum(1 for asset in assets if asset.get("relatedLineObjects"))},
        )

        unreferenced = [asset for asset in assets if not asset.get("packagedMmdReferenced")]
        _assert(
            checks,
            "unreferenced-assets-preserved",
            len(unreferenced) == int(audit.get("unreferencedPackagedAssetCount") or 0),
            len(unreferenced),
        )

        target = {"pages": [{"page": int(page.get("page") or 0)} for page in pages]}
        enrich_with_mathpix_package(target, package_map)
        root_keys = (
            "mathpixPackageSummary",
            "mathpixPackageMap",
            "mathpixMarkdownMap",
            "mathpixAssetMap",
            "mathpixPackageCompletenessAudit",
        )
        _assert(checks, "all-package-root-maps-attached", all(key in target for key in root_keys), [key for key in root_keys if key not in target])

        package_pages = {int(page.get("page") or 0) for page in package_map.get("pages") or []}
        enriched_pages = {
            int(page.get("page") or 0)
            for page in target.get("pages") or []
            if isinstance(page.get("mathpixAssetPageMap"), dict)
        }
        _assert(checks, "page-scoped-asset-maps-attached", package_pages == enriched_pages, {"packagePages": len(package_pages), "enrichedPages": len(enriched_pages)})

        pairs = markdown.get("referencePairs") or []
        _assert(checks, "canonical-packaged-reference-pairs-complete", len(pairs) == max(canonical_ref_count, packaged_ref_count), {"pairs": len(pairs), "canonical": canonical_ref_count, "packaged": packaged_ref_count})
        _assert(checks, "reference-pair-records-expose-geometry", all("geometryMatches" in pair for pair in pairs), len(pairs))

        failed = [check for check in checks if not check["ok"]]
        report = {
            "version": VERSION,
            "package": str(package_zip),
            "status": "PASS" if not failed else "FAIL",
            "summary": {
                "pageCount": len(pages),
                "lineObjectCount": object_count,
                "assetCount": len(assets),
                "canonicalMmdImageReferenceCount": canonical_ref_count,
                "packagedMmdImageReferenceCount": packaged_ref_count,
                "unreferencedPackagedAssetCount": len(unreferenced),
                "checkCount": len(checks),
                "failedCheckCount": len(failed),
            },
            "checks": checks,
        }

    if output_json:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict audit of Mathpix package ingestion/enrichment completeness.")
    parser.add_argument("package_zip", type=Path, help="Outer Mathpix manifest/package ZIP")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    args = parser.parse_args()

    report = run_audit(args.package_zip, args.output)
    print(json.dumps(report.get("summary") or {}, ensure_ascii=False, indent=2))
    print(f"Mathpix enrichment audit: {report['status']}")
    if report["status"] != "PASS":
        for check in report.get("checks") or []:
            if not check.get("ok"):
                print(f"FAIL: {check.get('name')}: {check.get('detail')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
