from __future__ import annotations

"""Windows-only Word COM preprocessor for complex Word drawing objects.

The BookWriter importer intentionally does not attempt to reproduce the whole
Word drawing engine. This module asks Microsoft Word itself to replace complex
visual compounds with a single enhanced-metafile picture before the normal
canonical DOCX pass.

Simple paragraphs, tables, inline images and simple text boxes are untouched.
The operation is explicit and audited; unsupported compounds are never silently
flattened by the browser importer.
"""

import argparse
import gc
import json
import os
import platform
import shutil
import tempfile
import time
from copy import deepcopy
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from .word_composite_overlay_manifest import (
    add_manifest_to_docx,
    extract_composite_overlay_manifest,
)

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
V = "urn:schemas-microsoft-com:vml"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
NS = {
    "w": W,
    "wp": WP,
    "wpg": WPG,
    "wps": WPS,
    "v": V,
    "mc": MC,
    "a": A,
    "pic": PIC,
    "r": R,
    "dgm": DGM,
}

# Word/Office constants used without makepy dependency.
WD_COLLAPSE_START = 1
WD_PASTE_ENHANCED_METAFILE = 9
WD_IN_LINE = 0
PP_PASTE_ENHANCED_METAFILE = 2
PP_SHAPE_FORMAT_PNG = 2
PP_LAYOUT_BLANK = 12
WD_DO_NOT_SAVE_CHANGES = 0
WD_FORMAT_XML_DOCUMENT = 12
WD_FORMAT_PDF = 17
MsoGroup = 6
MsoTextBox = 17
MsoCanvas = 20
MsoDiagram = 21
MsoSmartArt = 24
COMPLEX_TYPES = {MsoGroup, MsoCanvas, MsoDiagram, MsoSmartArt}


@dataclass
class CompositeCandidate:
    index: int
    name: str
    shape_type: int
    anchor_start: int
    reasons: list[str]
    width: float
    height: float
    left: float
    top: float


@dataclass
class CompositeResult:
    index: int
    name: str
    status: str
    reasons: list[str]
    error: str = ""


def inspect_docx_complexity(path: Path) -> dict[str, Any]:
    """Static OOXML inventory used before Word COM is launched."""
    path = Path(path)
    with ZipFile(path) as z:
        try:
            raw = z.read("word/document.xml")
        except KeyError as exc:
            raise ValueError("Το DOCX δεν έχει word/document.xml") from exc
    root = etree.fromstring(raw)
    grouped_word = len(root.xpath(".//wpg:wgp", namespaces=NS))
    grouped_vml = len(root.xpath(".//v:group", namespaces=NS))
    smartart = len(root.xpath(".//dgm:relIds", namespaces=NS))
    linked_textboxes = len(root.xpath(".//wps:linkedTxbx", namespaces=NS))
    txbx_tables = len(root.xpath(".//w:txbxContent//w:tbl", namespaces=NS))
    alternate_content = len(root.xpath(".//mc:AlternateContent", namespaces=NS))
    drawing_groups = len(root.xpath(".//a:grpSp", namespaces=NS))
    requires = any((grouped_word, grouped_vml, smartart, drawing_groups))
    return {
        "groupedWordDrawingML": grouped_word,
        "groupedVML": grouped_vml,
        "groupedDrawingML": drawing_groups,
        "smartArt": smartart,
        "linkedTextBoxes": linked_textboxes,
        "tablesInsideTextBoxes": txbx_tables,
        "nativeTextBoxTablesPreserved": txbx_tables,
        "alternateContent": alternate_content,
        "requiresWordCompositeRasterization": bool(requires),
        "detectionNote": (
            "Raw counts can include both Choice and Fallback branches of AlternateContent. "
            "A table inside a plain text box is a native semantic table and no longer triggers rasterization by itself."
        ),
    }


def _safe(callable_, default=None):
    try:
        return callable_()
    except Exception:
        return default


def _shape_reasons(shape) -> list[str]:
    reasons: list[str] = []
    shape_type = int(_safe(lambda: shape.Type, -1) or -1)
    group_count = int(_safe(lambda: shape.GroupItems.Count, 0) or 0)
    canvas_count = int(_safe(lambda: shape.CanvasItems.Count, 0) or 0)
    if shape_type in COMPLEX_TYPES:
        reasons.append(f"complex-shape-type:{shape_type}")
    if group_count > 0:
        reasons.append(f"group-items:{group_count}")
    if canvas_count > 1:
        reasons.append(f"canvas-items:{canvas_count}")
    # A plain Word text box that contains only a table is not a bitmap compound.
    # Its rows, cells, widths and text remain recoverable as a native Word/HTML
    # table, while the outer text-box anchor supplies the Around geometry.
    # Rasterization is reserved for genuinely mixed compositions.
    has_text_frame = bool(_safe(lambda: shape.TextFrame.HasText, False))
    if has_text_frame:
        tables = int(_safe(lambda: shape.TextFrame.TextRange.Tables.Count, 0) or 0)
        inline_shapes = int(_safe(lambda: shape.TextFrame.TextRange.InlineShapes.Count, 0) or 0)
        nested_shapes = int(_safe(lambda: shape.TextFrame.TextRange.ShapeRange.Count, 0) or 0)
        mixed_table_compound = bool(
            tables
            and (
                shape_type in COMPLEX_TYPES
                or group_count > 0
                or canvas_count > 1
                or inline_shapes > 0
                or nested_shapes > 0
            )
        )
        if mixed_table_compound:
            reasons.append(f"mixed-table-compound:{tables}")
        if inline_shapes and (tables or nested_shapes):
            reasons.append(f"mixed-textbox-media:{inline_shapes}")
    return reasons


def _candidate(shape, index: int) -> CompositeCandidate | None:
    reasons = _shape_reasons(shape)
    if not reasons:
        return None
    return CompositeCandidate(
        index=index,
        name=str(_safe(lambda: shape.Name, f"Shape {index}") or f"Shape {index}"),
        shape_type=int(_safe(lambda: shape.Type, -1) or -1),
        anchor_start=int(_safe(lambda: shape.Anchor.Start, 0) or 0),
        reasons=reasons,
        width=float(_safe(lambda: shape.Width, 0.0) or 0.0),
        height=float(_safe(lambda: shape.Height, 0.0) or 0.0),
        left=float(_safe(lambda: shape.Left, 0.0) or 0.0),
        top=float(_safe(lambda: shape.Top, 0.0) or 0.0),
    )


def _capture_geometry(shape) -> dict[str, Any]:
    wrap = _safe(lambda: shape.WrapFormat, None)
    return {
        "anchor_start": int(_safe(lambda: shape.Anchor.Start, 0) or 0),
        "width": float(_safe(lambda: shape.Width, 0.0) or 0.0),
        "height": float(_safe(lambda: shape.Height, 0.0) or 0.0),
        "left": float(_safe(lambda: shape.Left, 0.0) or 0.0),
        "top": float(_safe(lambda: shape.Top, 0.0) or 0.0),
        "relative_h": _safe(lambda: shape.RelativeHorizontalPosition, None),
        "relative_v": _safe(lambda: shape.RelativeVerticalPosition, None),
        "layout_in_cell": _safe(lambda: shape.LayoutInCell, None),
        "lock_anchor": _safe(lambda: shape.LockAnchor, None),
        "allow_overlap": _safe(lambda: shape.WrapFormat.AllowOverlap, None) if wrap is not None else None,
        "wrap_type": _safe(lambda: shape.WrapFormat.Type, None) if wrap is not None else None,
        "distance_left": _safe(lambda: shape.WrapFormat.DistanceLeft, None) if wrap is not None else None,
        "distance_right": _safe(lambda: shape.WrapFormat.DistanceRight, None) if wrap is not None else None,
        "distance_top": _safe(lambda: shape.WrapFormat.DistanceTop, None) if wrap is not None else None,
        "distance_bottom": _safe(lambda: shape.WrapFormat.DistanceBottom, None) if wrap is not None else None,
        "alternative_text": str(_safe(lambda: shape.AlternativeText, "") or ""),
        "title": str(_safe(lambda: shape.Title, "") or ""),
    }


def _apply_geometry(shape, geometry: dict[str, Any]) -> None:
    for prop, key in (
        ("RelativeHorizontalPosition", "relative_h"),
        ("RelativeVerticalPosition", "relative_v"),
        ("Left", "left"),
        ("Top", "top"),
        ("Width", "width"),
        ("Height", "height"),
        ("LayoutInCell", "layout_in_cell"),
        ("LockAnchor", "lock_anchor"),
        ("AlternativeText", "alternative_text"),
        ("Title", "title"),
    ):
        value = geometry.get(key)
        if value is None:
            continue
        try:
            setattr(shape, prop, value)
        except Exception:
            pass
    wrap = _safe(lambda: shape.WrapFormat, None)
    if wrap is not None:
        for prop, key in (
            ("Type", "wrap_type"),
            ("AllowOverlap", "allow_overlap"),
            ("DistanceLeft", "distance_left"),
            ("DistanceRight", "distance_right"),
            ("DistanceTop", "distance_top"),
            ("DistanceBottom", "distance_bottom"),
        ):
            value = geometry.get(key)
            if value is None:
                continue
            try:
                setattr(wrap, prop, value)
            except Exception:
                pass


def _copy_shape_as_picture(word, shape) -> None:
    # Direct Shape.Copy avoids the fragile Selection RPC path on many Word
    # installations while still exposing an enhanced-metafile clipboard
    # format to PowerPoint/Word PasteSpecial.  Selection.CopyAsPicture remains
    # the compatibility fallback.
    direct_error = None
    try:
        shape.Copy()
        time.sleep(0.18)
        return
    except Exception as exc:
        direct_error = exc
    try:
        shape.Select()
        word.Selection.CopyAsPicture()
        time.sleep(0.18)
        return
    except Exception:
        try:
            word.Selection.Copy()
            time.sleep(0.18)
            return
        except Exception as exc:
            raise RuntimeError(
                f"Απέτυχε η αντιγραφή του Word αντικειμένου στο clipboard. "
                f"Direct copy: {direct_error}; selection copy: {exc}"
            ) from exc


def _clipboard_to_png_with_powerpoint(win32_client, png_path: Path, width: float, height: float) -> bool:
    ppt = None
    presentation = None
    try:
        ppt = win32_client.DispatchEx("PowerPoint.Application")
        presentation = ppt.Presentations.Add(WithWindow=False)
        slide = presentation.Slides.Add(1, PP_LAYOUT_BLANK)
        shape_range = slide.Shapes.PasteSpecial(DataType=PP_PASTE_ENHANCED_METAFILE)
        pasted = shape_range.Item(1)
        scale_w = max(32, int(round(float(width or pasted.Width or 300) * 4.0)))
        scale_h = max(32, int(round(float(height or pasted.Height or 180) * 4.0)))
        pasted.Export(str(png_path), PP_SHAPE_FORMAT_PNG, scale_w, scale_h)
        return png_path.exists() and png_path.stat().st_size > 100
    except Exception:
        return False
    finally:
        if presentation is not None:
            try:
                presentation.Close()
            except Exception:
                pass
        if ppt is not None:
            try:
                ppt.Quit()
            except Exception:
                pass


def _clipboard_to_png_with_pillow(png_path: Path) -> bool:
    try:
        from PIL import ImageGrab  # type: ignore
        image = ImageGrab.grabclipboard()
        if image is None or isinstance(image, list):
            return False
        image.save(png_path, format="PNG")
        return png_path.exists() and png_path.stat().st_size > 100
    except Exception:
        return False


def _clipboard_to_png_with_word_pdf(word, png_path: Path, width: float, height: float) -> tuple[bool, str]:
    """Render the clipboard picture through Word -> PDF -> PyMuPDF.

    This is the Word-only fallback.  It deliberately avoids an undeclared
    PowerPoint dependency and also avoids relying on Pillow being able to read
    an enhanced-metafile clipboard object directly.
    """
    temp_doc = None
    temp_dir: Path | None = None
    try:
        import fitz  # type: ignore
        from PIL import Image, ImageChops  # type: ignore

        temp_dir = Path(tempfile.mkdtemp(prefix="bookwriter_word_pdf_export_"))
        pdf_path = temp_dir / "shape.pdf"
        raw_png = temp_dir / "shape_raw.png"
        temp_doc = word.Documents.Add()
        rng = temp_doc.Range(Start=0, End=0)
        try:
            rng.ParagraphFormat.SpaceBefore = 0
            rng.ParagraphFormat.SpaceAfter = 0
            rng.ParagraphFormat.LineSpacing = 1
        except Exception:
            pass
        try:
            rng.PasteSpecial(DataType=WD_PASTE_ENHANCED_METAFILE, Placement=WD_IN_LINE)
        except Exception:
            rng.Paste()
        if int(_safe(lambda: temp_doc.InlineShapes.Count, 0) or 0) < 1:
            if int(_safe(lambda: temp_doc.Shapes.Count, 0) or 0) > 0:
                try:
                    temp_doc.Shapes(1).ConvertToInlineShape()
                except Exception:
                    pass
        if int(_safe(lambda: temp_doc.InlineShapes.Count, 0) or 0) < 1:
            return False, "Word fallback: το clipboard δεν παρήγαγε εικόνα στο προσωρινό Word έγγραφο."
        pasted = temp_doc.InlineShapes(1)
        target_width = max(18.0, float(width or _safe(lambda: pasted.Width, 300.0) or 300.0))
        target_height = max(18.0, float(height or _safe(lambda: pasted.Height, 180.0) or 180.0))
        try:
            pasted.LockAspectRatio = 0
            pasted.Width = target_width
            pasted.Height = target_height
        except Exception:
            pass
        section = temp_doc.Sections(1)
        page_setup = section.PageSetup
        # A tiny frame avoids clipping by Word's PDF exporter.  The rendered
        # page is cropped again below and resized to the original point ratio.
        pad_pt = 4.0
        page_setup.TopMargin = 0
        page_setup.BottomMargin = 0
        page_setup.LeftMargin = 0
        page_setup.RightMargin = 0
        page_setup.HeaderDistance = 0
        page_setup.FooterDistance = 0
        page_setup.PageWidth = max(72.0, target_width + pad_pt * 2)
        page_setup.PageHeight = max(72.0, target_height + pad_pt * 2)
        temp_doc.ExportAsFixedFormat(str(pdf_path), WD_FORMAT_PDF)
        if not pdf_path.exists() or pdf_path.stat().st_size < 100:
            return False, "Word fallback: δεν δημιουργήθηκε έγκυρο προσωρινό PDF."
        best_image = None
        best_bbox = None
        best_area = -1
        with fitz.open(pdf_path) as pdf:
            if pdf.page_count < 1:
                return False, "Word fallback: το προσωρινό PDF δεν έχει σελίδα."
            # Word can occasionally push a large inline picture to page 2.
            # Render every temporary page and keep the one with the largest
            # non-white content area.
            for page_index in range(pdf.page_count):
                page = pdf[page_index]
                pix = page.get_pixmap(matrix=fitz.Matrix(4.0, 4.0), alpha=False)
                candidate_png = temp_dir / f"shape_raw_{page_index:02d}.png"
                pix.save(candidate_png)
                candidate = Image.open(candidate_png).convert("RGBA")
                candidate_rgb = candidate.convert("RGB")
                candidate_white = Image.new("RGB", candidate_rgb.size, "white")
                candidate_diff = ImageChops.difference(candidate_rgb, candidate_white).convert("L")
                candidate_mask = candidate_diff.point(lambda value: 255 if value > 6 else 0)
                candidate_bbox = candidate_mask.getbbox()
                area = 0 if not candidate_bbox else (candidate_bbox[2] - candidate_bbox[0]) * (candidate_bbox[3] - candidate_bbox[1])
                if area > best_area:
                    best_area = area
                    best_image = candidate.copy()
                    best_bbox = candidate_bbox
        if best_image is None:
            return False, "Word fallback: δεν αποδόθηκε εικόνα από το προσωρινό PDF."
        image = best_image
        bbox = best_bbox
        if bbox:
            margin = 4
            left = max(0, bbox[0] - margin)
            top = max(0, bbox[1] - margin)
            right = min(image.width, bbox[2] + margin)
            bottom = min(image.height, bbox[3] + margin)
            image = image.crop((left, top, right, bottom))
        target_px_w = max(32, int(round(target_width * 4.0)))
        target_px_h = max(32, int(round(target_height * 4.0)))
        image = image.resize((target_px_w, target_px_h), Image.Resampling.LANCZOS)
        image.save(png_path, format="PNG")
        return bool(png_path.exists() and png_path.stat().st_size > 100), ""
    except Exception as exc:
        return False, f"Word PDF fallback: {type(exc).__name__}: {exc}"
    finally:
        if temp_doc is not None:
            try:
                temp_doc.Close(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
            except Exception:
                pass
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _make_border_background_transparent(png_path: Path) -> dict[str, Any]:
    """Remove only border-connected synthetic near-white canvas pixels.

    Word/PowerPoint can rasterize a no-fill compound on an opaque white page.
    This pass keeps enclosed white artwork intact and clears only the background
    reachable from the image border. It therefore supports later overlays
    without a rectangular white card around the compound.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore

        image = Image.open(png_path).convert("RGBA")
        if image.width < 2 or image.height < 2:
            return {"applied": False, "reason": "image-too-small"}
        probe = image.copy()
        sentinel = (1, 2, 3, 4)
        seeds: list[tuple[int, int]] = []
        step_x = max(1, image.width // 32)
        step_y = max(1, image.height // 32)
        for x in range(0, image.width, step_x):
            seeds.extend(((x, 0), (x, image.height - 1)))
        for y in range(0, image.height, step_y):
            seeds.extend(((0, y), (image.width - 1, y)))
        seeds.extend(((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1)))
        used = 0
        for seed in seeds:
            r, g, b, a = probe.getpixel(seed)
            if a <= 8 or (r >= 232 and g >= 232 and b >= 232 and max(r, g, b) - min(r, g, b) <= 18):
                try:
                    ImageDraw.floodfill(probe, seed, sentinel, thresh=26)
                    used += 1
                except Exception:
                    pass
        from PIL import ImageChops, ImageOps  # type: ignore

        sentinel_image = Image.new("RGBA", image.size, sentinel)
        sentinel_diff = ImageChops.difference(probe, sentinel_image).convert("L")
        marker_mask = sentinel_diff.point(lambda value: 255 if value == 0 else 0)
        cleared = int(marker_mask.histogram()[255])
        # Soft alpha near antialiased dark edges; pure white becomes fully transparent.
        darkness = ImageOps.invert(image.convert("L"))
        soft_alpha = darkness.point(lambda value: 0 if value <= 5 else min(255, value * 10))
        original_alpha = image.getchannel("A")
        already_transparent = original_alpha.point(lambda value: 255 if value <= 8 else 0)
        candidate_alpha = Image.composite(Image.new("L", image.size, 0), soft_alpha, already_transparent)
        image.putalpha(Image.composite(candidate_alpha, original_alpha, marker_mask))
        image.save(png_path, format="PNG", optimize=True)
        return {
            "applied": bool(cleared),
            "clearedPixels": int(cleared),
            "pixelCount": int(image.width * image.height),
            "clearedRatio": float(cleared / max(1, image.width * image.height)),
            "seedCount": int(used),
        }
    except Exception as exc:
        return {"applied": False, "reason": f"{type(exc).__name__}: {exc}"}


def _overlay_guard_rectangles(overlays: list[dict[str, Any]], width: int, height: int) -> list[tuple[int, int, int, int]]:
    rectangles: list[tuple[int, int, int, int]] = []
    pad_x = max(5, int(round(width * 0.018)))
    pad_y = max(5, int(round(height * 0.025)))
    for overlay in overlays:
        geometry = overlay.get("geometry") or {}
        try:
            x = float(geometry.get("x") or 0.0)
            y = float(geometry.get("y") or 0.0)
            w = float(geometry.get("width") or 0.0)
            h = float(geometry.get("height") or 0.0)
        except Exception:
            continue
        if w <= 0 or h <= 0:
            continue
        left = max(0, int(round(x * width)) - pad_x)
        top = max(0, int(round(y * height)) - pad_y)
        right = min(width, int(round((x + w) * width)) + pad_x)
        bottom = min(height, int(round((y + h) * height)) + pad_y)
        if right > left and bottom > top:
            rectangles.append((left, top, right, bottom))
    return rectangles


def _visual_guard_clean_background(full_path: Path, clean_path: Path, overlays: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept a cleaned background only when differences stay inside equation zones."""
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageOps  # type: ignore

        full = Image.open(full_path).convert("RGBA")
        clean = Image.open(clean_path).convert("RGBA")
        if clean.size != full.size:
            clean = clean.resize(full.size, Image.Resampling.LANCZOS)
            clean.save(clean_path, format="PNG", optimize=True)
        width, height = full.size
        rectangles = _overlay_guard_rectangles(overlays, width, height)
        if not rectangles:
            return {"accepted": False, "reason": "no-overlay-geometry"}
        allowed = Image.new("L", full.size, 0)
        draw = ImageDraw.Draw(allowed)
        for rect in rectangles:
            draw.rectangle(rect, fill=255)
        outside = ImageOps.invert(allowed)
        diff = ImageChops.difference(full, clean)
        rgb_diff = diff.convert("RGB")
        channels = rgb_diff.split()
        magnitude = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        magnitude = ImageChops.lighter(magnitude, diff.getchannel("A"))
        changed = magnitude.point(lambda value: 255 if value > 30 else 0)
        inside_changed = ImageChops.multiply(changed, allowed)
        outside_changed = ImageChops.multiply(changed, outside)
        inside_pixels = max(1, sum(allowed.histogram()[128:]))
        outside_pixels = max(1, sum(outside.histogram()[128:]))
        inside_count = sum(inside_changed.histogram()[128:])
        outside_count = sum(outside_changed.histogram()[128:])
        full_alpha = full.getchannel("A")
        clean_alpha = clean.getchannel("A")
        full_content = max(1, sum(full_alpha.histogram()[8:]))
        clean_content = sum(clean_alpha.histogram()[8:])
        outside_ratio = outside_count / outside_pixels
        inside_ratio = inside_count / inside_pixels
        content_ratio = clean_content / full_content
        accepted = bool(outside_ratio <= 0.012 and inside_ratio >= 0.001 and content_ratio >= 0.70)
        return {
            "accepted": accepted,
            "outsideChangedRatio": float(outside_ratio),
            "insideChangedRatio": float(inside_ratio),
            "contentRatio": float(content_ratio),
            "guardRectangles": len(rectangles),
            "reason": "accepted" if accepted else "difference-outside-equation-zones-or-no-equation-removal",
        }
    except Exception as exc:
        return {"accepted": False, "reason": f"{type(exc).__name__}: {exc}"}


def _export_shape_png(win32_client, word, shape, png_path: Path, width: float, height: float) -> str:
    _copy_shape_as_picture(word, shape)
    if _clipboard_to_png_with_powerpoint(win32_client, png_path, width, height):
        return "powerpoint-emf-to-png-4x-transparent"
    ok, word_pdf_error = _clipboard_to_png_with_word_pdf(word, png_path, width, height)
    if ok:
        return "word-pdf-pymupdf-to-png-4x-transparent"
    if _clipboard_to_png_with_pillow(png_path):
        return "windows-clipboard-bitmap-to-png"
    raise RuntimeError(
        "Δεν ήταν δυνατή η εξαγωγή του σύνθετου Word αντικειμένου σε PNG. "
        "Απέτυχαν PowerPoint, Word→PDF και clipboard bitmap. " + (word_pdf_error or "")
    )



RPC_FAILURE_HRESULTS = {
    -2147023174,  # 0x800706BA RPC server unavailable
    -2147417848,  # 0x80010108 object disconnected from clients
    -2147418111,  # 0x80010001 call rejected by callee
    -2147023170,  # 0x800706BE remote procedure call failed
}


def _exception_hresult(exc: BaseException) -> int | None:
    value = getattr(exc, "hresult", None)
    if isinstance(value, int):
        return value
    args = getattr(exc, "args", ()) or ()
    if args and isinstance(args[0], int):
        return int(args[0])
    return None


def _is_rpc_failure(exc: BaseException) -> bool:
    code = _exception_hresult(exc)
    if code in RPC_FAILURE_HRESULTS:
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "rpc server is unavailable",
        "rpc server unavailable",
        "remote procedure call failed",
        "server threw an exception",
        "object invoked has disconnected",
        "call was rejected by callee",
        "ο διακομιστής rpc δεν είναι διαθέσιμος",
        "η κλήση απομακρυσμένης διαδικασίας απέτυχε",
    )
    return any(token in text for token in needles)


def _configure_word(word) -> None:
    word.Visible = False
    word.DisplayAlerts = 0
    try:
        word.ScreenUpdating = False
    except Exception:
        pass
    try:
        word.Options.SaveNormalPrompt = False
    except Exception:
        pass


def _close_word_session(word, doc=None) -> None:
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
    # Release late-bound COM proxies before the next isolated Word process.
    doc = None
    word = None
    gc.collect()
    time.sleep(0.20)


def _open_word_session(win32_client, document_path: Path, read_only: bool):
    word = win32_client.DispatchEx("Word.Application")
    _configure_word(word)
    try:
        doc = word.Documents.Open(
            str(document_path),
            ConfirmConversions=False,
            ReadOnly=bool(read_only),
            AddToRecentFiles=False,
            Visible=False,
            OpenAndRepair=False,
        )
    except Exception:
        try:
            word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
        except Exception:
            pass
        raise
    return word, doc


def _candidate_match_score(shape, candidate: CompositeCandidate) -> float:
    reasons = _shape_reasons(shape)
    if not reasons:
        return float("inf")
    score = 0.0
    current_name = str(_safe(lambda: shape.Name, "") or "")
    current_type = int(_safe(lambda: shape.Type, -1) or -1)
    current_anchor = int(_safe(lambda: shape.Anchor.Start, 0) or 0)
    current_width = float(_safe(lambda: shape.Width, 0.0) or 0.0)
    current_height = float(_safe(lambda: shape.Height, 0.0) or 0.0)
    if current_name == candidate.name:
        score -= 4.0
    else:
        score += 1.5
    if current_type == candidate.shape_type:
        score -= 1.5
    else:
        score += 1.0
    score += min(4.0, abs(current_anchor - candidate.anchor_start) / 250.0)
    if candidate.width > 0 and current_width > 0:
        score += abs(current_width - candidate.width) / max(current_width, candidate.width)
    if candidate.height > 0 and current_height > 0:
        score += abs(current_height - candidate.height) / max(current_height, candidate.height)
    overlap = len(set(reasons) & set(candidate.reasons))
    score -= min(2.0, overlap * 0.5)
    return score


def _locate_candidate_shape(doc, candidate: CompositeCandidate):
    count = int(_safe(lambda: doc.Shapes.Count, 0) or 0)
    if 1 <= candidate.index <= count:
        try:
            direct = doc.Shapes(candidate.index)
            if _candidate_match_score(direct, candidate) <= 1.5:
                return direct, candidate.index
        except Exception:
            pass
    best_shape = None
    best_index = -1
    best_score = float("inf")
    for index in range(1, count + 1):
        try:
            shape = doc.Shapes(index)
            score = _candidate_match_score(shape, candidate)
        except Exception:
            continue
        if score < best_score:
            best_shape = shape
            best_index = index
            best_score = score
    if best_shape is None or best_score > 5.0:
        raise RuntimeError(
            f"Δεν εντοπίστηκε ξανά το Word αντικείμενο {candidate.index} ({candidate.name}) "
            f"μετά την επανεκκίνηση της απομονωμένης συνεδρίας."
        )
    return best_shape, best_index


def _export_candidate_once(
    win32_client,
    source_path: Path,
    candidate: CompositeCandidate,
    overlays: list[dict[str, Any]],
    png_path: Path,
    request_clean_background: bool,
) -> dict[str, Any]:
    """Export the untouched Word compound as the immutable visual base.

    HF4 deliberately ignores ``request_clean_background``.  Equation recovery
    must never rewrite, transparentize or visually simplify the base asset.
    Editable equations are layered by BookWriter over this unchanged image and
    locally mask only their own rectangles.
    """
    word = None
    doc = None
    try:
        word, doc = _open_word_session(win32_client, source_path, read_only=True)
        shape, current_index = _locate_candidate_shape(doc, candidate)
        geometry = _capture_geometry(shape)
        try:
            png_path.unlink(missing_ok=True)
        except Exception:
            pass
        backend = _export_shape_png(
            win32_client,
            word,
            shape,
            png_path,
            geometry["width"],
            geometry["height"],
        )
        if not png_path.exists() or png_path.stat().st_size <= 100:
            raise RuntimeError("Η απομονωμένη εξαγωγή δεν παρήγαγε έγκυρο PNG.")
        return {
            "backend": backend,
            "backgroundClean": False,
            "clearedEquationCount": 0,
            "locatedIndex": int(current_index),
            "pngBytes": int(png_path.stat().st_size),
            "transparentBackground": False,
            "transparency": {"applied": False, "reason": "hf4-immutable-base"},
            "immutableBase": True,
            "basePixelPolicy": "untouched-word-render",
        }
    finally:
        _close_word_session(word, doc)


def _export_candidate_isolated(
    win32_client,
    source_path: Path,
    candidate: CompositeCandidate,
    overlays: list[dict[str, Any]],
    png_path: Path,
    max_attempts_per_plan: int = 2,
) -> dict[str, Any]:
    """Export one untouched base in an isolated WINWORD.EXE session.

    The HF3 clean-background branch is intentionally retired.  There is one
    execution path only: faithful full compound -> immutable PNG base.  RPC
    retries remain isolated per object.
    """
    errors: list[str] = []
    rpc_restarts = 0
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts_per_plan + 1):
        try:
            artifact = _export_candidate_once(
                win32_client,
                source_path,
                candidate,
                overlays,
                png_path,
                request_clean_background=False,
            )
            artifact.update({
                "exportPlan": "immutable-faithful-full-background",
                "exportAttempts": attempt,
                "rpcRestarts": rpc_restarts,
                "attemptErrors": list(errors),
                "visualGuard": {"accepted": True, "reason": "not-required-immutable-base"},
            })
            return artifact
        except Exception as exc:
            last_exc = exc
            rpc = _is_rpc_failure(exc)
            if rpc:
                rpc_restarts += 1
            errors.append(
                f"immutable-full attempt {attempt}/{max_attempts_per_plan}: "
                f"{type(exc).__name__}: {exc}"
            )
            time.sleep(0.35 if rpc else 0.20)
    assert last_exc is not None
    joined = " | ".join(errors[-6:])
    raise RuntimeError(
        f"Απέτυχε η απομονωμένη εξαγωγή του Word αντικειμένου "
        f"{candidate.index} ({candidate.name}). {joined}"
    )


def _replacement_marker(candidate: CompositeCandidate, composite: dict[str, Any] | None) -> str:
    composite_id = str((composite or {}).get("id") or "")
    if composite_id:
        return f'"compositeId":"{composite_id}"'
    return f"BookWriter composite candidate={candidate.index}"


def _find_existing_replacement(doc, candidate: CompositeCandidate, composite: dict[str, Any] | None):
    marker = _replacement_marker(candidate, composite)
    count = int(_safe(lambda: doc.Shapes.Count, 0) or 0)
    for index in range(1, count + 1):
        try:
            shape = doc.Shapes(index)
            alt = str(_safe(lambda: shape.AlternativeText, "") or "")
            if marker in alt:
                return shape, index
        except Exception:
            continue
    return None, -1

def _add_png_shape(doc, png_path: Path, anchor_start: int):
    rng = doc.Range(Start=anchor_start, End=anchor_start)
    rng.Collapse(WD_COLLAPSE_START)
    return doc.Shapes.AddPicture(FileName=str(png_path), LinkToFile=False, SaveWithDocument=True, Anchor=rng)




def _candidate_group_matches(candidates: list[CompositeCandidate], overlay_manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Match Word COM candidates to OOXML groups by order and rendered size."""
    groups = list((overlay_manifest.get("composites") or {}).values())
    if not groups or not candidates:
        return {}
    remaining = set(range(len(groups)))
    matches: dict[int, dict[str, Any]] = {}
    group_candidates = [candidate for candidate in candidates if candidate.shape_type in COMPLEX_TYPES or any(reason.startswith("group-items:") for reason in candidate.reasons)]
    for order, candidate in enumerate(group_candidates):
        best_index = None
        best_score = float("inf")
        for group_index in remaining:
            group = groups[group_index]
            width = float(group.get("widthPt") or 0.0)
            height = float(group.get("heightPt") or 0.0)
            if width <= 0 or height <= 0 or candidate.width <= 0 or candidate.height <= 0:
                size_score = 10.0
            else:
                size_score = abs(candidate.width - width) / max(candidate.width, width)
                size_score += abs(candidate.height - height) / max(candidate.height, height)
            order_score = abs(order - group_index) * 0.015
            score = size_score + order_score
            if score < best_score:
                best_score = score
                best_index = group_index
        if best_index is None:
            continue
        # A generous threshold allows Word's point rounding while preventing a
        # table-in-textbox candidate from consuming an unrelated group record.
        if best_score <= 0.40 or (len(group_candidates) == len(groups) and best_index == order):
            group = groups[best_index]
            matches[candidate.index] = group
            remaining.remove(best_index)
    return matches


def _clear_equation_textboxes(shape) -> int:
    """Clear only equation-bearing text boxes in a duplicate Word shape."""
    cleared = 0
    group_count = int(_safe(lambda: shape.GroupItems.Count, 0) or 0)
    if group_count:
        for index in range(1, group_count + 1):
            cleared += _clear_equation_textboxes(shape.GroupItems(index))
        return cleared
    text_frame = _safe(lambda: shape.TextFrame, None)
    if text_frame is None or not bool(_safe(lambda: text_frame.HasText, False)):
        return 0
    text_range = _safe(lambda: text_frame.TextRange, None)
    if text_range is None:
        return 0
    math_count = int(_safe(lambda: text_range.OMaths.Count, 0) or 0)
    if math_count <= 0:
        return 0
    try:
        text_range.Text = ""
        return math_count
    except Exception:
        return 0


def _duplicate_clean_background(shape):
    """Duplicate a group, remove equation text, and return the export shape."""
    duplicate_range = shape.Duplicate()
    duplicate = _safe(lambda: duplicate_range.Item(1), None) or duplicate_range
    for prop in ("Left", "Top", "Width", "Height"):
        value = _safe(lambda p=prop: getattr(shape, p), None)
        if value is not None:
            try:
                setattr(duplicate, prop, value)
            except Exception:
                pass
    cleared = _clear_equation_textboxes(duplicate)
    return duplicate, cleared


def _composite_alt_text(composite_id: str, background_clean: bool) -> str:
    payload = {
        "role": "composite_figure",
        "compositeId": composite_id,
        "backgroundClean": False,
        "immutableBase": True,
        "basePixelPolicy": "untouched-word-render",
    }
    return "BW_IMPORT " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _next_relationship_id(root: etree._Element) -> str:
    used: set[int] = set()
    for rel in root.findall(f"{{{REL}}}Relationship"):
        value = str(rel.get("Id") or "")
        if value.startswith("rId"):
            try:
                used.add(int(value[3:]))
            except ValueError:
                pass
    candidate = max(used or {0}) + 1
    while candidate in used:
        candidate += 1
    return f"rId{candidate}"


def _picture_payload(rel_id: str, name: str, cx: str, cy: str) -> etree._Element:
    pic = etree.Element(f"{{{PIC}}}pic", nsmap={"pic": PIC})
    nv = etree.SubElement(pic, f"{{{PIC}}}nvPicPr")
    etree.SubElement(nv, f"{{{PIC}}}cNvPr", id="0", name=name)
    cnv = etree.SubElement(nv, f"{{{PIC}}}cNvPicPr")
    etree.SubElement(cnv, f"{{{A}}}picLocks", noChangeAspect="1", noChangeArrowheads="1")
    fill = etree.SubElement(pic, f"{{{PIC}}}blipFill")
    blip = etree.SubElement(fill, f"{{{A}}}blip", cstate="print")
    blip.set(f"{{{R}}}embed", rel_id)
    stretch = etree.SubElement(fill, f"{{{A}}}stretch")
    etree.SubElement(stretch, f"{{{A}}}fillRect")
    sp_pr = etree.SubElement(pic, f"{{{PIC}}}spPr", bwMode="auto")
    xfrm = etree.SubElement(sp_pr, f"{{{A}}}xfrm")
    etree.SubElement(xfrm, f"{{{A}}}off", x="0", y="0")
    etree.SubElement(xfrm, f"{{{A}}}ext", cx=str(cx or "0"), cy=str(cy or "0"))
    geom = etree.SubElement(sp_pr, f"{{{A}}}prstGeom", prst="rect")
    etree.SubElement(geom, f"{{{A}}}avLst")
    return pic


def _replace_group_backgrounds_ooxml(
    source_path: Path,
    output_path: Path,
    replacements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace matched Word DrawingML groups with embedded PNG pictures.

    Microsoft Word is used only as the renderer.  The package mutation is done
    directly in OOXML, so inserted pictures cannot be lost because Word's
    Shapes collection is re-indexed while replacements are being added.
    """
    source_path = Path(source_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(source_path, "r") as source:
        items = source.infolist()
        data = {item.filename: source.read(item.filename) for item in items}

    document_name = "word/document.xml"
    rels_name = "word/_rels/document.xml.rels"
    content_types_name = "[Content_Types].xml"
    root = etree.fromstring(data[document_name])
    rels_root = etree.fromstring(data[rels_name])
    content_root = etree.fromstring(data[content_types_name])
    groups = root.xpath(".//wpg:wgp", namespaces=NS)
    existing_names = set(data)
    embedded: list[dict[str, Any]] = []

    for replacement in sorted(replacements, key=lambda row: int(row.get("sourceGroupIndex") or 0)):
        source_index = int(replacement.get("sourceGroupIndex") or 0)
        if source_index < 1 or source_index > len(groups):
            raise RuntimeError(
                f"Το OOXML group {source_index} δεν υπάρχει στο {source_path.name} "
                f"(διαθέσιμα: {len(groups)})."
            )
        group = groups[source_index - 1]
        drawing_nodes = group.xpath("ancestor::w:drawing[1]", namespaces=NS)
        if not drawing_nodes:
            raise RuntimeError(f"Το OOXML group {source_index} δεν βρίσκεται μέσα σε w:drawing.")
        drawing = drawing_nodes[0]
        host_nodes = drawing.xpath("./wp:anchor[1] | ./wp:inline[1]", namespaces=NS)
        if not host_nodes:
            raise RuntimeError(f"Το OOXML group {source_index} δεν διαθέτει wp:anchor/wp:inline.")
        host = host_nodes[0]
        extent = host.find(f"{{{WP}}}extent")
        cx = str(extent.get("cx") if extent is not None else "0")
        cy = str(extent.get("cy") if extent is not None else "0")
        graphic_data_nodes = drawing.xpath(".//a:graphicData[1]", namespaces=NS)
        if not graphic_data_nodes:
            raise RuntimeError(f"Το OOXML group {source_index} δεν διαθέτει a:graphicData.")
        graphic_data = graphic_data_nodes[0]

        rel_id = _next_relationship_id(rels_root)
        base_name = f"bookwriter_composite_{source_index:04d}.png"
        media_name = f"word/media/{base_name}"
        suffix = 2
        while media_name in existing_names:
            base_name = f"bookwriter_composite_{source_index:04d}_{suffix}.png"
            media_name = f"word/media/{base_name}"
            suffix += 1
        existing_names.add(media_name)
        png_path = Path(str(replacement.get("pngPath") or ""))
        if not png_path.exists() or png_path.stat().st_size <= 100:
            raise RuntimeError(f"Λείπει το PNG για το OOXML group {source_index}: {png_path}")
        data[media_name] = png_path.read_bytes()
        etree.SubElement(
            rels_root,
            f"{{{REL}}}Relationship",
            Id=rel_id,
            Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
            Target=f"media/{base_name}",
        )

        composite_id = str(replacement.get("compositeId") or f"bw-composite-{source_index:04d}")
        background_clean = bool(replacement.get("backgroundClean"))
        alt_text = _composite_alt_text(composite_id, background_clean)
        doc_pr_nodes = host.xpath("./wp:docPr[1]", namespaces=NS)
        if doc_pr_nodes:
            doc_pr = doc_pr_nodes[0]
            doc_pr.set("descr", alt_text)
            doc_pr.set("title", "BookWriter hybrid composite")
            doc_pr.set("name", str(doc_pr.get("name") or f"BookWriter composite {source_index}"))
        graphic_data.set("uri", PIC)
        for child in list(graphic_data):
            graphic_data.remove(child)
        graphic_data.append(_picture_payload(rel_id, f"BookWriter composite {source_index}", cx, cy))

        # The modern group normally sits in mc:Choice with a VML fallback.
        # Keeping the fallback makes the complexity detector see the old group
        # again and can make Word select stale content.  Replace the complete
        # AlternateContent node with the already-patched drawing.
        alternate_nodes = drawing.xpath("ancestor::mc:AlternateContent[1]", namespaces=NS)
        if alternate_nodes:
            alternate = alternate_nodes[0]
            parent = alternate.getparent()
            if parent is None:
                raise RuntimeError(f"Το AlternateContent του group {source_index} δεν έχει γονέα.")
            parent.replace(alternate, deepcopy(drawing))

        embedded.append(
            {
                "sourceGroupIndex": source_index,
                "compositeId": composite_id,
                "relationshipId": rel_id,
                "mediaPath": media_name,
                "pngBytes": len(data[media_name]),
                "backgroundClean": background_clean,
            }
        )

    has_png = any(
        child.get("Extension", "").lower() == "png"
        for child in content_root.findall(f"{{{CT}}}Default")
    )
    if not has_png:
        etree.SubElement(content_root, f"{{{CT}}}Default", Extension="png", ContentType="image/png")
    data[document_name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    data[rels_name] = etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    data[content_types_name] = etree.tostring(content_root, xml_declaration=True, encoding="UTF-8", standalone="yes")

    temp = output_path.with_suffix(output_path.suffix + ".ooxml.tmp")
    with ZipFile(temp, "w", ZIP_DEFLATED) as target:
        written = set()
        for item in items:
            target.writestr(item, data[item.filename])
            written.add(item.filename)
        for name, payload in data.items():
            if name not in written:
                target.writestr(name, payload)
    temp.replace(output_path)
    return {"replacementMode": "direct-ooxml-picture", "embedded": embedded}


def _audit_embedded_composite_backgrounds(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    with ZipFile(path, "r") as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
        rels_root = etree.fromstring(archive.read("word/_rels/document.xml.rels"))
        names = set(archive.namelist())
    rels = {
        str(rel.get("Id") or ""): str(rel.get("Target") or "")
        for rel in rels_root.findall(f"{{{REL}}}Relationship")
    }
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for doc_pr in root.xpath(".//wp:docPr[contains(@descr, 'BW_IMPORT') and contains(@descr, 'composite_figure')]", namespaces=NS):
        descr = str(doc_pr.get("descr") or "")
        composite_id = ""
        match = descr.partition("BW_IMPORT ")[2]
        if match:
            try:
                composite_id = str(json.loads(match).get("compositeId") or "")
            except Exception:
                composite_id = ""
        drawing_nodes = doc_pr.xpath("ancestor::w:drawing[1]", namespaces=NS)
        embed = ""
        if drawing_nodes:
            blips = drawing_nodes[0].xpath(".//a:blip/@r:embed", namespaces=NS)
            embed = str(blips[0]) if blips else ""
        target = rels.get(embed, "")
        package_path = "word/" + target.lstrip("/") if target else ""
        ok = bool(embed and target and package_path in names)
        row = {"compositeId": composite_id, "relationshipId": embed, "target": target, "packagePath": package_path, "ok": ok}
        rows.append(row)
        if not ok:
            missing.append(composite_id or embed or "unknown")
    return {
        "embeddedCompositeBackgroundCount": len(rows),
        "validEmbeddedCompositeBackgroundCount": sum(1 for row in rows if row["ok"]),
        "missingCompositeBackgrounds": missing,
        "rows": rows,
    }


def rasterize_complex_objects(input_path: Path, output_path: Path, report_path: Path | None = None) -> dict[str, Any]:
    """Replace complex body Shapes with faithful pictures and editable equation overlays.

    4.8.7d uses a two-phase transaction:
      1. each complex object is rendered in a fresh isolated WINWORD.EXE;
      2. only after every PNG exists, a separate replacement session mutates a
         temporary DOCX, saving a checkpoint after every successful object.

    A crashed RPC server can therefore neither invalidate the remaining exports
    nor destroy the last successful DOCX checkpoint.
    """
    input_path = Path(input_path).resolve()
    output_path = Path(output_path).resolve()
    static = inspect_docx_complexity(input_path)
    result: dict[str, Any] = {
        "version": "0.4.7e-hf3-transparent-composites",
        "input": str(input_path),
        "output": str(output_path),
        "platform": platform.platform(),
        "staticInventory": static,
        "wordComAvailable": False,
        "transactionMode": "isolated-export-per-object + direct-ooxml-package-replacement",
        "candidates": [],
        "results": [],
        "rasterizedCount": 0,
        "failedCount": 0,
        "equationOverlayCount": 0,
        "compositesWithEditableEquations": 0,
        "cleanBackgroundCount": 0,
        "exportSessionCount": 0,
        "exportRpcRestarts": 0,
        "replacementSessionRestarts": 0,
        "notDone": [
            "Complex objects in headers, footers and non-main stories are not converted in this checkpoint.",
            "Non-equation shapes inside a rasterized compound remain part of the transparent PNG background.",
            "Equation objects are preserved as editable objects. A cleaned background is used only after the visual guard verifies that changes are confined to equation zones.",
            "Word tight/through contour polygons are approximated by rectangular browser flow after import.",
        ],
    }
    if os.name != "nt":
        raise RuntimeError("Η ζητούμενη διέλευση Word COM απαιτεί Microsoft Word σε Windows.")
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        raise RuntimeError("Δεν είναι διαθέσιμο το pywin32. Τρέξε ξανά το 01_SETUP_FIRST_TIME.cmd.") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pythoncom.CoInitialize()
    scratch_dir: Path | None = None
    try:
        # Inventory is taken in a short disposable Word session.  No source
        # document remains open while the isolated export workers run.
        inventory_word = None
        inventory_doc = None
        try:
            inventory_word, inventory_doc = _open_word_session(win32com.client, input_path, read_only=True)
            result["wordComAvailable"] = True
            candidates: list[CompositeCandidate] = []
            for index in range(1, int(inventory_doc.Shapes.Count) + 1):
                cand = _candidate(inventory_doc.Shapes(index), index)
                if cand:
                    candidates.append(cand)
        finally:
            _close_word_session(inventory_word, inventory_doc)
        result["candidates"] = [asdict(c) for c in candidates]

        overlay_manifest = extract_composite_overlay_manifest(input_path)
        candidate_groups = _candidate_group_matches(candidates, overlay_manifest)
        result["overlayInventory"] = {
            "sourceGroups": overlay_manifest.get("groupCount", 0),
            "equationOverlays": overlay_manifest.get("equationOverlayCount", 0),
            "matchedCandidates": len(candidate_groups),
        }

        scratch_dir = Path(tempfile.mkdtemp(prefix="bookwriter_rpc_isolated_composites_"))
        image_dir = scratch_dir / "png"
        image_dir.mkdir(parents=True, exist_ok=True)
        artifacts: dict[int, dict[str, Any]] = {}

        # PHASE 1 — immutable source, one fresh Word process per object/attempt.
        for cand in candidates:
            composite = candidate_groups.get(cand.index)
            overlays = list((composite or {}).get("overlays") or [])
            png_path = image_dir / f"composite_{cand.index:04d}.png"
            try:
                artifact = _export_candidate_isolated(
                    win32com.client,
                    input_path,
                    cand,
                    overlays,
                    png_path,
                )
                artifact["pngPath"] = str(png_path)
                artifacts[cand.index] = artifact
                result["exportSessionCount"] += int(artifact.get("exportAttempts") or 1)
                result["exportRpcRestarts"] += int(artifact.get("rpcRestarts") or 0)
            except Exception as exc:
                row = asdict(CompositeResult(cand.index, cand.name, "failed-export", cand.reasons, str(exc)))
                row["rpcFailure"] = _is_rpc_failure(exc)
                result["results"].append(row)
                result["failedCount"] += 1

        # PHASE 2 — deterministic OOXML package replacement.
        #
        # Export failures no longer cancel equation recovery.  Successful
        # Word-rendered backgrounds are embedded directly in the DOCX package;
        # groups without a background remain in the source and their equations
        # are exposed by the browser importer as equation-only salvage items.
        replacements: list[dict[str, Any]] = []
        successful_candidate_indexes: set[int] = set()
        for cand in candidates:
            artifact = artifacts.get(cand.index)
            if not artifact:
                continue
            composite = candidate_groups.get(cand.index)
            source_group_index = int((composite or {}).get("sourceGroupIndex") or 0)
            if not composite or source_group_index <= 0:
                row = asdict(CompositeResult(
                    cand.index,
                    cand.name,
                    "background-exported-but-unmatched",
                    cand.reasons,
                    "Το αντικείμενο δεν αντιστοιχίστηκε σε DrawingML group για ασφαλή OOXML αντικατάσταση.",
                ))
                row.update({
                    "renderBackend": artifact.get("backend"),
                    "exportPlan": artifact.get("exportPlan"),
                    "exportAttempts": artifact.get("exportAttempts"),
                    "rpcRestarts": artifact.get("rpcRestarts"),
                    "pngBytes": artifact.get("pngBytes"),
                })
                result["results"].append(row)
                result["failedCount"] += 1
                continue
            overlays = list(composite.get("overlays") or [])
            background_clean = False
            composite["backgroundClean"] = False
            composite["transparentBackground"] = False
            composite["immutableBase"] = True
            composite["basePixelPolicy"] = "untouched-word-render"
            composite["status"] = (
                "immutable-faithful-background-with-editable-equations"
                if overlays
                else "immutable-faithful-background"
            )
            replacements.append({
                "sourceGroupIndex": source_group_index,
                "compositeId": str(composite.get("id") or ""),
                "pngPath": str(artifact.get("pngPath") or ""),
                "backgroundClean": False,
                "immutableBase": True,
                "basePixelPolicy": "untouched-word-render",
                "candidateIndex": cand.index,
            })
            successful_candidate_indexes.add(cand.index)

        working_path = scratch_dir / "working.docx"
        if replacements:
            package_replacement = _replace_group_backgrounds_ooxml(input_path, working_path, replacements)
        else:
            shutil.copy2(input_path, working_path)
            package_replacement = {"replacementMode": "none", "embedded": []}
        add_manifest_to_docx(working_path, overlay_manifest)
        background_audit = _audit_embedded_composite_backgrounds(working_path)
        result["packageReplacement"] = package_replacement
        result["backgroundAudit"] = background_audit

        embedded_by_composite = {
            str(row.get("compositeId") or ""): row
            for row in package_replacement.get("embedded", [])
        }
        for cand in candidates:
            if cand.index not in successful_candidate_indexes:
                continue
            composite = candidate_groups.get(cand.index) or {}
            overlays = list(composite.get("overlays") or [])
            artifact = artifacts[cand.index]
            composite_id = str(composite.get("id") or "")
            embedded = embedded_by_composite.get(composite_id, {})
            row = asdict(CompositeResult(cand.index, cand.name, "rasterized-png-ooxml", cand.reasons))
            row.update({
                "renderBackend": artifact.get("backend"),
                "exportPlan": artifact.get("exportPlan"),
                "exportAttempts": artifact.get("exportAttempts"),
                "rpcRestarts": artifact.get("rpcRestarts"),
                "attemptErrors": artifact.get("attemptErrors"),
                "pngBytes": artifact.get("pngBytes"),
                "compositeId": composite_id,
                "sourceGroupIndex": int(composite.get("sourceGroupIndex") or 0),
                "equationOverlays": len(overlays),
                "backgroundClean": False,
                "transparentBackground": False,
                "immutableBase": True,
                "basePixelPolicy": "untouched-word-render",
                "transparency": artifact.get("transparency"),
                "visualGuard": artifact.get("visualGuard"),
                "mediaPath": embedded.get("mediaPath", ""),
                "replacementMode": "direct-ooxml-picture",
            })
            result["results"].append(row)
            result["rasterizedCount"] += 1
            if bool(artifact.get("backgroundClean")):
                result["cleanBackgroundCount"] += 1
            if overlays:
                result["equationOverlayCount"] += len(overlays)
                result["compositesWithEditableEquations"] += 1

        all_composites = list((overlay_manifest.get("composites") or {}).values())
        attached_ids = {str(row.get("compositeId") or "") for row in package_replacement.get("embedded", [])}
        fallback_composites = [
            composite for composite in all_composites
            if str(composite.get("id") or "") not in attached_ids and composite.get("overlays")
        ]
        result["equationOnlyFallbackCompositeCount"] = len(fallback_composites)
        result["equationOnlyFallbackCount"] = sum(len(composite.get("overlays") or []) for composite in fallback_composites)
        result["backgroundAttemptedCount"] = len(candidates)
        result["backgroundEmbeddedCount"] = int(background_audit.get("validEmbeddedCompositeBackgroundCount") or 0)
        result["backgroundMissingCount"] = max(0, len(candidates) - result["backgroundEmbeddedCount"])

        shutil.copy2(working_path, output_path)
    except Exception:
        if report_path:
            try:
                Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        raise
    finally:
        if scratch_dir is not None:
            shutil.rmtree(scratch_dir, ignore_errors=True)
        try:
            pythoncom.CoFreeUnusedLibraries()
        except Exception:
            pass
        pythoncom.CoUninitialize()

    if not output_path.exists() or output_path.stat().st_size < 1024:
        raise RuntimeError("Το Word δεν παρήγαγε έγκυρο DOCX μετά τη μετατροπή σύνθετων αντικειμένων.")
    result["postInventory"] = inspect_docx_complexity(output_path)
    result["unconvertedComplexObjectsRemain"] = bool(
        result["postInventory"].get("requiresWordCompositeRasterization")
    )
    if report_path:
        Path(report_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result

def probe_word() -> dict[str, Any]:
    out = {"platform": platform.platform(), "windows": os.name == "nt", "pywin32": False, "word": False, "error": ""}
    if os.name != "nt":
        out["error"] = "Word COM is Windows-only."
        return out
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
        out["pywin32"] = True
        pythoncom.CoInitialize()
        word = win32com.client.DispatchEx("Word.Application")
        out["word"] = True
        out["version"] = str(_safe(lambda: word.Version, ""))
        word.Quit(SaveChanges=WD_DO_NOT_SAVE_CHANGES)
        pythoncom.CoUninitialize()
    except Exception as exc:
        out["error"] = str(exc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Rasterize complex Word groups to single EMF pictures using Microsoft Word.")
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--probe-word", action="store_true")
    args = parser.parse_args()
    if args.probe_word:
        print(json.dumps(probe_word(), ensure_ascii=False, indent=2))
        return 0
    if not args.input:
        parser.error("input is required")
    if args.inspect:
        print(json.dumps(inspect_docx_complexity(args.input), ensure_ascii=False, indent=2))
        return 0
    if not args.output:
        parser.error("output is required unless --inspect is used")
    report = rasterize_complex_objects(args.input, args.output, args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
