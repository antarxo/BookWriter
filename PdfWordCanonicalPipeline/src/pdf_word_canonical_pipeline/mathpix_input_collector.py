from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree
from .markdown_element_map import extract_markdown_element_map
from .markdown_equation_donor import extract_markdown_equations

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".svg"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_stem(path: Path) -> str:
    stem = path.stem.casefold()
    stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    stem = re.sub(r"[^0-9a-zα-ωά-ώ]+", "", stem)
    return stem


def _extract_zip(archive_path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(target)


def _zip_listing(path: Path, limit: int = 80) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except Exception as exc:
        return {"path": str(path), "error": str(exc)}
    suffix_counts: dict[str, int] = {}
    for name in names:
        suffix = Path(name).suffix.lower() or "(none)"
        suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
    return {
        "path": str(path),
        "entryCount": len(names),
        "suffixCounts": suffix_counts,
        "sample": names[:limit],
    }


def _docx_inventory(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path),
        "sha256": _sha256(path),
        "ommlCount": 0,
        "drawingCount": 0,
        "mediaCount": 0,
        "rasterMediaCount": 0,
        "mediaHashes": [],
    }
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
            root = etree.fromstring(document)
            ns = {
                "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
            }
            out["ommlCount"] = int(len(root.xpath(".//m:oMath", namespaces=ns)))
            out["drawingCount"] = int(len(root.xpath(".//w:drawing | .//w:pict", namespaces=ns)))
            hashes = []
            for name in archive.namelist():
                if not name.startswith("word/media/") or name.endswith("/"):
                    continue
                data = archive.read(name)
                hashes.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
            out["mediaHashes"] = hashes
            out["mediaCount"] = len(hashes)
            out["rasterMediaCount"] = sum(Path(item["name"]).suffix.lower() in IMAGE_EXTENSIONS - {".svg"} for item in hashes)
    except Exception as exc:
        out["error"] = str(exc)
    return out


def _collect_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def collect_mathpix_inputs(pdf_path: Path, source: Path, output_dir: Path) -> dict[str, Any]:
    pdf_path = Path(pdf_path).resolve()
    source = Path(source).resolve()
    output_dir = Path(output_dir).resolve()
    extracted = output_dir / "extracted"
    nested = output_dir / "nested"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    extracted.mkdir(parents=True, exist_ok=True)

    if source.is_file() and source.suffix.lower() == ".zip":
        _extract_zip(source, extracted)
        source_kind = "zip"
    elif source.is_dir():
        # Preserve the user's download folder and work from a private copy of
        # only the relevant file types. This avoids mutating or locking inputs.
        source_kind = "folder"
        for path in source.iterdir():
            if path.is_file() and path.suffix.lower() in {".docx", ".md", ".mmd", ".html", ".zip", ".tex"} | IMAGE_EXTENSIONS:
                shutil.copy2(path, extracted / path.name)
    else:
        raise FileNotFoundError(f"Δεν βρέθηκε φάκελος/ZIP Mathpix: {source}")

    # Expand the outer all-formats ZIP and its common inner TeX/image ZIP.
    # Two levels are sufficient for current Mathpix exports and prevent an
    # accidental recursive expansion loop.
    queue = [(path, 1) for path in extracted.rglob("*.zip")]
    seen_archives: set[str] = set()
    extraction_errors: list[dict[str, Any]] = []
    while queue:
        zip_path, depth = queue.pop(0)
        try:
            archive_key = _sha256(zip_path)
        except Exception:
            continue
        if archive_key in seen_archives or depth > 2:
            continue
        seen_archives.add(archive_key)
        target = nested / f"level{depth}" / f"archive_{archive_key[:8]}"
        try:
            _extract_zip(zip_path, target)
        except Exception as exc:
            extraction_errors.append({
                "archive": str(zip_path),
                "depth": depth,
                "error": str(exc),
                "listing": _zip_listing(zip_path),
            })
            continue
        if depth < 2:
            queue.extend((child, depth + 1) for child in target.rglob("*.zip"))

    files = _collect_files(extracted) + _collect_files(nested)
    docx_files = sorted({path.resolve() for path in files if path.suffix.lower() == ".docx"})
    markdown_files = sorted({path.resolve() for path in files if path.suffix.lower() in {".md", ".mmd"}})
    html_files = sorted({path.resolve() for path in files if path.suffix.lower() in {".html", ".htm"}})
    asset_files = sorted({path.resolve() for path in files if path.suffix.lower() in IMAGE_EXTENSIONS})
    asset_hashes = {_sha256(path) for path in asset_files if path.suffix.lower() != ".svg"}
    pdf_stem = _normalized_stem(pdf_path)

    if not markdown_files:
        zip_paths = [path for path in extracted.rglob("*.zip")]
        zip_names = [str(path.relative_to(extracted)) for path in zip_paths]
        diagnostics = {
            "source": str(source),
            "zipFiles": [_zip_listing(path) for path in zip_paths],
            "extractionErrors": extraction_errors,
        }
        diagnostic_path = output_dir / "MATHPIX_INPUT_DIAGNOSTIC.json"
        diagnostic_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
        raise FileNotFoundError(
            "Δεν βρέθηκε Mathpix Markdown αρχείο (.md/.mmd) μέσα στο πακέτο. "
            "Δώσε το Mathpix Markdown ZIP με τις εικόνες, όχι μόνο DOCX ή λάθος ZIP. "
            f"ZIP αρχεία που εντοπίστηκαν: {zip_names or 'κανένα'}. "
            f"Διαγνωστικό: {diagnostic_path}"
        )

    docx_candidates = []
    for path in docx_files:
        inventory = _docx_inventory(path)
        media_hashes = {item["sha256"] for item in inventory.get("mediaHashes", []) if Path(item["name"]).suffix.lower() != ".svg"}
        overlap = len(media_hashes & asset_hashes)
        overlap_ratio = overlap / max(1, len(media_hashes))
        stem_match = _normalized_stem(path) == pdf_stem
        bundled = source_kind == "zip" or nested in path.parents
        score = (
            (1000 if stem_match else 0)
            + (250 if bundled else 0)
            + overlap * 4
            + overlap_ratio * 300
            + min(150, int(inventory.get("ommlCount", 0)) // 20)
        )
        inventory.update({
            "stemMatch": stem_match,
            "bundled": bundled,
            "externalRasterOverlap": overlap,
            "externalRasterOverlapRatio": round(overlap_ratio, 5),
            "selectionScore": round(score, 3),
        })
        docx_candidates.append(inventory)

    docx_candidates.sort(key=lambda item: (item.get("selectionScore", 0), item.get("ommlCount", 0), item.get("mediaCount", 0), item.get("path", "")), reverse=True)
    if not docx_candidates:
        raise FileNotFoundError("Δεν βρέθηκε DOCX μέσα στο Mathpix source.")
    selected_docx = Path(docx_candidates[0]["path"])

    markdown_element_map = extract_markdown_element_map(
        markdown_files,
        output_dir / "MARKDOWN_ELEMENT_MAP.json",
        docx_path=selected_docx,
    )
    equation_donors = extract_markdown_equations(markdown_files, output_dir / "MARKDOWN_EQUATION_DONORS.json")
    source_pdf_copy = output_dir / "SOURCE_PDF.pdf"
    shutil.copy2(pdf_path, source_pdf_copy)

    manifest = {
        "version": "mathpix-input-collector-0.2",
        "pdf": {
            "path": str(pdf_path),
            "sha256": _sha256(pdf_path),
            "normalizedStem": pdf_stem,
            "diagnosticCopy": str(source_pdf_copy),
        },
        "source": {"path": str(source), "kind": source_kind},
        "selectedReferenceDocx": str(selected_docx),
        "docxCandidates": docx_candidates,
        "markdownFiles": [str(path) for path in markdown_files],
        "htmlFiles": [str(path) for path in html_files],
        "markdownElementMap": str(output_dir / "MARKDOWN_ELEMENT_MAP.json"),
        "markdownElementCount": markdown_element_map.get("count", 0),
        "markdownElementTypeCounts": markdown_element_map.get("typeCounts", {}),
        "markdownEquationDonors": str(output_dir / "MARKDOWN_EQUATION_DONORS.json"),
        "markdownEquationDonorCount": equation_donors.get("count", 0),
        "assetRoots": [str(path) for path in (nested, extracted) if path.exists()],
        "assetCounts": {
            "all": len(asset_files),
            "raster": sum(path.suffix.lower() != ".svg" for path in asset_files),
            "svg": sum(path.suffix.lower() == ".svg" for path in asset_files),
            "uniqueRasterHashes": len(asset_hashes),
        },
        "selectionRule": "stem compatibility + bundled export + exact image overlap + OMML inventory",
        "notDone": [
            "The collector defines the automatic input package; it does not yet reconstruct editable composites from Markdown overlays.",
            "SVG assets embedded in the selected DOCX are paired with their raster fallback by the native builder; selection remains conservative and may choose no SVG-backed asset in a specific page range.",
        ],
    }
    manifest_path = output_dir / "MATHPIX_INPUT_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest["manifestPath"] = str(manifest_path)
    return manifest
