from __future__ import annotations

"""Rendered Word page map for the BookWriter DOCX importer.

HF13 keeps the fast Word-rendered page map from HF12 and adds two narrowly
scoped truth probes:

* exact visible Word list labels remain available for multilevel-list rendering;
* table rows get exact start/end rendered pages;
* table-cell paragraph start pages are queried only for rows that actually span
  rendered pages, so a spanning row can be reconstructed at paragraph boundaries;
* top-level page ownership still uses fast page-boundary searches rather than
  querying every paragraph individually.

The marked copy is temporary and never reaches the user.  The resulting page
map is embedded into the canonical DOCX as customXml/bookwriter-page-map.xml.
"""

import gc
import os
import platform
import tempfile
import time
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
WD_ACTIVE_END_PAGE_NUMBER = 3
WD_STATISTIC_PAGES = 2
WD_DO_NOT_SAVE_CHANGES = 0
MAP_PATH = "customXml/bookwriter-page-map.xml"
PAGE_MAP_NS = "urn:bookwriter:page-map:v1"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CUSTOM_XML_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/customXml"
VERSION = "word-rendered-page-map-v3-list-row-fragments"


def qn(local: str) -> str:
    return f"{{{W}}}{local}"


def _next_bookmark_id(root: etree._Element) -> int:
    values: list[int] = []
    for node in root.findall(".//w:bookmarkStart", NS):
        raw = node.get(qn("id"), "")
        try:
            values.append(int(raw))
        except (TypeError, ValueError):
            pass
    return max(values, default=0) + 1


def _insert_zero_bookmark(paragraph: etree._Element, name: str, bookmark_id: int) -> int:
    start = etree.Element(qn("bookmarkStart"))
    start.set(qn("id"), str(bookmark_id))
    start.set(qn("name"), name)
    end = etree.Element(qn("bookmarkEnd"))
    end.set(qn("id"), str(bookmark_id))
    ppr = paragraph.find("w:pPr", NS)
    index = 1 if ppr is not None and len(paragraph) and paragraph[0] is ppr else 0
    paragraph.insert(index, start)
    paragraph.insert(index + 1, end)
    return bookmark_id + 1


def _insert_zero_bookmark_at_end(paragraph: etree._Element, name: str, bookmark_id: int) -> int:
    """Insert a zero-width bookmark immediately before the paragraph mark.

    In OOXML the paragraph mark is implicit, so appending the bookmark pair as
    the last children places the probe at the rendered end of the paragraph
    without adding visible content.
    """
    start = etree.Element(qn("bookmarkStart"))
    start.set(qn("id"), str(bookmark_id))
    start.set(qn("name"), name)
    end = etree.Element(qn("bookmarkEnd"))
    end.set(qn("id"), str(bookmark_id))
    paragraph.append(start)
    paragraph.append(end)
    return bookmark_id + 1


def _first_paragraph_in_cell(cell: etree._Element) -> etree._Element | None:
    direct = cell.findall("./w:p", NS)
    if direct:
        return direct[0]
    nested = cell.findall(".//w:p", NS)
    return nested[0] if nested else None


def _last_paragraph_in_cell(cell: etree._Element) -> etree._Element | None:
    direct = cell.findall("./w:p", NS)
    if direct:
        return direct[-1]
    nested = cell.findall(".//w:p", NS)
    return nested[-1] if nested else None


def _style_list_map(parts: Path) -> dict[str, bool]:
    styles_path = parts / "word" / "styles.xml"
    if not styles_path.exists():
        return {}
    try:
        root = etree.parse(str(styles_path)).getroot()
    except Exception:
        return {}
    direct: dict[str, bool] = {}
    based_on: dict[str, str] = {}
    for style in root.findall("w:style", NS):
        sid = style.get(qn("styleId"), "")
        if not sid:
            continue
        parent = style.find("w:basedOn", NS)
        if parent is not None:
            based_on[sid] = parent.get(qn("val"), "")
        ppr = style.find("w:pPr", NS)
        numpr = ppr.find("w:numPr", NS) if ppr is not None else None
        numid = numpr.find("w:numId", NS) if numpr is not None else None
        if numid is not None:
            value = numid.get(qn("val"), "")
            direct[sid] = bool(value and value != "0")
    resolved: dict[str, bool] = {}
    def resolve(sid: str, stack: set[str] | None = None) -> bool:
        if sid in resolved:
            return resolved[sid]
        stack = stack or set()
        if not sid or sid in stack:
            return False
        stack.add(sid)
        value = direct[sid] if sid in direct else resolve(based_on.get(sid, ""), stack)
        resolved[sid] = value
        return value
    for sid in set(direct) | set(based_on):
        resolve(sid)
    return resolved

def _paragraph_is_direct_list(paragraph: etree._Element) -> bool:
    ppr = paragraph.find("w:pPr", NS)
    numpr = ppr.find("w:numPr", NS) if ppr is not None else None
    numid = numpr.find("w:numId", NS) if numpr is not None else None
    value = numid.get(qn("val"), "") if numid is not None else ""
    return bool(value and value != "0")


def _inject_markers(parts: Path) -> dict[str, Any]:
    document_path = parts / "word" / "document.xml"
    tree = etree.parse(str(document_path))
    root = tree.getroot()
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError("word/document.xml has no w:body")

    bookmark_id = _next_bookmark_id(root)
    source_index = 0
    paragraph_count = 0
    table_count = 0
    row_count = 0
    list_candidates = 0
    markers: dict[str, dict[str, Any]] = {}
    style_lists = _style_list_map(parts)

    for child in list(body):
        if child.tag == qn("p"):
            source_index += 1
            paragraph_count += 1
            start_name = f"BWP{source_index:06d}S"
            bookmark_id = _insert_zero_bookmark(child, start_name, bookmark_id)
            is_list = _paragraph_is_direct_list(child)
            if not is_list:
                ppr = child.find("w:pPr", NS)
                pstyle = ppr.find("w:pStyle", NS) if ppr is not None else None
                sid = pstyle.get(qn("val"), "") if pstyle is not None else ""
                is_list = bool(style_lists.get(sid, False))
            if is_list:
                list_candidates += 1
            markers[str(source_index)] = {
                "kind": "paragraph",
                "start": start_name,
                "listCandidate": is_list,
            }
        elif child.tag == qn("tbl"):
            source_index += 1
            table_count += 1
            rows: list[dict[str, Any]] = []
            block_start = ""
            for row_index, tr in enumerate(child.findall("./w:tr", NS), 1):
                cells = tr.findall("./w:tc", NS)
                if not cells:
                    continue
                first_p = _first_paragraph_in_cell(cells[0])
                last_p = _last_paragraph_in_cell(cells[-1])
                if first_p is None or last_p is None:
                    continue
                start_name = f"BWT{source_index:06d}R{row_index:04d}S"
                end_name = f"BWT{source_index:06d}R{row_index:04d}E"
                bookmark_id = _insert_zero_bookmark(first_p, start_name, bookmark_id)
                if not block_start:
                    block_start = start_name

                cell_markers: list[dict[str, Any]] = []
                for cell_index, cell in enumerate(cells, 1):
                    paragraphs: list[dict[str, Any]] = []
                    for paragraph_index, paragraph in enumerate(cell.findall("./w:p", NS), 1):
                        # Reuse the row-start marker for the first paragraph of
                        # the first cell. Every other direct cell paragraph gets
                        # its own zero-width start marker. These markers are only
                        # queried if the row is later proven to span pages.
                        if cell_index == 1 and paragraph_index == 1:
                            paragraph_name = start_name
                        else:
                            paragraph_name = (
                                f"BWT{source_index:06d}R{row_index:04d}"
                                f"C{cell_index:03d}P{paragraph_index:03d}S"
                            )
                            bookmark_id = _insert_zero_bookmark(paragraph, paragraph_name, bookmark_id)
                        paragraphs.append({"paragraph": paragraph_index, "start": paragraph_name})
                    cell_markers.append({"cell": cell_index, "paragraphs": paragraphs})

                bookmark_id = _insert_zero_bookmark_at_end(last_p, end_name, bookmark_id)
                rows.append({
                    "row": row_index,
                    "start": start_name,
                    "end": end_name,
                    "cells": cell_markers,
                })
                row_count += 1
            markers[str(source_index)] = {
                "kind": "table",
                "start": block_start,
                "rows": rows,
            }

    document_path.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes"))
    return {
        "markers": markers,
        "topLevelBlocks": source_index,
        "paragraphs": paragraph_count,
        "tables": table_count,
        "rows": row_count,
        "listCandidates": list_candidates,
    }


def _rezip(parts: Path, output: Path) -> None:
    with ZipFile(output, "w", ZIP_DEFLATED) as z:
        for path in sorted(parts.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(parts).as_posix())


def _bookmark_page(doc, name: str) -> int | None:
    if not name:
        return None
    try:
        bookmark = doc.Bookmarks.Item(name)
        page = int(bookmark.Range.Information(WD_ACTIVE_END_PAGE_NUMBER))
        return page if page > 0 else None
    except Exception:
        return None


def _bookmark_list_value(doc, name: str) -> tuple[int | None, str]:
    if not name:
        return None, ""
    try:
        bookmark = doc.Bookmarks.Item(name)
        paragraph_range = bookmark.Range.Paragraphs.Item(1).Range
        value = int(paragraph_range.ListFormat.ListValue)
        label = str(paragraph_range.ListFormat.ListString or "")
        return (value if value > 0 else None), label
    except Exception:
        return None, ""


def _open_word_map(marked_docx: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    try:
        import pythoncom  # type: ignore
        import win32com.client as win32_client  # type: ignore
    except Exception as exc:  # pragma: no cover - Windows runtime path
        raise RuntimeError(f"pywin32/Word COM unavailable: {exc}") from exc

    last_error: Exception | None = None
    # STAGE9B2_FIX3_WORD_PAGE_MAP_COM_APARTMENT
    # The BookWriter gateway uses ThreadingHTTPServer. Every request therefore
    # runs in a worker thread, and pywin32 COM must be initialized explicitly in
    # that thread before DispatchEx("Word.Application").
    for attempt in range(1, 4):
        word = None
        doc = None
        com_initialized = False
        try:
            pythoncom.CoInitialize()
            com_initialized = True
            word = win32_client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = 0
            doc = word.Documents.Open(
                str(marked_docx),
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
            doc.Repaginate()
            page_count = max(1, int(doc.ComputeStatistics(WD_STATISTIC_PAGES)))
            markers = inventory["markers"]
            block_count = int(inventory["topLevelBlocks"])
            missing: list[str] = []
            page_cache: dict[str, int | None] = {}

            def page_for_name(name: str) -> int | None:
                if name not in page_cache:
                    page_cache[name] = _bookmark_page(doc, name)
                    if page_cache[name] is None and name:
                        missing.append(name)
                return page_cache[name]

            def page_for_block(index: int) -> int | None:
                marker = markers.get(str(index)) or {}
                return page_for_name(str(marker.get("start") or ""))

            # Find only the first top-level block that starts on each rendered page.
            # Page ownership is monotonic, so a binary search needs O(P log N)
            # Word/COM page queries instead of O(2N).
            boundaries: dict[int, int] = {1: 1}
            for target_page in range(2, page_count + 1):
                lo, hi = 1, block_count + 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    mid_page = page_for_block(mid)
                    if mid_page is None:
                        # Missing marker: narrow conservatively to the right. The
                        # final validation below will fall back if mapping is weak.
                        lo = mid + 1
                    elif mid_page >= target_page:
                        hi = mid
                    else:
                        lo = mid + 1
                boundaries[target_page] = lo

            blocks: dict[str, Any] = {}
            current_page = 1
            for index in range(1, block_count + 1):
                while current_page < page_count and boundaries.get(current_page + 1, block_count + 1) <= index:
                    current_page += 1
                marker = markers.get(str(index)) or {}
                blocks[str(index)] = {
                    "kind": str(marker.get("kind") or ""),
                    "startPage": current_page,
                    # HF12 intentionally treats a Word paragraph as one natural
                    # pagination fragment. End-page probing is omitted; overflow
                    # remains visible instead of splitting the paragraph silently.
                    "endPage": current_page,
                }

            # Tables need exact row start/end ownership.  Only rows that really
            # span rendered pages trigger the more detailed cell-paragraph page
            # queries used by the browser importer to create continuation rows.
            spanning_rows = 0
            table_paragraph_queries_before = len(page_cache)
            for key, marker in markers.items():
                if marker.get("kind") != "table":
                    continue
                rows: list[dict[str, Any]] = []
                for row_marker in marker.get("rows", []):
                    start_page = page_for_name(str(row_marker.get("start") or ""))
                    end_page = page_for_name(str(row_marker.get("end") or ""))
                    if start_page is None:
                        continue
                    if end_page is None:
                        end_page = start_page
                    row_data: dict[str, Any] = {
                        "row": int(row_marker["row"]),
                        "startPage": start_page,
                        "endPage": max(start_page, end_page),
                    }
                    if end_page > start_page:
                        spanning_rows += 1
                        cells_out: list[dict[str, Any]] = []
                        for cell_marker in row_marker.get("cells", []):
                            paragraphs_out: list[dict[str, Any]] = []
                            for paragraph_marker in cell_marker.get("paragraphs", []):
                                paragraph_page = page_for_name(str(paragraph_marker.get("start") or ""))
                                if paragraph_page is None:
                                    continue
                                paragraphs_out.append({
                                    "paragraph": int(paragraph_marker.get("paragraph") or 0),
                                    "startPage": paragraph_page,
                                })
                            cells_out.append({
                                "cell": int(cell_marker.get("cell") or 0),
                                "paragraphs": paragraphs_out,
                            })
                        row_data["cells"] = cells_out
                    rows.append(row_data)
                block = blocks.setdefault(key, {"kind": "table", "startPage": None, "endPage": None})
                if rows:
                    block["startPage"] = rows[0]["startPage"]
                    block["endPage"] = max(int(r.get("endPage") or r.get("startPage") or 1) for r in rows)
                block["rows"] = rows
                block["rowSpansPages"] = sum(1 for r in rows if int(r.get("endPage") or 0) > int(r.get("startPage") or 0))
                block["rowEndPageExact"] = True

            # Ask Word for the displayed list ordinal only for actual list
            # candidates. This is authoritative for restarts/continuations and is
            # far cheaper than querying every paragraph for list state.
            list_values_mapped = 0
            for key, marker in markers.items():
                if marker.get("kind") != "paragraph" or not marker.get("listCandidate"):
                    continue
                value, label = _bookmark_list_value(doc, str(marker.get("start") or ""))
                if value is not None:
                    blocks[key]["listValue"] = value
                    list_values_mapped += 1
                if label:
                    blocks[key]["listString"] = label

            # Validate the boundary map with the first/last starts. Missing pages
            # without a starting paragraph are allowed (a single paragraph/table
            # may visually span them); total pageCount still comes from Word.
            first_page = page_for_block(1) if block_count else 1
            last_page = page_for_block(block_count) if block_count else page_count
            if block_count and first_page is None:
                raise RuntimeError("Word page-map could not resolve the first top-level block.")
            if block_count and last_page is None:
                raise RuntimeError("Word page-map could not resolve the last top-level block.")

            return {
                "version": 3,
                "source": VERSION,
                "available": True,
                "pageCount": page_count,
                "topLevelBlocks": block_count,
                "paragraphs": int(inventory["paragraphs"]),
                "tables": int(inventory["tables"]),
                "rows": int(inventory["rows"]),
                "listCandidates": int(inventory.get("listCandidates") or 0),
                "listValuesMapped": list_values_mapped,
                "mappedBlocks": len(blocks),
                "missingMarkers": sorted(set(missing)),
                "pageQueries": len(page_cache),
                "boundaryQueries": len(page_cache),
                "spanningTableRows": spanning_rows,
                "tableParagraphPageQueries": max(0, len(page_cache) - table_paragraph_queries_before),
                "paragraphEndPageExact": False,
                "blocks": blocks,
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        except Exception as exc:  # pragma: no cover - Windows runtime path
            last_error = exc
            time.sleep(0.6 * attempt)
        finally:
            try:
                if doc is not None:
                    doc.Close(WD_DO_NOT_SAVE_CHANGES)
            except Exception:
                pass
            try:
                if word is not None:
                    word.Quit()
            except Exception:
                pass
            doc = None
            word = None
            try:
                if com_initialized:
                    pythoncom.CoFreeUnusedLibraries()
            except Exception:
                pass
            try:
                if com_initialized:
                    pythoncom.CoUninitialize()
            except Exception:
                pass
            gc.collect()
    raise RuntimeError(f"Word COM rendered page mapping failed after 3 COM-initialized attempts: {last_error}")


def extract_word_page_map(input_docx: Path) -> dict[str, Any]:
    input_docx = Path(input_docx).resolve()
    base = {
        "version": 3,
        "source": VERSION,
        "available": False,
        "pageCount": 0,
        "blocks": {},
        "platform": platform.system(),
    }
    if platform.system().lower() != "windows":
        return {**base, "status": "unavailable-non-windows", "error": "Desktop Word page mapping requires Windows + Microsoft Word."}
    if not input_docx.exists():
        return {**base, "status": "missing-input", "error": str(input_docx)}

    try:
        with tempfile.TemporaryDirectory(prefix="bookwriter_word_page_map_") as td:
            work = Path(td)
            parts = work / "parts"
            parts.mkdir()
            with ZipFile(input_docx) as z:
                z.extractall(parts)
            inventory = _inject_markers(parts)
            marked = work / "marked.docx"
            _rezip(parts, marked)
            result = _open_word_map(marked, inventory)
            result["status"] = "ok"
            return result
    except Exception as exc:
        return {**base, "status": "failed", "error": str(exc)}


def _page_map_xml(page_map: dict[str, Any]) -> bytes:
    root = etree.Element(f"{{{PAGE_MAP_NS}}}pageMap", nsmap={None: PAGE_MAP_NS})
    for key in (
        "version", "source", "status", "pageCount", "topLevelBlocks", "paragraphs", "tables", "rows",
        "mappedBlocks", "listCandidates", "listValuesMapped", "pageQueries", "boundaryQueries",
        "spanningTableRows", "tableParagraphPageQueries"
    ):
        value = page_map.get(key)
        if value is not None:
            root.set(key, str(value))
    root.set("available", "true" if page_map.get("available") else "false")
    root.set("paragraphEndPageExact", "true" if page_map.get("paragraphEndPageExact") else "false")
    if page_map.get("error"):
        error = etree.SubElement(root, f"{{{PAGE_MAP_NS}}}error")
        error.text = str(page_map.get("error"))
    missing = page_map.get("missingMarkers") or []
    if missing:
        node = etree.SubElement(root, f"{{{PAGE_MAP_NS}}}missingMarkers")
        for name in missing:
            child = etree.SubElement(node, f"{{{PAGE_MAP_NS}}}marker")
            child.set("name", str(name))
    blocks = etree.SubElement(root, f"{{{PAGE_MAP_NS}}}blocks")
    for index in sorted((page_map.get("blocks") or {}).keys(), key=lambda x: int(x)):
        block = page_map["blocks"][index]
        node = etree.SubElement(blocks, f"{{{PAGE_MAP_NS}}}block")
        node.set("index", str(index))
        node.set("kind", str(block.get("kind", "")))
        for key in ("startPage", "endPage", "rowSpansPages", "listValue"):
            value = block.get(key)
            if value is not None:
                node.set(key, str(value))
        if block.get("listString"):
            node.set("listString", str(block.get("listString")))
        if block.get("rowEndPageExact") is not None:
            node.set("rowEndPageExact", "true" if block.get("rowEndPageExact") else "false")
        for row in block.get("rows") or []:
            rn = etree.SubElement(node, f"{{{PAGE_MAP_NS}}}row")
            for key in ("row", "startPage", "endPage"):
                value = row.get(key)
                if value is not None:
                    rn.set(key, str(value))
            for cell in row.get("cells") or []:
                cn = etree.SubElement(rn, f"{{{PAGE_MAP_NS}}}cell")
                cn.set("cell", str(cell.get("cell", 0)))
                for paragraph in cell.get("paragraphs") or []:
                    pn = etree.SubElement(cn, f"{{{PAGE_MAP_NS}}}paragraph")
                    pn.set("paragraph", str(paragraph.get("paragraph", 0)))
                    if paragraph.get("startPage") is not None:
                        pn.set("startPage", str(paragraph.get("startPage")))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")


def embed_page_map(docx_path: Path, page_map: dict[str, Any]) -> None:
    """Embed a valid custom-XML page map and relationship into the DOCX package."""
    docx_path = Path(docx_path).resolve()
    temp = docx_path.with_suffix(docx_path.suffix + ".page-map.tmp")
    payload = _page_map_xml(page_map)
    rels_path = "word/_rels/document.xml.rels"
    with ZipFile(docx_path, "r") as src:
        rels_root = etree.fromstring(src.read(rels_path))
        for rel in list(rels_root):
            if rel.get("Type") == CUSTOM_XML_REL and rel.get("Target") == "../customXml/bookwriter-page-map.xml":
                rels_root.remove(rel)
        used = {rel.get("Id") for rel in rels_root}
        n = 1
        while f"rIdBWPM{n}" in used:
            n += 1
        rel = etree.SubElement(rels_root, f"{{{REL_NS}}}Relationship")
        rel.set("Id", f"rIdBWPM{n}")
        rel.set("Type", CUSTOM_XML_REL)
        rel.set("Target", "../customXml/bookwriter-page-map.xml")
        rels_payload = etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone="yes")
        with ZipFile(temp, "w", ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename in {MAP_PATH, rels_path}:
                    continue
                dst.writestr(info, src.read(info.filename))
            dst.writestr(rels_path, rels_payload)
            dst.writestr(MAP_PATH, payload)
    os.replace(temp, docx_path)
