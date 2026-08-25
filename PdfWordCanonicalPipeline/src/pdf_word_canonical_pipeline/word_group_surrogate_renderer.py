from __future__ import annotations

"""HF24 failure-isolated fidelity capture for complex Word DrawingML groups.

The primary Word reference is now the raster bitmap placed directly on the Windows
clipboard by Word. PowerPoint is only an isolated fallback, because HF23 showed
that PasteSpecial itself can distort or lose otherwise valid Word output.

The browser importer should not optimistically reimplement every Word drawing
semantic.  This module classifies top-level WordprocessingML groups before the
canonical DOCX is emitted:

* native-safe   -> ordinary native SVG reconstruction is allowed;
* hybrid-safe   -> mixed picture/vector group is still within the supported
                   DrawingML subset;
* render-required -> Word itself renders the whole group to a browser PNG
                     surrogate while the original DOCX group stays untouched.

The surrogate is BookWriter-only metadata.  No Word relationship, OLE payload,
WMF/EMF part or source drawing is replaced, so the Word-rendered page map remains
valid.
"""

import gc
import json
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from .word_clipboard_capture import clipboard_image_to_png, clipboard_enhmetafile_to_png, clear_windows_clipboard
from .word_composite_rasterizer import _clipboard_to_png_with_word_pdf

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"w": W, "wpg": WPG, "wps": WPS, "a": A, "pic": PIC, "wp": WP, "mc": MC}

MANIFEST_PATH = "customXml/bookwriter-group-surrogates.json"
MEDIA_PREFIX = "word/media/bw_group_surrogate_"
PP_LAYOUT_BLANK = 12
PP_PASTE_ENHANCED_METAFILE = 2
PP_SHAPE_FORMAT_PNG = 2
WD_DO_NOT_SAVE_CHANGES = 0
WD_FORMAT_PDF = 17
WD_ACTIVE_END_PAGE_NUMBER = 3
WD_HORIZONTAL_POSITION_RELATIVE_TO_PAGE = 5
WD_VERTICAL_POSITION_RELATIVE_TO_PAGE = 6

# Shapes already implemented faithfully by the browser renderer. A group
# containing only these, without risky transforms/effects/custom geometry,
# can stay editable.
#
# STAGE9A2A_WORD_SHAPE_GEOMETRY_TRUTH
# cloudCallout is deliberately NOT native-safe. The browser renderer's old
# implementation reduced the Word cloud outline to an ellipse + two circles,
# which is not the DrawingML cloudCallout geometry. Until the full preset
# geometry is implemented, Word itself is the visual authority for this preset.
SAFE_PRESETS = {
    "rect", "roundRect", "ellipse", "line", "straightConnector1",
    "bentConnector3", "arc", "stripedRightArrow",
}

WORD_RENDER_PRESETS = {
    "cloudCallout",
}


def _safe(callable_, default=None):
    try:
        return callable_()
    except Exception:
        return default


def _top_groups(root: etree._Element) -> list[etree._Element]:
    return list(root.xpath(".//wpg:wgp[not(ancestor::wpg:wgp)]", namespaces=NS))


def _group_scale(group: etree._Element) -> tuple[float, float]:
    values: list[float] = []
    for xfrm in group.xpath(".//a:xfrm", namespaces=NS):
        ext = xfrm.find(f"{{{A}}}ext")
        child = xfrm.find(f"{{{A}}}chExt")
        if ext is None or child is None:
            continue
        for axis in ("cx", "cy"):
            try:
                outer = float(ext.get(axis) or 0)
                inner = float(child.get(axis) or 0)
                if inner:
                    values.append(abs(outer / inner))
            except Exception:
                pass
    if not values:
        return 1.0, 1.0
    return min(values), max(values)




def _expected_extent_pt(group: etree._Element) -> tuple[float, float]:
    drawings = group.xpath("ancestor::w:drawing[1]", namespaces=NS)
    if not drawings:
        return 0.0, 0.0
    extents = drawings[0].xpath(".//wp:extent[1]", namespaces=NS)
    if not extents:
        return 0.0, 0.0
    extent = extents[0]
    try:
        return float(extent.get("cx") or 0) / 12700.0, float(extent.get("cy") or 0) / 12700.0
    except Exception:
        return 0.0, 0.0


def _group_context(group: etree._Element, ordinal: int) -> dict[str, Any]:
    paragraphs = group.xpath("ancestor::w:p[1]", namespaces=NS)
    paragraph_ordinal = 0
    if paragraphs:
        try:
            paragraph_ordinal = int(paragraphs[0].xpath("count(preceding::w:p)", namespaces=NS)) + 1
        except Exception:
            paragraph_ordinal = 0
    tables = group.xpath("ancestor::w:tbl[1]", namespaces=NS)
    rows = group.xpath("ancestor::w:tr[1]", namespaces=NS)
    cells = group.xpath("ancestor::w:tc[1]", namespaces=NS)
    table_ordinal = row_ordinal = cell_ordinal = 0
    if tables:
        try:
            table_ordinal = int(tables[0].xpath("count(preceding::w:tbl)", namespaces=NS)) + 1
        except Exception:
            pass
    if rows:
        try:
            row_ordinal = int(rows[0].xpath("count(preceding-sibling::w:tr)", namespaces=NS)) + 1
        except Exception:
            pass
    if cells:
        try:
            cell_ordinal = int(cells[0].xpath("count(preceding-sibling::w:tc)", namespaces=NS)) + 1
        except Exception:
            pass
    expected_w, expected_h = _expected_extent_pt(group)
    triage_id = f"G{ordinal:04d}"
    context_key = f"p{paragraph_ordinal:04d}" if paragraph_ordinal else "p?"
    if table_ordinal:
        context_key += f"-t{table_ordinal:02d}-r{row_ordinal:02d}-c{cell_ordinal:02d}"
    return {
        "triageId": triage_id,
        "contextKey": context_key,
        "paragraphOrdinal": paragraph_ordinal,
        "tableOrdinal": table_ordinal,
        "rowOrdinal": row_ordinal,
        "cellOrdinal": cell_ordinal,
        "expectedWidthPt": round(expected_w, 3),
        "expectedHeightPt": round(expected_h, 3),
    }


def _capture_geometry_check(expected_w: float, expected_h: float, captured_w: float, captured_h: float) -> dict[str, Any]:
    if expected_w <= 0 or expected_h <= 0 or captured_w <= 0 or captured_h <= 0:
        return {"status": "unknown", "widthRatio": None, "heightRatio": None}
    wr = captured_w / expected_w
    hr = captured_h / expected_h
    # A Word/COM capture does not need exact point equality, but a large mismatch
    # usually means we copied a neighbouring shape/range instead of the target group.
    ok = 0.80 <= wr <= 1.25 and 0.80 <= hr <= 1.25
    return {"status": "match" if ok else "mismatch", "widthRatio": round(wr, 4), "heightRatio": round(hr, 4)}

def _classify_group(group: etree._Element, ordinal: int) -> dict[str, Any]:
    presets = [str(v) for v in group.xpath(".//a:prstGeom/@prst", namespaces=NS)]
    word_render_presets = sorted({p for p in presets if p in WORD_RENDER_PRESETS})
    unknown = sorted({p for p in presets if p not in SAFE_PRESETS and p not in WORD_RENDER_PRESETS})
    shape_count = len(group.xpath(".//wps:wsp", namespaces=NS))
    picture_count = len(group.xpath(".//pic:pic", namespaces=NS))
    custom_count = len(group.xpath(".//a:custGeom", namespaces=NS))
    pattern_count = len(group.xpath(".//a:pattFill", namespaces=NS))
    nested_count = len(group.xpath(".//wpg:wgp", namespaces=NS))
    effect_count = len(group.xpath(".//a:effectLst | .//a:effectDag", namespaces=NS))
    min_scale, max_scale = _group_scale(group)
    in_table = bool(group.xpath("ancestor::w:tc[1]", namespaces=NS))
    inline = bool(group.xpath("ancestor::wp:inline[1]", namespaces=NS))
    anchored = bool(group.xpath("ancestor::wp:anchor[1]", namespaces=NS))
    reasons: list[str] = []
    if custom_count:
        reasons.append(f"custom-geometry:{custom_count}")
    if pattern_count:
        reasons.append(f"pattern-fill:{pattern_count}")
    if nested_count:
        reasons.append(f"nested-group:{nested_count}")
    if effect_count:
        reasons.append(f"drawing-effects:{effect_count}")
    if word_render_presets:
        reasons.append("word-render-required-presets:" + ",".join(word_render_presets))
    if unknown:
        reasons.append("unsupported-presets:" + ",".join(unknown))
    if max_scale > 10.0 or min_scale < 0.10:
        reasons.append(f"high-risk-group-scale:{min_scale:.4g}..{max_scale:.4g}")

    if reasons:
        fidelity = "render-required"
    elif picture_count and shape_count:
        fidelity = "hybrid-safe"
    else:
        fidelity = "native-safe"

    context = _group_context(group, ordinal)
    return {
        "ordinal": ordinal,
        **context,
        "fidelityClass": fidelity,
        "reasons": reasons,
        "shapeCount": shape_count,
        "pictureCount": picture_count,
        "customGeometryCount": custom_count,
        "patternFillCount": pattern_count,
        "nestedGroupCount": nested_count,
        "effectCount": effect_count,
        "presets": presets,
        "wordRenderPresets": word_render_presets,
        "unknownPresets": unknown,
        "minGroupScale": min_scale,
        "maxGroupScale": max_scale,
        "inTableCell": in_table,
        "inline": inline,
        "anchored": anchored,
    }


def inspect_group_fidelity(docx_path: Path) -> dict[str, Any]:
    docx_path = Path(docx_path)
    with ZipFile(docx_path, "r") as zf:
        root = etree.fromstring(zf.read("word/document.xml"))
    rows = [_classify_group(group, i) for i, group in enumerate(_top_groups(root), 1)]
    return {
        "version": 1,
        "source": "hf26-source-page-fidelity-boundary",
        "groupCount": len(rows),
        "nativeSafe": sum(r["fidelityClass"] == "native-safe" for r in rows),
        "hybridSafe": sum(r["fidelityClass"] == "hybrid-safe" for r in rows),
        "renderRequired": sum(r["fidelityClass"] == "render-required" for r in rows),
        "groups": rows,
    }


def _write_docx(entries: dict[str, bytes], infos, output: Path) -> None:
    temp = output.with_suffix(output.suffix + ".tmp")
    with ZipFile(temp, "w", ZIP_DEFLATED) as target:
        written: set[str] = set()
        for info in infos:
            if info.filename in entries:
                target.writestr(info, entries[info.filename])
                written.add(info.filename)
        for name, payload in entries.items():
            if name not in written:
                target.writestr(name, payload)
    temp.replace(output)


def _instrument_bookmarks(input_docx: Path, output_docx: Path, required_ordinals: set[int]) -> dict[int, str]:
    with ZipFile(input_docx, "r") as zf:
        infos = zf.infolist()
        entries = {info.filename: zf.read(info.filename) for info in infos}
    root = etree.fromstring(entries["word/document.xml"])
    groups = _top_groups(root)
    existing_ids = []
    for node in root.xpath(".//w:bookmarkStart", namespaces=NS):
        try:
            existing_ids.append(int(node.get(f"{{{W}}}id") or 0))
        except Exception:
            pass
    next_id = max(existing_ids or [1000]) + 1
    names: dict[int, str] = {}
    for ordinal, group in enumerate(groups, 1):
        if ordinal not in required_ordinals:
            continue
        runs = group.xpath("ancestor::w:r[1]", namespaces=NS)
        if not runs:
            continue
        run = runs[0]
        parent = run.getparent()
        if parent is None:
            continue
        name = f"BW_GRP_{ordinal:04d}"
        start = etree.Element(f"{{{W}}}bookmarkStart")
        start.set(f"{{{W}}}id", str(next_id))
        start.set(f"{{{W}}}name", name)
        end = etree.Element(f"{{{W}}}bookmarkEnd")
        end.set(f"{{{W}}}id", str(next_id))
        next_id += 1
        idx = parent.index(run)
        parent.insert(idx, start)
        parent.insert(idx + 2, end)
        names[ordinal] = name
    entries["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    _write_docx(entries, infos, output_docx)
    return names


class _PowerPointExporter:
    def __init__(self, win32_client):
        self.win32 = win32_client
        self.ppt = None
        self.presentation = None
        self.slide = None

    def __enter__(self):
        self.ppt = self.win32.DispatchEx("PowerPoint.Application")
        self.presentation = self.ppt.Presentations.Add(WithWindow=False)
        self.slide = self.presentation.Slides.Add(1, PP_LAYOUT_BLANK)
        return self

    def export_clipboard(self, png_path: Path, width_pt: float, height_pt: float) -> None:
        shape_range = None
        pasted = None
        try:
            try:
                shape_range = self.slide.Shapes.PasteSpecial(DataType=PP_PASTE_ENHANCED_METAFILE)
            except Exception:
                shape_range = self.slide.Shapes.Paste()
            pasted = shape_range.Item(1)
            width = max(float(width_pt or 0), float(_safe(lambda: pasted.Width, 0) or 0), 36.0)
            height = max(float(height_pt or 0), float(_safe(lambda: pasted.Height, 0) or 0), 24.0)
            scale = min(6.0, 4096.0 / max(width, height, 1.0))
            px_w = max(64, int(round(width * scale)))
            px_h = max(64, int(round(height * scale)))
            pasted.Export(str(png_path), PP_SHAPE_FORMAT_PNG, px_w, px_h)
            if not png_path.exists() or png_path.stat().st_size <= 100:
                raise RuntimeError("PowerPoint export did not produce a valid PNG.")
        finally:
            if pasted is not None:
                try:
                    pasted.Delete()
                except Exception:
                    pass

    def __exit__(self, exc_type, exc, tb):
        if self.presentation is not None:
            try:
                self.presentation.Close()
            except Exception:
                pass
        if self.ppt is not None:
            try:
                self.ppt.Quit()
            except Exception:
                pass
        self.slide = None
        self.presentation = None
        self.ppt = None


def _copy_bookmark_visual(word, doc, bookmark_name: str) -> tuple[float, float, str]:
    bookmark = doc.Bookmarks.Item(bookmark_name)
    rng = bookmark.Range
    # Anchored group: Word exposes the ShapeRange at the bookmark's anchor.
    try:
        shape_range = rng.ShapeRange
        if int(shape_range.Count or 0) > 0:
            shape = shape_range.Item(1)
            width = float(_safe(lambda: shape.Width, 0.0) or 0.0)
            height = float(_safe(lambda: shape.Height, 0.0) or 0.0)
            # HF24: request Word's rendered picture explicitly. Shape.Copy can
            # place an editable Office object/metafile on the clipboard without a
            # raster CF_DIB, which defeats the direct-bitmap isolation path.
            shape.Select()
            word.Selection.CopyAsPicture()
            time.sleep(0.12)
            return width, height, "shape-selection-picture"
    except Exception:
        pass
    # Inline group: select/copy the exact bookmarked run.  The bookmark was
    # inserted only in the temporary probe copy and does not mutate the user's DOCX.
    try:
        inline_shapes = rng.InlineShapes
        if int(inline_shapes.Count or 0) > 0:
            inline = inline_shapes.Item(1)
            width = float(_safe(lambda: inline.Width, 0.0) or 0.0)
            height = float(_safe(lambda: inline.Height, 0.0) or 0.0)
            try:
                rng.CopyAsPicture()
            except Exception:
                rng.Select()
                word.Selection.CopyAsPicture()
            time.sleep(0.12)
            return width, height, "inline-range"
    except Exception:
        pass
    # Last-resort exact range rendering; this remains preferable to guessing
    # DrawingML semantics for a group already classified render-required.
    try:
        rng.CopyAsPicture()
    except Exception:
        rng.Select()
        word.Selection.CopyAsPicture()
    time.sleep(0.12)
    return 0.0, 0.0, "bookmark-range"



def _export_source_pdf(doc, pdf_path: Path) -> tuple[bool, str]:
    """Render the temporary bookmarked document with Word itself.

    This is the terminal visual authority for inline render-required groups.
    It avoids all clipboard / metafile reinterpretation: Word lays out and
    paints the original page, then BookWriter crops only the target rectangle.
    """
    try:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        doc.ExportAsFixedFormat(str(pdf_path), WD_FORMAT_PDF)
        if not pdf_path.exists() or pdf_path.stat().st_size < 200:
            return False, "Word source-page export did not produce a valid PDF."
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _inline_group_page_crop(doc, bookmark_name: str, source_pdf: Path, png_path: Path) -> dict[str, Any]:
    """Crop an inline Word group from Word's own rendered page.

    For inline objects Word exposes page-relative x/y directly via Range.Information,
    so this fallback is deterministic and does not need to reinterpret DrawingML,
    WMF/EMF or clipboard transforms.
    """
    audit: dict[str, Any] = {
        "ok": False,
        "backend": "word-source-page-inline-crop",
        "page": 0,
        "xPt": 0.0,
        "yPt": 0.0,
        "widthPt": 0.0,
        "heightPt": 0.0,
        "error": "",
    }
    try:
        import fitz  # type: ignore
        bookmark = doc.Bookmarks.Item(bookmark_name)
        rng = bookmark.Range
        if int(getattr(rng.InlineShapes, "Count", 0) or 0) < 1:
            audit["error"] = "bookmark has no inline shape"
            return audit
        inline = rng.InlineShapes.Item(1)
        irng = inline.Range
        page_no = int(irng.Information(WD_ACTIVE_END_PAGE_NUMBER) or 0)
        x = float(irng.Information(WD_HORIZONTAL_POSITION_RELATIVE_TO_PAGE) or -1.0)
        y = float(irng.Information(WD_VERTICAL_POSITION_RELATIVE_TO_PAGE) or -1.0)
        width = float(getattr(inline, "Width", 0.0) or 0.0)
        height = float(getattr(inline, "Height", 0.0) or 0.0)
        audit.update({
            "page": page_no,
            "xPt": round(x, 3),
            "yPt": round(y, 3),
            "widthPt": round(width, 3),
            "heightPt": round(height, 3),
        })
        if page_no < 1 or x < 0 or y < 0 or width <= 0 or height <= 0:
            audit["error"] = "Word did not expose a usable page-relative inline rectangle"
            return audit
        if not source_pdf.exists():
            audit["error"] = "source PDF unavailable"
            return audit
        with fitz.open(source_pdf) as pdf:
            if page_no > pdf.page_count:
                audit["error"] = f"page {page_no} outside rendered PDF ({pdf.page_count})"
                return audit
            page = pdf[page_no - 1]
            pad = 0.75
            rect = fitz.Rect(
                max(0.0, x - pad),
                max(0.0, y - pad),
                min(float(page.rect.width), x + width + pad),
                min(float(page.rect.height), y + height + pad),
            )
            if rect.width <= 1 or rect.height <= 1:
                audit["error"] = "computed crop rectangle is empty"
                return audit
            pix = page.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), clip=rect, alpha=False)
            png_path.parent.mkdir(parents=True, exist_ok=True)
            pix.save(str(png_path))
        if not png_path.exists() or png_path.stat().st_size <= 150:
            audit["error"] = "page crop did not produce a valid PNG"
            return audit
        audit["ok"] = True
        audit["bytes"] = png_path.stat().st_size
        return audit
    except Exception as exc:
        audit["error"] = f"{type(exc).__name__}: {exc}"
        return audit


def _png_content_metrics(png_path: Path) -> dict[str, Any]:
    """Measure visible content bounds without pretending this proves fidelity."""
    try:
        from PIL import Image  # type: ignore
        image = Image.open(png_path).convert("RGBA")
        px = image.load()
        xs: list[int] = []
        ys: list[int] = []
        for y in range(image.height):
            for x in range(image.width):
                r, g, b, a = px[x, y]
                if a > 8 and min(r, g, b) < 242:
                    xs.append(x); ys.append(y)
        if not xs:
            return {"status": "blank", "widthPx": image.width, "heightPx": image.height, "inkFraction": 0.0}
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
        box_w = right - left + 1; box_h = bottom - top + 1
        return {
            "status": "measured",
            "widthPx": image.width, "heightPx": image.height,
            "inkBounds": {"left": left, "top": top, "right": right, "bottom": bottom},
            "inkWidthFraction": round(box_w / max(1, image.width), 4),
            "inkHeightFraction": round(box_h / max(1, image.height), 4),
            "inkFraction": round((box_w * box_h) / max(1, image.width * image.height), 4),
        }
    except Exception as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}


def _embed_manifest_and_media(target_docx: Path, manifest: dict[str, Any], media: dict[str, Path]) -> None:
    target_docx = Path(target_docx)
    with ZipFile(target_docx, "r") as zf:
        infos = zf.infolist()
        entries = {info.filename: zf.read(info.filename) for info in infos}
    entries[MANIFEST_PATH] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    for package_path, file_path in media.items():
        entries[package_path] = Path(file_path).read_bytes()
    _write_docx(entries, infos, target_docx)


def render_required_group_surrogates(source_docx: Path, target_docx: Path) -> dict[str, Any]:
    """HF26: isolate complex groups and terminate inline failures with a Word-page crop.

    This is intentionally a diagnostic checkpoint.  It does not add new SVG
    semantics.  Each group receives a stable Gxxxx id and an explicit failing
    stage.  A render-required group is allowed to use the Word reference only
    when the capture geometry is consistent with the source wp:extent; otherwise
    it is marked ``source-crop-required`` instead of silently using a doubtful
    capture.
    """
    source_docx = Path(source_docx).resolve()
    target_docx = Path(target_docx).resolve()
    inventory = inspect_group_fidelity(source_docx)
    all_ordinals = {int(row["ordinal"]) for row in inventory["groups"]}
    required = {int(row["ordinal"]) for row in inventory["groups"] if row["fidelityClass"] == "render-required"}
    report: dict[str, Any] = {
        "version": 5,
        "source": "hf26-source-page-fidelity-boundary",
        "platform": platform.platform(),
        "available": False,
        "status": "not-required" if not all_ordinals else "pending",
        "inventory": inventory,
        "groupCount": len(all_ordinals),
        "renderRequired": len(required),
        "referencesCaptured": 0,
        "referencesFailed": 0,
        "geometryMismatch": 0,
        "contentMismatch": 0,
        "rendered": 0,
        "failed": 0,
        "sourceCropRequired": 0,
        "browserValidationRequired": 0,
        "directBitmapReferences": 0,
        "wordPdfReferences": 0,
        "enhMetafileReferences": 0,
        "powerPointFallbackReferences": 0,
        "sourcePageCropReferences": 0,
        "groups": {},
    }
    base_groups = {str(row["ordinal"]): dict(row) for row in inventory["groups"]}
    manifest: dict[str, Any] = {
        "version": 5,
        "source": "hf26-source-page-fidelity-boundary",
        "groups": base_groups,
        "summary": {
            "groupCount": inventory["groupCount"],
            "nativeSafe": inventory["nativeSafe"],
            "hybridSafe": inventory["hybridSafe"],
            "renderRequired": inventory["renderRequired"],
            "referencesCaptured": 0,
            "referencesFailed": 0,
            "geometryMismatch": 0,
            "contentMismatch": 0,
            "rendered": 0,
            "failed": 0,
            "sourceCropRequired": 0,
            "browserValidationRequired": 0,
            "directBitmapReferences": 0,
            "wordPdfReferences": 0,
            "enhMetafileReferences": 0,
            "powerPointFallbackReferences": 0,
            "sourcePageCropReferences": 0,
        },
    }
    if not all_ordinals:
        _embed_manifest_and_media(target_docx, manifest, {})
        report["available"] = True
        return report

    def mark_unavailable(reason: str, error: str = "") -> dict[str, Any]:
        report["status"] = reason
        if error:
            report["error"] = error
        for ordinal in sorted(all_ordinals):
            row = base_groups[str(ordinal)]
            row["referenceStatus"] = "unavailable"
            row["triageStage"] = "word-reference-unavailable"
            if row.get("fidelityClass") == "render-required":
                row["decision"] = "source-crop-required"
                report["sourceCropRequired"] += 1
            else:
                row["decision"] = "native-browser-validation-required"
                report["browserValidationRequired"] += 1
            report["referencesFailed"] += 1
        report["failed"] = len(required)
        manifest["summary"].update({
            "referencesFailed": report["referencesFailed"],
            "failed": report["failed"],
            "sourceCropRequired": report["sourceCropRequired"],
            "browserValidationRequired": report["browserValidationRequired"],
            "directBitmapReferences": report["directBitmapReferences"],
            "wordPdfReferences": report["wordPdfReferences"],
            "enhMetafileReferences": report.get("enhMetafileReferences", 0),
            "powerPointFallbackReferences": report["powerPointFallbackReferences"],
            "sourcePageCropReferences": report.get("sourcePageCropReferences", 0),
            "contentMismatch": report.get("contentMismatch", 0),
        })
        _embed_manifest_and_media(target_docx, manifest, {})
        return report

    if os.name != "nt":
        return mark_unavailable("word-com-unavailable-non-windows")
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        return mark_unavailable("word-com-unavailable-pywin32", str(exc))

    scratch = Path(tempfile.mkdtemp(prefix="bookwriter_hf26_composite_triage_"))
    probe_docx = scratch / "group_probe.docx"
    image_dir = scratch / "png"
    image_dir.mkdir(parents=True, exist_ok=True)
    source_pdf = scratch / "word_rendered_source.pdf"
    media_to_embed: dict[str, Path] = {}
    word = None
    doc = None
    pythoncom.CoInitialize()
    try:
        bookmarks = _instrument_bookmarks(source_docx, probe_docx, all_ordinals)
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(probe_docx), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            doc.Repaginate()
        except Exception:
            pass
        source_pdf_ok, source_pdf_error = _export_source_pdf(doc, source_pdf)
        report["sourcePagePdfAvailable"] = bool(source_pdf_ok)
        if source_pdf_error:
            report["sourcePagePdfError"] = source_pdf_error
        for ordinal in sorted(all_ordinals):
                row = base_groups[str(ordinal)]
                triage_id = str(row.get("triageId") or f"G{ordinal:04d}")
                bookmark_name = bookmarks.get(ordinal)
                if not bookmark_name:
                    row.update({
                        "referenceStatus": "failed-no-bookmark",
                        "triageStage": "bind-failed",
                        "decision": "source-crop-required" if row.get("fidelityClass") == "render-required" else "native-browser-validation-required",
                    })
                    report["referencesFailed"] += 1
                    if row.get("fidelityClass") == "render-required":
                        report["sourceCropRequired"] += 1
                        report["failed"] += 1
                    else:
                        report["browserValidationRequired"] += 1
                    report["groups"][triage_id] = {"status": "bind-failed", "ordinal": ordinal}
                    continue
                png = image_dir / f"reference_{ordinal:04d}.png"
                try:
                    width, height, backend = _copy_bookmark_visual(word, doc, bookmark_name)
                    direct = clipboard_image_to_png(png, timeout_s=2.0)
                    if direct.get("ok"):
                        reference_backend = f"word-{backend}-clipboard-bitmap"
                        report["directBitmapReferences"] += 1
                    else:
                        # HF25: when Word publishes only CF_ENHMETAFILE, render that
                        # metafile directly with Windows GDI at the Word-reported
                        # extent.  Do not ask PowerPoint to reinterpret group
                        # transforms/patterns.
                        emf = clipboard_enhmetafile_to_png(png, width_pt=width, height_pt=height)
                        if emf.get("ok"):
                            reference_backend = f"word-{backend}-clipboard-enhmetafile-gdi"
                            report["enhMetafileReferences"] += 1
                        else:
                            # Keep the next fallback inside Word before involving PowerPoint.
                            width, height, backend = _copy_bookmark_visual(word, doc, bookmark_name)
                            word_pdf_ok, word_pdf_error = _clipboard_to_png_with_word_pdf(word, png, width, height)
                            if word_pdf_ok:
                                reference_backend = f"word-{backend}-word-pdf-pymupdf"
                                report["wordPdfReferences"] += 1
                            else:
                                # Final fallback: re-copy and isolate PowerPoint to this reference.
                                width, height, backend = _copy_bookmark_visual(word, doc, bookmark_name)
                                with _PowerPointExporter(win32com.client) as exporter:
                                    exporter.export_clipboard(png, width, height)
                                reference_backend = f"word-{backend}-fresh-powerpoint-png"
                                report["powerPointFallbackReferences"] += 1
                                row["wordPdfFallbackError"] = word_pdf_error
                                row["enhMetafileFallbackError"] = str(emf.get("error") or "")
                    # HF26 terminal fidelity boundary: for an inline group that
                    # was already classified render-required, the authoritative
                    # visual is the object rectangle cropped from Word's own
                    # rendered source page. This bypasses clipboard/metafile
                    # semantics entirely while keeping the surrounding table/text
                    # native and editable.
                    source_crop_audit: dict[str, Any] = {}
                    if row.get("fidelityClass") == "render-required" and bool(row.get("inline")) and source_pdf_ok:
                        crop_png = image_dir / f"reference_{ordinal:04d}_source_crop.png"
                        source_crop_audit = _inline_group_page_crop(doc, bookmark_name, source_pdf, crop_png)
                        row["sourcePageCropAudit"] = source_crop_audit
                        if source_crop_audit.get("ok"):
                            shutil.copyfile(crop_png, png)
                            reference_backend = "word-source-page-inline-crop"
                            report["sourcePageCropReferences"] += 1

                    package_path = f"word/media/bw_triage_reference_{ordinal:04d}.png"
                    media_to_embed[package_path] = png
                    expected_w = float(row.get("expectedWidthPt") or 0.0)
                    expected_h = float(row.get("expectedHeightPt") or 0.0)
                    geometry = _capture_geometry_check(expected_w, expected_h, float(width or 0.0), float(height or 0.0))
                    content_metrics = _png_content_metrics(png)
                    expected_aspect = (expected_w / expected_h) if expected_w > 0 and expected_h > 0 else 0.0
                    png_w = float(content_metrics.get("widthPx") or 0.0)
                    png_h = float(content_metrics.get("heightPx") or 0.0)
                    png_aspect = (png_w / png_h) if png_w > 0 and png_h > 0 else 0.0
                    aspect_ratio = (png_aspect / expected_aspect) if expected_aspect > 0 and png_aspect > 0 else 0.0
                    content_check = {
                        "status": "ok",
                        "expectedAspect": round(expected_aspect, 4) if expected_aspect else 0.0,
                        "pngAspect": round(png_aspect, 4) if png_aspect else 0.0,
                        "aspectRatio": round(aspect_ratio, 4) if aspect_ratio else 0.0,
                    }
                    if content_metrics.get("status") == "blank":
                        content_check["status"] = "mismatch-blank"
                    elif aspect_ratio and (aspect_ratio < 0.5 or aspect_ratio > 2.0):
                        content_check["status"] = "mismatch-aspect"
                    row.update({
                        "referenceStatus": "captured",
                        "referencePath": package_path,
                        "referenceBackend": reference_backend,
                        "captureWidthPt": round(float(width or 0.0), 3),
                        "captureHeightPt": round(float(height or 0.0), 3),
                        "capturePngBytes": png.stat().st_size,
                        "captureGeometry": geometry,
                        "captureContent": content_metrics,
                        "captureContentCheck": content_check,
                        "sourcePageCropAudit": source_crop_audit,
                    })
                    report["referencesCaptured"] += 1
                    # STAGE9A2A_FIX1_ANCHORED_CLOUD_WORD_AUTHORITY
                    #
                    # cloudCallout groups in the real chemistry DOCX are anchored
                    # DrawingML groups (inline=False). HF26's terminal source-page
                    # crop only applies to inline groups. The previous 9A2a
                    # classification therefore still allowed an anchored cloud
                    # capture to be rejected by the generic geometry/aspect gate,
                    # after which docx-core silently fell back to the old native
                    # ellipse approximation.
                    #
                    # For a preset explicitly placed in WORD_RENDER_PRESETS, the
                    # Word CopyAsPicture result is the visual authority. Geometry
                    # mismatches remain audited, but they no longer veto a
                    # non-blank Word capture.
                    force_word_preset = (
                        row.get("fidelityClass") == "render-required"
                        and bool(set(row.get("wordRenderPresets") or []) & WORD_RENDER_PRESETS)
                    )
                    capture_nonblank = str(content_metrics.get("status") or "") != "blank"

                    if force_word_preset and capture_nonblank:
                        if geometry.get("status") == "mismatch":
                            report["geometryMismatch"] += 1
                        if str(content_check.get("status") or "") != "ok":
                            report["contentMismatch"] = int(report.get("contentMismatch") or 0) + 1
                        row["triageStage"] = "word-reference-ok-preset-authority"
                        row.update({
                            "decision": "word-reference-surrogate",
                            "renderStatus": "rendered",
                            "path": package_path,
                            "renderBackend": row["referenceBackend"],
                            "wordPresetAuthority": True,
                        })
                        report["rendered"] += 1
                    elif geometry.get("status") == "mismatch":
                        report["geometryMismatch"] += 1
                        row["triageStage"] = "word-capture-geometry-mismatch"
                        if row.get("fidelityClass") == "render-required":
                            row["decision"] = "source-crop-required"
                            report["sourceCropRequired"] += 1
                            report["failed"] += 1
                        else:
                            row["decision"] = "native-browser-validation-required"
                            report["browserValidationRequired"] += 1
                    elif str(content_check.get("status") or "") != "ok":
                        report["contentMismatch"] = int(report.get("contentMismatch") or 0) + 1
                        row["triageStage"] = "word-capture-content-mismatch"
                        if row.get("fidelityClass") == "render-required":
                            row["decision"] = "source-crop-required"
                            report["sourceCropRequired"] += 1
                            report["failed"] += 1
                        else:
                            row["decision"] = "native-browser-validation-required"
                            report["browserValidationRequired"] += 1
                    else:
                        row["triageStage"] = "word-reference-ok"
                        if row.get("fidelityClass") == "render-required":
                            # The exact Word reference becomes the browser surrogate.
                            row.update({
                                "decision": "word-reference-surrogate",
                                "renderStatus": "rendered",
                                "path": package_path,
                                "renderBackend": row["referenceBackend"],
                            })
                            report["rendered"] += 1
                        else:
                            row["decision"] = "native-browser-validation-required"
                            report["browserValidationRequired"] += 1
                    report["groups"][triage_id] = {
                        "status": row["triageStage"],
                        "ordinal": ordinal,
                        "fidelityClass": row.get("fidelityClass"),
                        "decision": row.get("decision"),
                        "referencePath": package_path,
                        "referenceBackend": row.get("referenceBackend"),
                        "captureGeometry": geometry,
                        "captureContent": row.get("captureContent"),
                        "captureContentCheck": row.get("captureContentCheck"),
                        "sourcePageCropAudit": row.get("sourcePageCropAudit"),
                    }
                except Exception as exc:
                    row.update({
                        "referenceStatus": "failed",
                        "referenceError": f"{type(exc).__name__}: {exc}",
                        "triageStage": "word-capture-failed",
                    })
                    report["referencesFailed"] += 1
                    if row.get("fidelityClass") == "render-required":
                        row["decision"] = "source-crop-required"
                        report["sourceCropRequired"] += 1
                        report["failed"] += 1
                    else:
                        row["decision"] = "native-browser-validation-required"
                        report["browserValidationRequired"] += 1
                    report["groups"][triage_id] = {
                        "status": "word-capture-failed",
                        "ordinal": ordinal,
                        "error": row["referenceError"],
                        "decision": row.get("decision"),
                    }
        report["available"] = report["referencesCaptured"] > 0
        if report["sourceCropRequired"]:
            report["status"] = "triage-has-source-crop-required"
        elif report["referencesFailed"]:
            report["status"] = "triage-partial"
        else:
            report["status"] = "triage-reference-complete"
    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
            except Exception:
                pass
        gc.collect()
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        manifest["summary"].update({
            "referencesCaptured": report["referencesCaptured"],
            "referencesFailed": report["referencesFailed"],
            "geometryMismatch": report["geometryMismatch"],
            "rendered": report["rendered"],
            "failed": report["failed"],
            "sourceCropRequired": report["sourceCropRequired"],
            "browserValidationRequired": report["browserValidationRequired"],
            "directBitmapReferences": report["directBitmapReferences"],
            "wordPdfReferences": report["wordPdfReferences"],
            "enhMetafileReferences": report.get("enhMetafileReferences", 0),
            "powerPointFallbackReferences": report["powerPointFallbackReferences"],
            "sourcePageCropReferences": report.get("sourcePageCropReferences", 0),
            "contentMismatch": report.get("contentMismatch", 0),
        })
        _embed_manifest_and_media(target_docx, manifest, media_to_embed)
        shutil.rmtree(scratch, ignore_errors=True)
    return report

