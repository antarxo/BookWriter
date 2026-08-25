from __future__ import annotations

"""Web-surrogate conversion for legacy Word vector previews.

HF24 makes the raster bitmap produced directly by Word CopyAsPicture the primary
OLE browser surrogate. PowerPoint remains a fallback only; it is no longer the
mandatory bridge between Word and PNG.

Ordinary DOCX files can contain WMF/EMF pictures that Word renders perfectly but
Chromium does not display natively.  OLE equation/chemical objects commonly use
such a vector picture as their visible preview while the actual editable OLE
payload lives separately in word/embeddings/*.bin.

HF21 keeps the original vector files, OLE payloads and *all Word relationships*
untouched. It adds high-resolution PNG surrogates plus a BookWriter-only manifest
that maps vector package parts to browser-readable previews. Word therefore keeps
rendering exactly the original WMF/EMF relationship graph, while Chromium uses the
surrogate without mutating pagination/layout after the Word page map was captured.
"""

import gc
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from .word_clipboard_capture import copy_as_picture_to_png, clipboard_enhmetafile_to_png, clear_windows_clipboard
from .word_composite_rasterizer import _clipboard_to_png_with_word_pdf

REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
PP_LAYOUT_BLANK = 12
PP_SHAPE_FORMAT_PNG = 2
PP_PASTE_ENHANCED_METAFILE = 2
VECTOR_EXTENSIONS = {".wmf", ".emf"}
SURROGATE_MANIFEST = "customXml/bookwriter-vector-surrogates.json"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
V_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def inspect_vector_previews(docx_path: Path) -> dict[str, Any]:
    docx_path = Path(docx_path)
    out: dict[str, Any] = {
        "wmfMedia": 0,
        "emfMedia": 0,
        "vectorMedia": 0,
        "oleObjects": 0,
        "equationDsmt4OleObjects": 0,
        "vectorRelationshipCount": 0,
    }
    with ZipFile(docx_path, "r") as zf:
        names = zf.namelist()
        media = [name for name in names if name.startswith("word/media/")]
        out["wmfMedia"] = sum(name.lower().endswith(".wmf") for name in media)
        out["emfMedia"] = sum(name.lower().endswith(".emf") for name in media)
        out["vectorMedia"] = out["wmfMedia"] + out["emfMedia"]
        for name in names:
            if name.startswith("word/") and name.lower().endswith(".xml"):
                try:
                    data = zf.read(name)
                except KeyError:
                    continue
                out["equationDsmt4OleObjects"] += data.count(b'ProgID="Equation.DSMT4"')
                try:
                    xml_root = etree.fromstring(data)
                    out["oleObjects"] += len(xml_root.findall(f".//{{{O_NS}}}OLEObject"))
                except Exception:
                    pass
            if name.startswith("word/") and name.lower().endswith(".rels"):
                try:
                    root = etree.fromstring(zf.read(name))
                except Exception:
                    continue
                for rel in root.findall(f"{{{REL}}}Relationship"):
                    target = str(rel.get("Target") or "")
                    if PurePosixPath(target).suffix.lower() in VECTOR_EXTENSIONS:
                        out["vectorRelationshipCount"] += 1
    return out


def _source_part_for_rels(relative_rels: PurePosixPath) -> PurePosixPath | None:
    parts = list(relative_rels.parts)
    if "_rels" not in parts:
        return None
    idx = parts.index("_rels")
    if idx + 1 >= len(parts):
        return None
    rel_name = parts[idx + 1]
    if not rel_name.endswith(".rels"):
        return None
    source_name = rel_name[:-5]
    return PurePosixPath(*parts[:idx], source_name)


def _resolve_target(relative_rels: PurePosixPath, target: str) -> PurePosixPath | None:
    source_part = _source_part_for_rels(relative_rels)
    if source_part is None:
        return None
    base = source_part.parent
    # Package relationship targets use POSIX separators.  Resolve '..' without
    # touching the host filesystem.
    stack: list[str] = []
    for part in (base / PurePosixPath(target)).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if stack:
                stack.pop()
            continue
        stack.append(part)
    return PurePosixPath(*stack)


def _surrogate_target(target: str) -> str:
    path = PurePosixPath(target)
    return str(path.with_name(path.stem + "_bw_web.png"))


def _rels_part_for_source(source_part: PurePosixPath) -> PurePosixPath:
    return source_part.parent / "_rels" / (source_part.name + ".rels")


def _relationship_target_map(root: Path, source_part: PurePosixPath) -> dict[str, PurePosixPath]:
    rels_part = _rels_part_for_source(source_part)
    rels_path = root / Path(rels_part.as_posix())
    out: dict[str, PurePosixPath] = {}
    if not rels_path.exists():
        return out
    try:
        tree = etree.parse(str(rels_path))
    except Exception:
        return out
    for rel in tree.getroot().findall(f"{{{REL}}}Relationship"):
        if str(rel.get("TargetMode") or "").lower() == "external":
            continue
        rid = str(rel.get("Id") or "")
        target = str(rel.get("Target") or "")
        package_target = _resolve_target(rels_part, target)
        if rid and package_target is not None:
            out[rid] = package_target
    return out


def _ole_preview_vector_targets(root: Path) -> set[PurePosixPath]:
    """Return WMF/EMF package parts used as visible previews of Word OLE objects.

    Detection is structural, not ProgID-specific: any ``w:object`` that contains an
    Office OLE object and a VML ``v:imagedata`` preview is treated as an OLE visual
    preview.  This therefore covers MathType, Equation Editor, chemistry add-ins and
    other legacy embedded objects without document-specific branches.
    """
    out: set[PurePosixPath] = set()
    word_root = root / "word"
    if not word_root.exists():
        return out
    for xml_path in word_root.rglob("*.xml"):
        try:
            source_part = PurePosixPath(xml_path.relative_to(root).as_posix())
            tree = etree.parse(str(xml_path))
        except Exception:
            continue
        rels = _relationship_target_map(root, source_part)
        if not rels:
            continue
        for obj in tree.getroot().iter(f"{{{W_NS}}}object"):
            # Some producers put o:OLEObject under w:object, others wrap it in a
            # VML shape.  Descendant search covers both representations.
            has_ole = any(True for _ in obj.iter(f"{{{O_NS}}}OLEObject"))
            if not has_ole:
                continue
            for image in obj.iter(f"{{{V_NS}}}imagedata"):
                rid = str(image.get(f"{{{R_NS}}}id") or "")
                target = rels.get(rid)
                if target is not None and target.suffix.lower() in VECTOR_EXTENSIONS:
                    out.add(target)
    return out


def _export_vectors(
    source_to_png: dict[Path, Path],
    ole_sources: set[Path] | None = None,
    scale: float = 6.0,
) -> tuple[dict[Path, str], dict[str, Any]]:
    """Fast fallback export of raw WMF/EMF parts through PowerPoint.

    HF21 no longer asks Word to reinterpret the *preview metafile*.  For real OLE
    objects the visual authority is now the actual object occurrence rendered from
    the marked Word document (see ``_export_ole_occurrence_surrogates`` below).
    Raw vector conversion remains only the generic fallback for ordinary WMF/EMF
    pictures and for OLE occurrences that Word cannot copy as a picture.
    """
    methods: dict[Path, str] = {}
    ole_sources = set(ole_sources or set())
    stats: dict[str, Any] = {
        "status": "not-needed" if not source_to_png else "pending",
        "oleVectorMedia": sum(1 for src in source_to_png if src in ole_sources),
        "wordReencodedMedia": 0,
        "directPowerPointMedia": 0,
        "wordReencodeFallbacks": 0,
    }
    if not source_to_png:
        return methods, stats
    if os.name != "nt":
        stats["status"] = "unavailable-non-windows"
        return methods, stats
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        stats["status"] = f"pywin32-unavailable: {exc}"
        return methods, stats

    ppt = None
    presentation = None
    pythoncom.CoInitialize()
    try:
        ppt = win32com.client.DispatchEx("PowerPoint.Application")
        presentation = ppt.Presentations.Add(WithWindow=False)
        slide = presentation.Slides.Add(1, PP_LAYOUT_BLANK)

        for src, dst in source_to_png.items():
            shape = None
            try:
                shape = slide.Shapes.AddPicture(str(src), False, True, 0, 0, -1, -1)
                natural_w = max(1.0, float(getattr(shape, "Width", 300.0) or 300.0))
                natural_h = max(1.0, float(getattr(shape, "Height", 180.0) or 180.0))
                px_w = max(96, min(4096, int(round(natural_w * scale))))
                px_h = max(96, min(4096, int(round(natural_h * scale))))
                dst.parent.mkdir(parents=True, exist_ok=True)
                shape.Export(str(dst), PP_SHAPE_FORMAT_PNG, px_w, px_h)
                if dst.exists() and dst.stat().st_size > 100:
                    methods[src] = "powerpoint-vector-to-png-6x"
                    stats["directPowerPointMedia"] += 1
            except Exception:
                pass
            finally:
                if shape is not None:
                    try:
                        shape.Delete()
                    except Exception:
                        pass

        stats["status"] = "powerpoint"
        return methods, stats
    except Exception as exc:
        stats["status"] = f"vector-export-failed: {type(exc).__name__}: {exc}"
        return methods, stats
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
        presentation = None
        ppt = None
        gc.collect()
        time.sleep(0.05)
        try:
            pythoncom.CoFreeUnusedLibraries()  # type: ignore[name-defined]
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()  # type: ignore[name-defined]
        except Exception:
            pass


def _wqn(local: str) -> str:
    return f"{{{W_NS}}}{local}"


def _next_word_bookmark_id(root: etree._Element) -> int:
    values: list[int] = []
    for node in root.iter(_wqn("bookmarkStart")):
        raw = str(node.get(_wqn("id")) or "")
        try:
            values.append(int(raw))
        except Exception:
            pass
    return max(values, default=0) + 1


def _ole_xml_context(obj: etree._Element) -> dict[str, Any]:
    paragraph = obj.getparent()
    while paragraph is not None and paragraph.tag != _wqn("p"):
        paragraph = paragraph.getparent()
    paragraph_ordinal = 0
    if paragraph is not None:
        try:
            paragraph_ordinal = int(paragraph.xpath("count(preceding::w:p)", namespaces={"w": W_NS})) + 1
        except Exception:
            paragraph_ordinal = 0

    table = obj.getparent()
    while table is not None and table.tag != _wqn("tbl"):
        table = table.getparent()
    row = obj.getparent()
    while row is not None and row.tag != _wqn("tr"):
        row = row.getparent()
    cell = obj.getparent()
    while cell is not None and cell.tag != _wqn("tc"):
        cell = cell.getparent()

    table_ordinal = row_ordinal = cell_ordinal = 0
    if table is not None:
        try:
            table_ordinal = int(table.xpath("count(preceding::w:tbl)", namespaces={"w": W_NS})) + 1
        except Exception:
            pass
    if row is not None:
        try:
            row_ordinal = int(row.xpath("count(preceding-sibling::w:tr)", namespaces={"w": W_NS})) + 1
        except Exception:
            pass
    if cell is not None:
        try:
            cell_ordinal = int(cell.xpath("count(preceding-sibling::w:tc)", namespaces={"w": W_NS})) + 1
        except Exception:
            pass

    context_key = f"p{paragraph_ordinal:04d}" if paragraph_ordinal else "p?"
    if table_ordinal:
        context_key += f"-t{table_ordinal:02d}-r{row_ordinal:02d}-c{cell_ordinal:02d}"
    return {
        "paragraphOrdinal": paragraph_ordinal,
        "tableOrdinal": table_ordinal,
        "rowOrdinal": row_ordinal,
        "cellOrdinal": cell_ordinal,
        "contextKey": context_key,
    }


def _document_ole_occurrences(root: Path) -> list[dict[str, Any]]:
    """Inventory every ``w:object`` occurrence in document order.

    The ordinal is deliberately the same order used by the browser DOM parser.
    This gives BookWriter an occurrence-level key rather than pretending that one
    shared WMF preview is the identity of an embedded OLE object.
    """
    document_part = PurePosixPath("word/document.xml")
    document_path = root / document_part.as_posix()
    if not document_path.exists():
        return []
    try:
        tree = etree.parse(str(document_path))
    except Exception:
        return []
    rels = _relationship_target_map(root, document_part)
    records: list[dict[str, Any]] = []
    for ordinal, obj in enumerate(tree.getroot().iter(_wqn("object")), 1):
        ole = next(obj.iter(f"{{{O_NS}}}OLEObject"), None)
        if ole is None:
            continue
        ole_rid = str(ole.get(f"{{{R_NS}}}id") or "")
        embedding = rels.get(ole_rid)
        preview = None
        preview_rid = ""
        for image in obj.iter(f"{{{V_NS}}}imagedata"):
            preview_rid = str(image.get(f"{{{R_NS}}}id") or "")
            preview = rels.get(preview_rid)
            if preview is not None:
                break
        payload_hash = ""
        if embedding is not None:
            payload = root / Path(embedding.as_posix())
            if payload.exists():
                try:
                    payload_hash = hashlib.sha256(payload.read_bytes()).hexdigest()
                except Exception:
                    payload_hash = ""
        context = _ole_xml_context(obj)
        records.append({
            "ordinal": int(ordinal),
            "triageId": f"OLE{int(ordinal):04d}",
            **context,
            "progId": str(ole.get("ProgID") or ""),
            "oleRid": ole_rid,
            "embedding": embedding.as_posix() if embedding is not None else "",
            "previewRid": preview_rid,
            "preview": preview.as_posix() if preview is not None else "",
            "payloadHash": payload_hash,
        })
    return records


def _inject_ole_occurrence_bookmarks(parts: Path, occurrences: list[dict[str, Any]]) -> dict[int, str]:
    document_path = parts / "word" / "document.xml"
    tree = etree.parse(str(document_path))
    root = tree.getroot()
    objects = list(root.iter(_wqn("object")))
    bookmark_id = _next_word_bookmark_id(root)
    mapping: dict[int, str] = {}
    run_names: dict[Any, str] = {}

    wanted = {int(record.get("ordinal") or 0) for record in occurrences}
    for ordinal, obj in enumerate(objects, 1):
        if ordinal not in wanted:
            continue
        run = obj.getparent()
        while run is not None and run.tag != _wqn("r"):
            run = run.getparent()
        if run is None or run.getparent() is None:
            continue
        # lxml can recycle Python proxy ids for XML nodes.  Use the element
        # itself as the key so one OLE occurrence can never inherit another
        # run's bookmark merely because a proxy id was reused.
        run_key = run
        if run_key in run_names:
            mapping[ordinal] = run_names[run_key]
            continue
        name = f"BWOLE{ordinal:06d}"
        parent = run.getparent()
        index = parent.index(run)
        start = etree.Element(_wqn("bookmarkStart"))
        start.set(_wqn("id"), str(bookmark_id))
        start.set(_wqn("name"), name)
        end = etree.Element(_wqn("bookmarkEnd"))
        end.set(_wqn("id"), str(bookmark_id))
        bookmark_id += 1
        parent.insert(index, start)
        parent.insert(index + 2, end)
        run_names[run_key] = name
        mapping[ordinal] = name

    document_path.write_bytes(
        etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
    )
    return mapping


def _rezip_parts(parts: Path, output: Path) -> None:
    with ZipFile(output, "w", ZIP_DEFLATED) as zf:
        for file in sorted(parts.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(parts).as_posix())


def _clear_near_white_border(png_path: Path) -> None:
    """Make only border-connected near-white canvas transparent."""
    try:
        from PIL import Image, ImageDraw  # type: ignore
        image = Image.open(png_path).convert("RGBA")
        probe = image.copy()
        sentinel = (1, 2, 3, 4)
        seeds = [
            (0, 0), (max(0, image.width - 1), 0),
            (0, max(0, image.height - 1)),
            (max(0, image.width - 1), max(0, image.height - 1)),
        ]
        step_x = max(1, image.width // 24)
        step_y = max(1, image.height // 24)
        seeds += [(x, 0) for x in range(0, image.width, step_x)]
        seeds += [(x, image.height - 1) for x in range(0, image.width, step_x)]
        seeds += [(0, y) for y in range(0, image.height, step_y)]
        seeds += [(image.width - 1, y) for y in range(0, image.height, step_y)]
        for seed in seeds:
            r, g, b, a = probe.getpixel(seed)
            if a <= 8 or (r >= 236 and g >= 236 and b >= 236 and max(r, g, b) - min(r, g, b) <= 16):
                try:
                    ImageDraw.floodfill(probe, seed, sentinel, thresh=24)
                except Exception:
                    pass
        pixels = image.load()
        marked = probe.load()
        for y in range(image.height):
            for x in range(image.width):
                if marked[x, y] == sentinel:
                    r, g, b, _ = pixels[x, y]
                    pixels[x, y] = (r, g, b, 0)
        image.save(png_path, format="PNG", optimize=True)
    except Exception:
        pass


def _export_ole_occurrence_surrogates(
    package_root: Path,
    temp_root: Path,
    occurrences: list[dict[str, Any]],
    scale: float = 6.0,
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    """HF24: render actual Word OLE occurrences with failure isolation.

    Primary path:
        Word CopyAsPicture -> direct Windows clipboard bitmap -> PNG.

    This bypasses PowerPoint completely for the normal case.  HF23 proved that
    the dominant failure was not OLE discovery or WMF conversion but the
    Word-clipboard-PowerPoint PasteSpecial bridge.  PowerPoint is now a fresh,
    per-payload fallback so one COM exception cannot poison all later payloads.
    """
    stats: dict[str, Any] = {
        "attemptedUniqueOlePayloads": 0,
        "renderedUniqueOlePayloads": 0,
        "mappedOleOccurrences": 0,
        "directBitmapPayloads": 0,
        "enhMetafilePayloads": 0,
        "wordPdfPayloads": 0,
        "freshWordRecoveredPayloads": 0,
        "powerPointFallbackPayloads": 0,
        "failedPayloads": 0,
        "status": "not-needed" if not occurrences else "pending",
    }
    if not occurrences:
        return {}, [], stats
    if os.name != "nt":
        stats["status"] = "unavailable-non-windows"
        return {}, [], stats
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except Exception as exc:
        stats["status"] = f"pywin32-unavailable: {exc}"
        return {}, [], stats

    marked_parts = temp_root / "ole_marked_parts"
    shutil.copytree(package_root, marked_parts)
    bookmark_map = _inject_ole_occurrence_bookmarks(marked_parts, occurrences)
    marked_docx = temp_root / "ole_marked.docx"
    _rezip_parts(marked_parts, marked_docx)

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in occurrences:
        key = str(record.get("payloadHash") or f"occ-{int(record.get('ordinal') or 0):06d}")
        groups.setdefault(key, []).append(record)
    stats["attemptedUniqueOlePayloads"] = len(groups)

    def copy_range_picture(rng) -> None:
        try:
            rng.CopyAsPicture()
            return
        except Exception:
            pass
        if int(getattr(rng.InlineShapes, "Count", 0) or 0) > 0:
            rng.InlineShapes.Item(1).Range.CopyAsPicture()
            return
        rng.CopyAsPicture()

    def fresh_word_bitmap(bookmark_name: str, dst: Path) -> dict[str, Any]:
        local_word = local_doc = None
        try:
            local_word = win32com.client.DispatchEx("Word.Application")
            local_word.Visible = False
            local_word.DisplayAlerts = 0
            try:
                local_word.ScreenUpdating = False
            except Exception:
                pass
            local_doc = local_word.Documents.Open(
                str(marked_docx), ReadOnly=True, AddToRecentFiles=False, Visible=False,
            )
            rng = local_doc.Bookmarks.Item(bookmark_name).Range
            return copy_as_picture_to_png(lambda: copy_range_picture(rng), dst, attempts=2, timeout_s=2.2)
        except Exception as exc:
            return {"ok": False, "backend": "fresh-word-clipboard-bitmap", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if local_doc is not None:
                try:
                    local_doc.Close(SaveChanges=False)
                except Exception:
                    pass
            if local_word is not None:
                try:
                    local_word.Quit(SaveChanges=False)
                except Exception:
                    pass
            local_doc = None
            local_word = None
            gc.collect()
            time.sleep(0.08)

    def fresh_powerpoint_from_word(rng, dst: Path) -> dict[str, Any]:
        """Last clipboard bridge fallback, isolated in a fresh PowerPoint process."""
        ppt = presentation = slide = pasted_shape = None
        try:
            clear_windows_clipboard()
            copy_range_picture(rng)
            time.sleep(0.18)
            ppt = win32com.client.DispatchEx("PowerPoint.Application")
            presentation = ppt.Presentations.Add(WithWindow=False)
            slide = presentation.Slides.Add(1, PP_LAYOUT_BLANK)
            pasted_range = None
            errors: list[str] = []
            for data_type in (PP_PASTE_ENHANCED_METAFILE,):
                try:
                    pasted_range = slide.Shapes.PasteSpecial(DataType=data_type)
                    break
                except Exception as exc:
                    errors.append(f"PasteSpecial({data_type}):{type(exc).__name__}:{exc}")
            if pasted_range is None:
                try:
                    pasted_range = slide.Shapes.Paste()
                except Exception as exc:
                    errors.append(f"Paste:{type(exc).__name__}:{exc}")
                    raise RuntimeError(" | ".join(errors))
            pasted_shape = pasted_range.Item(1)
            natural_w = max(1.0, float(getattr(pasted_shape, "Width", 300.0) or 300.0))
            natural_h = max(1.0, float(getattr(pasted_shape, "Height", 180.0) or 180.0))
            px_w = max(96, min(4096, int(round(natural_w * scale))))
            px_h = max(96, min(4096, int(round(natural_h * scale))))
            dst.parent.mkdir(parents=True, exist_ok=True)
            pasted_shape.Export(str(dst), PP_SHAPE_FORMAT_PNG, px_w, px_h)
            ok = bool(dst.exists() and dst.stat().st_size > 100)
            return {"ok": ok, "backend": "fresh-powerpoint-fallback", "errors": errors, "widthPx": px_w, "heightPx": px_h}
        except Exception as exc:
            return {"ok": False, "backend": "fresh-powerpoint-fallback", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            if pasted_shape is not None:
                try:
                    pasted_shape.Delete()
                except Exception:
                    pass
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
            pasted_shape = None
            presentation = None
            ppt = None
            gc.collect()
            time.sleep(0.08)

    occurrence_map: dict[str, str] = {}
    audit: list[dict[str, Any]] = []
    word = None
    doc = None
    pythoncom.CoInitialize()
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        try:
            word.ScreenUpdating = False
        except Exception:
            pass
        doc = word.Documents.Open(
            str(marked_docx), ReadOnly=True, AddToRecentFiles=False, Visible=False,
        )
        try:
            doc.Repaginate()
        except Exception:
            pass
        occurrence_pages: dict[int, int] = {}
        for occurrence_ordinal, bookmark_name in bookmark_map.items():
            try:
                occurrence_pages[int(occurrence_ordinal)] = int(doc.Bookmarks.Item(bookmark_name).Range.Information(3))
            except Exception:
                occurrence_pages[int(occurrence_ordinal)] = 0

        for key, records in groups.items():
            representative = next((r for r in records if int(r.get("ordinal") or 0) in bookmark_map), records[0])
            ordinal = int(representative.get("ordinal") or 0)
            bookmark_name = bookmark_map.get(ordinal, "")
            safe_key = re.sub(r"[^A-Za-z0-9_-]+", "_", key)[:32] or f"occ_{ordinal:06d}"
            package_png = PurePosixPath("word/media") / f"bw_ole_{safe_key}.png"
            dst = package_root / Path(package_png.as_posix())
            rendered = False
            method = ""
            page_number = 0
            direct_audit: dict[str, Any] = {}
            metafile_audit: dict[str, Any] = {}
            recovery_audit: dict[str, Any] = {}
            fallback_audit: dict[str, Any] = {}
            error = ""
            try:
                if not bookmark_name:
                    raise RuntimeError("bookmark-unavailable")
                bookmark = doc.Bookmarks.Item(bookmark_name)
                rng = bookmark.Range
                page_number = int(occurrence_pages.get(ordinal) or 0)
                visual_width = visual_height = 0.0
                try:
                    if int(getattr(rng.InlineShapes, "Count", 0) or 0) > 0:
                        visual_width = float(getattr(rng.InlineShapes.Item(1), "Width", 0.0) or 0.0)
                        visual_height = float(getattr(rng.InlineShapes.Item(1), "Height", 0.0) or 0.0)
                except Exception:
                    pass

                direct_audit = copy_as_picture_to_png(
                    lambda: copy_range_picture(rng), dst, attempts=3, timeout_s=2.0,
                )
                if direct_audit.get("ok"):
                    rendered = True
                    method = "word-clipboard-bitmap"
                    stats["directBitmapPayloads"] += 1
                else:
                    # HF26 evidence boundary: legacy Equation.DSMT4/OLE objects
                    # that do not publish a CF_DIB are NOT sent through the GDI
                    # metafile path. In HF25 the four such payloads produced tiny
                    # 64x64/134-byte surrogates and visible garbage in the book,
                    # while HF24's Word->PDF route rendered all four successfully.
                    # Keep OLE and DrawingML-group fallback policies separate.
                    clear_windows_clipboard()
                    copy_range_picture(rng)
                    time.sleep(0.12)
                    word_pdf_ok, word_pdf_error = _clipboard_to_png_with_word_pdf(
                        word, dst, visual_width, visual_height
                    )
                    if word_pdf_ok:
                        rendered = True
                        method = "word-pdf-pymupdf"
                        stats["wordPdfPayloads"] += 1
                    else:
                        recovery_audit = fresh_word_bitmap(bookmark_name, dst)
                        if recovery_audit.get("ok"):
                            rendered = True
                            method = "fresh-word-clipboard-bitmap"
                            stats["freshWordRecoveredPayloads"] += 1
                        else:
                            fallback_audit = fresh_powerpoint_from_word(rng, dst)
                            if fallback_audit.get("ok"):
                                rendered = True
                                method = "fresh-powerpoint-fallback"
                                stats["powerPointFallbackPayloads"] += 1
                            elif word_pdf_error:
                                fallback_audit.setdefault("wordPdfError", word_pdf_error)
                if rendered:
                    _clear_near_white_border(dst)
                else:
                    error = "direct-bitmap-failed"
                    if recovery_audit.get("error"):
                        error += f" | fresh-word:{recovery_audit.get('error')}"
                    if fallback_audit.get("error"):
                        error += f" | powerpoint:{fallback_audit.get('error')}"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                rendered = False

            if rendered:
                stats["renderedUniqueOlePayloads"] += 1
                for record in records:
                    occurrence_map[str(int(record.get("ordinal") or 0))] = package_png.as_posix()
            else:
                stats["failedPayloads"] += 1

            audit.append({
                "payloadKey": key,
                "representativeOrdinal": ordinal,
                "representativeTriageId": str(representative.get("triageId") or f"OLE{ordinal:04d}"),
                "contextKey": str(representative.get("contextKey") or ""),
                "paragraphOrdinal": int(representative.get("paragraphOrdinal") or 0),
                "tableOrdinal": int(representative.get("tableOrdinal") or 0),
                "rowOrdinal": int(representative.get("rowOrdinal") or 0),
                "cellOrdinal": int(representative.get("cellOrdinal") or 0),
                "sourcePage": page_number,
                "occurrences": [int(r.get("ordinal") or 0) for r in records],
                "occurrenceLocations": [
                    {
                        "ordinal": int(r.get("ordinal") or 0),
                        "triageId": str(r.get("triageId") or ""),
                        "contextKey": str(r.get("contextKey") or ""),
                        "paragraphOrdinal": int(r.get("paragraphOrdinal") or 0),
                        "tableOrdinal": int(r.get("tableOrdinal") or 0),
                        "rowOrdinal": int(r.get("rowOrdinal") or 0),
                        "cellOrdinal": int(r.get("cellOrdinal") or 0),
                        "sourcePage": int(occurrence_pages.get(int(r.get("ordinal") or 0)) or 0),
                    }
                    for r in records
                ],
                "surrogate": package_png.as_posix() if rendered else "",
                "rendered": rendered,
                "method": method,
                "directBitmapAudit": direct_audit,
                "enhMetafileAudit": metafile_audit,
                **({"freshWordAudit": recovery_audit} if recovery_audit else {}),
                **({"powerPointFallbackAudit": fallback_audit} if fallback_audit else {}),
                **({"error": error} if error else {}),
            })

        stats["mappedOleOccurrences"] = len(occurrence_map)
        if stats["failedPayloads"]:
            stats["status"] = "word-ole-isolated-partial"
        elif occurrence_map:
            stats["status"] = "word-ole-direct-bitmap-complete"
        else:
            stats["status"] = "word-ole-occurrence-unavailable"
        return occurrence_map, audit, stats
    except Exception as exc:
        stats["status"] = f"word-ole-occurrence-failed: {type(exc).__name__}: {exc}"
        return {}, audit, stats
    finally:
        if doc is not None:
            try:
                doc.Close(SaveChanges=False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit(SaveChanges=False)
            except Exception:
                pass
        doc = None
        word = None
        gc.collect()
        time.sleep(0.05)
        try:
            pythoncom.CoFreeUnusedLibraries()  # type: ignore[name-defined]
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()  # type: ignore[name-defined]
        except Exception:
            pass


def _ensure_png_content_type(content_types_path: Path) -> None:
    tree = etree.parse(str(content_types_path))
    root = tree.getroot()
    defaults = root.findall(f"{{{CT}}}Default")
    if not any(str(node.get("Extension") or "").lower() == "png" for node in defaults):
        node = etree.Element(f"{{{CT}}}Default")
        node.set("Extension", "png")
        node.set("ContentType", "image/png")
        root.insert(0, node)
        tree.write(str(content_types_path), encoding="UTF-8", xml_declaration=True, standalone=True)


def convert_vector_previews_in_docx(docx_path: Path) -> dict[str, Any]:
    """Add BookWriter PNG surrogates without rewriting Word relationships.

    This is deliberately a dual-representation contract:
    * Word/OLE continues to point at the original WMF/EMF part.
    * BookWriter resolves the same package path through a sidecar manifest.

    That separation prevents browser compatibility work from changing the Word
    layout *after* the rendered page map has already been measured.
    """
    docx_path = Path(docx_path).resolve()
    inventory = inspect_vector_previews(docx_path)
    result: dict[str, Any] = {
        "version": 5,
        "source": "hf26-word-source-page-and-ole-safe-surrogate-manifest",
        **inventory,
        "attemptedMedia": 0,
        "convertedMedia": 0,
        "updatedRelationships": 0,
        "preservedRelationships": int(inventory.get("vectorRelationshipCount") or 0),
        "failedMedia": 0,
        "status": "not-needed" if not inventory["vectorMedia"] and not inventory.get("oleObjects") else "pending",
        "renderer": "",
        "oleVectorMedia": 0,
        "wordReencodedMedia": 0,
        "directPowerPointMedia": 0,
        "wordReencodeFallbacks": 0,
        "manifest": SURROGATE_MANIFEST,
        "records": [],
        "oleOccurrences": 0,
        "oleMappedOccurrences": 0,
        "oleUniquePayloads": 0,
        "oleRenderedPayloads": 0,
        "oleOccurrenceRenderer": "",
        "oleOccurrenceRecords": [],
        "oleDirectBitmapPayloads": 0,
        "oleWordPdfPayloads": 0,
        "oleFreshWordRecoveredPayloads": 0,
        "olePowerPointFallbackPayloads": 0,
        "oleFailedPayloads": 0,
    }
    # Actual OLE occurrences are renderable by Word even when their package
    # preview is raster or absent.  Do not gate the occurrence renderer on the
    # presence of WMF/EMF media; vector conversion and OLE occurrence rendering
    # are independent browser-surrogate capabilities.
    if not inventory["vectorMedia"] and not inventory.get("oleObjects"):
        return result

    with tempfile.TemporaryDirectory(prefix="bookwriter_vector_preview_") as td:
        root = Path(td) / "pkg"
        root.mkdir(parents=True, exist_ok=True)
        with ZipFile(docx_path, "r") as zf:
            zf.extractall(root)

        ole_preview_targets = _ole_preview_vector_targets(root)
        result["oleVectorMedia"] = len(ole_preview_targets)

        # HF21: actual OLE occurrence is the visual authority.  The WMF/EMF
        # preview remains available only as a fallback.
        ole_occurrences = _document_ole_occurrences(root)
        ole_occurrence_map, ole_occurrence_records, ole_occurrence_stats = (
            _export_ole_occurrence_surrogates(root, Path(td), ole_occurrences)
        )
        result["oleOccurrences"] = len(ole_occurrences)
        result["oleMappedOccurrences"] = len(ole_occurrence_map)
        # Payload inventory is structural and remains useful even when COM is
        # unavailable on the current host.
        result["oleUniquePayloads"] = len({
            str(r.get("payloadHash") or f"occ-{int(r.get('ordinal') or 0):06d}")
            for r in ole_occurrences
        })
        result["oleRenderedPayloads"] = int(ole_occurrence_stats.get("renderedUniqueOlePayloads") or 0)
        result["oleOccurrenceRenderer"] = str(ole_occurrence_stats.get("status") or "")
        result["oleOccurrenceRecords"] = ole_occurrence_records
        result["oleDirectBitmapPayloads"] = int(ole_occurrence_stats.get("directBitmapPayloads") or 0)
        result["oleEnhMetafilePayloads"] = int(ole_occurrence_stats.get("enhMetafilePayloads") or 0)
        result["oleWordPdfPayloads"] = int(ole_occurrence_stats.get("wordPdfPayloads") or 0)
        result["oleFreshWordRecoveredPayloads"] = int(ole_occurrence_stats.get("freshWordRecoveredPayloads") or 0)
        result["olePowerPointFallbackPayloads"] = int(ole_occurrence_stats.get("powerPointFallbackPayloads") or 0)
        result["oleFailedPayloads"] = int(ole_occurrence_stats.get("failedPayloads") or 0)

        relationship_refs: dict[PurePosixPath, list[tuple[Path, str]]] = {}
        for rel_path in root.rglob("*.rels"):
            try:
                relative = PurePosixPath(rel_path.relative_to(root).as_posix())
                tree = etree.parse(str(rel_path))
            except Exception:
                continue
            for rel in tree.getroot().findall(f"{{{REL}}}Relationship"):
                if str(rel.get("TargetMode") or "").lower() == "external":
                    continue
                target = str(rel.get("Target") or "")
                if PurePosixPath(target).suffix.lower() not in VECTOR_EXTENSIONS:
                    continue
                package_target = _resolve_target(relative, target)
                if package_target is None:
                    continue
                source_file = root / Path(package_target.as_posix())
                if not source_file.exists():
                    continue
                relationship_refs.setdefault(package_target, []).append((rel_path, target))

        source_to_png: dict[Path, Path] = {}
        package_to_png: dict[PurePosixPath, PurePosixPath] = {}
        for package_target in relationship_refs:
            src = root / Path(package_target.as_posix())
            png_package = package_target.with_name(package_target.stem + "_bw_web.png")
            dst = root / Path(png_package.as_posix())
            source_to_png[src] = dst
            package_to_png[package_target] = png_package
        result["attemptedMedia"] = len(source_to_png)

        ole_sources = {
            root / Path(target.as_posix())
            for target in ole_preview_targets
            if target in package_to_png
        }
        methods, renderer_stats = _export_vectors(source_to_png, ole_sources)
        result["renderer"] = str(renderer_stats.get("status") or "")
        result["wordReencodedMedia"] = int(renderer_stats.get("wordReencodedMedia") or 0)
        result["directPowerPointMedia"] = int(renderer_stats.get("directPowerPointMedia") or 0)
        result["wordReencodeFallbacks"] = int(renderer_stats.get("wordReencodeFallbacks") or 0)
        manifest_map: dict[str, str] = {}
        for package_target, refs in relationship_refs.items():
            src = root / Path(package_target.as_posix())
            dst = source_to_png[src]
            success = src in methods and dst.exists() and dst.stat().st_size > 100
            surrogate = package_to_png[package_target]
            record = {
                "source": package_target.as_posix(),
                "surrogate": surrogate.as_posix(),
                "relationships": len(refs),
                "converted": bool(success),
                "method": methods.get(src, ""),
                "olePreview": package_target in ole_preview_targets,
                "wordRelationshipPreserved": True,
            }
            if success:
                result["convertedMedia"] += 1
                manifest_map[package_target.as_posix()] = surrogate.as_posix()
            else:
                result["failedMedia"] += 1
            result["records"].append(record)

        if manifest_map or ole_occurrence_map:
            manifest_path = root / SURROGATE_MANIFEST
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            ole_occurrence_audit: dict[str, Any] = {}
            for payload_record in ole_occurrence_records:
                for location in payload_record.get("occurrenceLocations") or []:
                    occ = str(int(location.get("ordinal") or 0))
                    ole_occurrence_audit[occ] = {
                        **location,
                        "rendered": bool(payload_record.get("rendered")),
                        "method": str(payload_record.get("method") or ""),
                        "surrogate": str(payload_record.get("surrogate") or ""),
                        "representativeOrdinal": int(payload_record.get("representativeOrdinal") or 0),
                        "error": str(payload_record.get("error") or ""),
                    }
            manifest_payload = {
                "version": 5,
                "source": "hf26-word-source-page-and-ole-safe-surrogate-manifest",
                "map": manifest_map,
                "oleOccurrences": ole_occurrence_map,
                "oleOccurrenceAudit": ole_occurrence_audit,
                "records": result["records"],
                "oleOccurrenceRecords": ole_occurrence_records,
            }
            manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            _ensure_png_content_type(root / "[Content_Types].xml")
            temp_zip = Path(td) / "patched.docx"
            with ZipFile(temp_zip, "w", ZIP_DEFLATED) as zf:
                for file in root.rglob("*"):
                    if file.is_file():
                        zf.write(file, file.relative_to(root).as_posix())
            shutil.copy2(temp_zip, docx_path)

        if result["convertedMedia"] == result["attemptedMedia"] and result["attemptedMedia"]:
            result["status"] = "converted-all-nonmutating" + ("+ole-occurrence" if ole_occurrence_map else "")
        elif result["convertedMedia"] or ole_occurrence_map:
            result["status"] = "converted-partial-nonmutating" + ("+ole-occurrence" if ole_occurrence_map else "")
        else:
            result["status"] = "unavailable-or-failed"
    return result

