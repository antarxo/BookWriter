#!/usr/bin/env python3
"""Word-to-Word canonicalizer for the BookWriter canonical Word profile.

Purpose
-------
Convert either a regular Word document or the reconstructor's page-shaped
Word document into the single BookWriter canonical Word profile without rasterizing content:

* regular DOCX: preserve its native Word structure and add the canonical profile marker;
* reconstructed DOCX: keep one native Word section per source page;
* keep floating drawings, frames and actual dimensions unchanged;
* replace only extreme reconstruction margins with paragraph indents/spacing;
* emit a detailed JSON report and an explicit DOCX custom-property marker.

This is the only DOCX gateway for the BookWriter import profile.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from copy import deepcopy
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
V = "urn:schemas-microsoft-com:vml"
CP = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"

NS = {"w": W, "wp": WP, "a": A, "dgm": DGM, "wps": WPS, "wpg": WPG, "mc": MC, "v": V, "cp": CP, "vt": VT}
qn = lambda ns, name: f"{{{ns}}}{name}"


@dataclass
class SectionReport:
    source_section: int
    output_section: int
    paragraph_count: int
    flow_paragraph_count: int
    framed_paragraph_count: int
    page_anchor_paragraph_count: int
    inline_drawing_count: int
    floating_drawing_count: int
    columns: int
    old_margins_twips: dict[str, int]
    new_margins_twips: dict[str, int]
    added_indent_twips: dict[str, int]
    added_first_spacing_before_twips: int
    status: str
    notes: list[str]


def int_attr(el: etree._Element | None, name: str, default: int = 0) -> int:
    if el is None:
        return default
    raw = el.get(qn(W, name))
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def set_int_attr(el: etree._Element, name: str, value: int) -> None:
    el.set(qn(W, name), str(max(0, int(value))))


def ensure_child(parent: etree._Element, tag: str, before_tags: tuple[str, ...] = ()) -> etree._Element:
    child = parent.find(tag, NS)
    if child is not None:
        return child
    child = etree.Element(qn(W, tag.split(":", 1)[1]))
    if before_tags:
        before_qnames = {qn(W, x.split(":", 1)[1]) for x in before_tags}
        for i, existing in enumerate(parent):
            if existing.tag in before_qnames:
                parent.insert(i, child)
                return child
    parent.append(child)
    return child


def paragraph_ppr(p: etree._Element) -> etree._Element:
    ppr = p.find("w:pPr", NS)
    if ppr is None:
        ppr = etree.Element(qn(W, "pPr"))
        p.insert(0, ppr)
    return ppr


def add_to_indentation(p: etree._Element, left_delta: int, right_delta: int) -> None:
    if left_delta == 0 and right_delta == 0:
        return
    ppr = paragraph_ppr(p)
    ind = ppr.find("w:ind", NS)
    if ind is None:
        ind = etree.Element(qn(W, "ind"))
        # Ordering: ind normally precedes spacing/justification/rPr.
        inserted = False
        for i, existing in enumerate(ppr):
            if existing.tag in {qn(W, "spacing"), qn(W, "jc"), qn(W, "rPr"), qn(W, "sectPr")}:
                ppr.insert(i, ind)
                inserted = True
                break
        if not inserted:
            ppr.append(ind)
    if left_delta:
        current = int_attr(ind, "left", int_attr(ind, "start", 0))
        ind.attrib.pop(qn(W, "start"), None)
        set_int_attr(ind, "left", current + left_delta)
    if right_delta:
        current = int_attr(ind, "right", int_attr(ind, "end", 0))
        ind.attrib.pop(qn(W, "end"), None)
        set_int_attr(ind, "right", current + right_delta)


def add_spacing_before(p: etree._Element, delta: int) -> None:
    if delta <= 0:
        return
    ppr = paragraph_ppr(p)
    spacing = ppr.find("w:spacing", NS)
    if spacing is None:
        spacing = etree.Element(qn(W, "spacing"))
        inserted = False
        for i, existing in enumerate(ppr):
            if existing.tag in {qn(W, "jc"), qn(W, "rPr"), qn(W, "sectPr")}:
                ppr.insert(i, spacing)
                inserted = True
                break
        if not inserted:
            ppr.append(spacing)
    current = int_attr(spacing, "before", 0)
    set_int_attr(spacing, "before", current + delta)


def paragraph_is_frame(p: etree._Element) -> bool:
    return p.find("./w:pPr/w:framePr", NS) is not None


def paragraph_is_page_anchor_only(p: etree._Element) -> bool:
    """True for a drawing-anchor carrier paragraph with no visible flow content."""
    anchors = p.findall(".//wp:anchor", NS)
    if not anchors:
        return False
    text = "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
    inlines = p.findall(".//wp:inline", NS)
    # Breaks/tabs can still make a paragraph visible; keep those in flow.
    visible_controls = p.findall(".//w:br", NS) or p.findall(".//w:tab", NS)
    return not text and not inlines and not visible_controls


def paragraph_has_flow_content(p: etree._Element) -> bool:
    if paragraph_is_frame(p) or paragraph_is_page_anchor_only(p):
        return False
    # Empty section-break carrier paragraphs should not receive geometry.
    text = "".join(p.xpath(".//w:t/text()", namespaces=NS)).strip()
    drawings = p.findall(".//wp:inline", NS)
    equations = p.findall(".//m:oMath", {"m": "http://schemas.openxmlformats.org/officeDocument/2006/math"})
    return bool(text or drawings or equations)


def split_sections(body: etree._Element) -> list[tuple[list[etree._Element], etree._Element, str]]:
    sections: list[tuple[list[etree._Element], etree._Element, str]] = []
    current: list[etree._Element] = []
    for el in body:
        current.append(el)
        if el.tag == qn(W, "p"):
            sect = el.find("./w:pPr/w:sectPr", NS)
            if sect is not None:
                sections.append((current, sect, "paragraph"))
                current = []
        elif el.tag == qn(W, "sectPr"):
            sections.append((current[:-1], el, "body"))
            current = []
    if current:
        raise ValueError(f"Unterminated body content after last section: {len(current)} elements")
    return sections


def parse_section_range(spec: str | None, total: int) -> list[int]:
    if not spec or spec.lower() in {"all", "*"}:
        return list(range(1, total + 1))
    selected: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            start, end = int(a), int(b)
            if start > end:
                start, end = end, start
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))
    invalid = sorted(i for i in selected if i < 1 or i > total)
    if invalid:
        raise ValueError(f"Section selection outside 1..{total}: {invalid}")
    return sorted(selected)


def columns_count(sect: etree._Element) -> int:
    cols = sect.find("w:cols", NS)
    if cols is None:
        return 1
    return max(1, int_attr(cols, "num", 1))


def ensure_next_page(sect: etree._Element) -> None:
    stype = sect.find("w:type", NS)
    if stype is None:
        stype = etree.Element(qn(W, "type"))
        sect.insert(0, stype)
    stype.set(qn(W, "val"), "nextPage")


def normalize_section(
    elements: list[etree._Element],
    sect: etree._Element,
    source_index: int,
    output_index: int,
    base_margin: int,
    normalize_multicolumn: bool,
) -> SectionReport:
    pg_mar = sect.find("w:pgMar", NS)
    if pg_mar is None:
        pg_mar = etree.SubElement(sect, qn(W, "pgMar"))
    old = {name: int_attr(pg_mar, name, base_margin) for name in ("top", "right", "bottom", "left")}
    col_count = columns_count(sect)

    paragraphs = [el for el in elements if el.tag == qn(W, "p")]
    flow = [p for p in paragraphs if paragraph_has_flow_content(p)]
    frame_count = sum(1 for p in paragraphs if paragraph_is_frame(p))
    anchor_carrier_count = sum(1 for p in paragraphs if paragraph_is_page_anchor_only(p))
    inline_count = sum(len(el.findall(".//wp:inline", NS)) for el in elements)
    floating_count = sum(len(el.findall(".//wp:anchor", NS)) for el in elements)
    notes: list[str] = []

    if col_count > 1 and not normalize_multicolumn:
        ensure_next_page(sect)
        notes.append("Multi-column section preserved unchanged; not normalized in this probe.")
        return SectionReport(
            source_index, output_index, len(paragraphs), len(flow), frame_count,
            anchor_carrier_count, inline_count, floating_count, col_count,
            old, old.copy(), {"left": 0, "right": 0}, 0,
            "preserved-multicolumn", notes,
        )

    # Use one ordinary page box. Preserve header/footer/gutter values.
    new = {"top": base_margin, "right": base_margin, "bottom": base_margin, "left": base_margin}
    for name, value in new.items():
        set_int_attr(pg_mar, name, value)

    left_delta = max(0, old["left"] - new["left"])
    right_delta = max(0, old["right"] - new["right"])
    top_delta = max(0, old["top"] - new["top"])

    for p in flow:
        add_to_indentation(p, left_delta, right_delta)
    if flow:
        add_spacing_before(flow[0], top_delta)
    elif top_delta:
        notes.append("No flow paragraph found; top offset was not materialized.")

    ensure_next_page(sect)
    notes.append("Page-relative wp:anchor and w:framePr geometry preserved verbatim.")
    notes.append("Bottom margin was normalized without an explicit trailing spacer.")
    if old["left"] < base_margin or old["right"] < base_margin or old["top"] < base_margin:
        notes.append("A source margin was smaller than the canonical base; negative compensation was intentionally not applied.")

    return SectionReport(
        source_index, output_index, len(paragraphs), len(flow), frame_count,
        anchor_carrier_count, inline_count, floating_count, col_count,
        old, new, {"left": left_delta, "right": right_delta}, top_delta,
        "normalized", notes,
    )


def read_custom_properties(parts_dir: Path) -> dict[str, str]:
    custom = parts_dir / "docProps" / "custom.xml"
    if not custom.exists():
        return {}
    root = etree.parse(str(custom)).getroot()
    result: dict[str, str] = {}
    for prop in root.findall(qn(CP, "property")):
        name = prop.get("name", "")
        child = next(iter(prop), None)
        if name and child is not None:
            result[name] = child.text or ""
    return result


def add_custom_property(parts_dir: Path, name: str, value: str) -> None:
    custom = parts_dir / "docProps" / "custom.xml"
    custom.parent.mkdir(parents=True, exist_ok=True)
    if custom.exists():
        root = etree.parse(str(custom)).getroot()
    else:
        root = etree.Element(qn(CP, "Properties"), nsmap={None: CP, "vt": VT})
    existing = root.xpath(f"./cp:property[@name={json.dumps(name)}]", namespaces={"cp": CP})
    if existing:
        prop = existing[0]
        for child in list(prop):
            prop.remove(child)
    else:
        pids = []
        for prop0 in root.findall(qn(CP, "property")):
            try:
                pids.append(int(prop0.get("pid", "1")))
            except ValueError:
                pass
        prop = etree.SubElement(root, qn(CP, "property"))
        prop.set("fmtid", "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}")
        prop.set("pid", str(max(pids, default=1) + 1))
        prop.set("name", name)
    child = etree.SubElement(prop, qn(VT, "lpwstr"))
    child.text = value
    custom.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes"))

    # Root relationship.
    rels_path = parts_dir / "_rels" / ".rels"
    rels_root = etree.parse(str(rels_path)).getroot()
    rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
    if not any(r.get("Type") == rel_type for r in rels_root):
        ids = {r.get("Id") for r in rels_root}
        n = 1
        while f"rId{n}" in ids:
            n += 1
        rel = etree.SubElement(rels_root, qn(REL, "Relationship"))
        rel.set("Id", f"rId{n}")
        rel.set("Type", rel_type)
        rel.set("Target", "docProps/custom.xml")
        rels_path.write_bytes(etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone="yes"))

    # Content type override.
    ct_path = parts_dir / "[Content_Types].xml"
    ct_root = etree.parse(str(ct_path)).getroot()
    part_name = "/docProps/custom.xml"
    if not any(x.get("PartName") == part_name for x in ct_root):
        ov = etree.SubElement(ct_root, qn(CT, "Override"))
        ov.set("PartName", part_name)
        ov.set("ContentType", "application/vnd.openxmlformats-officedocument.custom-properties+xml")
        ct_path.write_bytes(etree.tostring(ct_root, xml_declaration=True, encoding="UTF-8", standalone="yes"))


def rezip(parts_dir: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output_path, "w", ZIP_DEFLATED) as z:
        for path in sorted(parts_dir.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(parts_dir).as_posix())


def _section_margins(sect: etree._Element, default: int = 1440) -> dict[str, int]:
    pg_mar = sect.find("w:pgMar", NS)
    return {name: int_attr(pg_mar, name, default) for name in ("top", "right", "bottom", "left")}


def _document_signature(root: etree._Element, sections: list[tuple[list[etree._Element], etree._Element, str]], custom: dict[str, str], base_margin: int) -> dict:
    frame_count = len(root.findall(".//w:framePr", NS))
    floating_count = len(root.findall(".//wp:anchor", NS))
    inline_count = len(root.findall(".//wp:inline", NS))
    text_box_count = len(root.findall(".//w:txbxContent", NS))
    vml_count = len(root.findall(".//w:pict", NS))
    vml_group_count = len(root.findall(".//v:group", NS))
    textbox_table_count = len(root.xpath(".//w:txbxContent//w:tbl", namespaces=NS))
    alternate_content_count = len(root.findall(".//mc:AlternateContent", NS))
    group_count = len(root.findall(".//a:grpSp", NS)) + len(root.findall(".//wpg:wgp", NS))
    smartart_count = len(root.findall(".//dgm:relIds", NS))
    linked_textbox_count = len(root.findall(".//wps:linkedTxbx", NS))
    margins = [_section_margins(sect) for _elements, sect, _kind in sections]
    extreme_limit = max(1800, int(base_margin * 2.5))
    extreme_sections = sum(
        1 for m in margins
        if any(m[name] > extreme_limit for name in ("top", "left", "right"))
    )
    explicit_reconstructor = any(
        str(custom.get(key, "")).strip()
        for key in ("PdfWordReconstructorMode", "ReconstructionMode", "PdfWordReconstructorVersion")
    )
    reconstructed = bool(
        explicit_reconstructor
        or (len(sections) > 1 and extreme_sections >= max(1, len(sections) // 3) and (frame_count + floating_count) > 0)
    )
    unsupported = {
        "smartArt": smartart_count,
        "groupedDrawingML": group_count,
        "linkedTextBoxes": linked_textbox_count,
        "vmlPicturesOrShapes": vml_count,
        "groupedVML": vml_group_count,
        "tablesInsideTextBoxes": textbox_table_count,
        "alternateContent": alternate_content_count,
    }
    warnings = []
    if smartart_count:
        warnings.append(f"SmartArt objects detected: {smartart_count}; they are preserved in DOCX but not guaranteed editable in BookWriter.")
    if group_count:
        warnings.append(f"Grouped DrawingML objects detected: {group_count}; preserved native for the HF8 browser SVG/editable-overlay/anchor-flow probe.")
    if linked_textbox_count:
        warnings.append(f"Linked text boxes detected: {linked_textbox_count}; text-chain behavior is not implemented.")
    if vml_count:
        warnings.append(f"VML picture/shape containers detected: {vml_count}.")
    if vml_group_count:
        warnings.append(f"VML groups detected: {vml_group_count}; preserved as compatibility markup. HF8 does not rasterize them or use them to hide DrawingML failures.")
    if textbox_table_count:
        warnings.append(f"Tables inside text boxes detected: {textbox_table_count}; plain table-only text boxes remain native semantic tables with the outer Around geometry.")
    return {
        "sectionCount": len(sections),
        "frameCount": frame_count,
        "floatingDrawingCount": floating_count,
        "inlineDrawingCount": inline_count,
        "textBoxCount": text_box_count,
        "tablesInsideTextBoxes": textbox_table_count,
        "groupedVML": vml_group_count,
        "alternateContent": alternate_content_count,
        "extremeMarginSections": extreme_sections,
        "explicitReconstructorMarker": explicit_reconstructor,
        "reconstructedLayout": reconstructed,
        "unsupported": unsupported,
        "warnings": warnings,
    }


def _copy_selected_sections(body: etree._Element, sections: list[tuple[list[etree._Element], etree._Element, str]], selected: list[int]) -> list[tuple[list[etree._Element], etree._Element, str, int]]:
    for child in list(body):
        body.remove(child)
    copied: list[tuple[list[etree._Element], etree._Element, str, int]] = []
    for source_idx in selected:
        src_elements, src_sect, kind = sections[source_idx - 1]
        copied_elements = [deepcopy(el) for el in src_elements]
        if kind == "paragraph":
            copied_sect = copied_elements[-1].find("./w:pPr/w:sectPr", NS)
        else:
            copied_sect = deepcopy(src_sect)
        if copied_sect is None:
            raise ValueError(f"Copied section {source_idx} lost its sectPr")
        for el in copied_elements:
            body.append(el)
        if kind == "body":
            body.append(copied_sect)
        copied.append((copied_elements, copied_sect, kind, source_idx))
    return copied


def normalize_docx(args: argparse.Namespace) -> dict:
    input_path = Path(args.input).resolve()
    output_path = Path(args.output).resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    if input_path == output_path:
        raise ValueError("Input and output DOCX must be different files")

    with tempfile.TemporaryDirectory(prefix="word_to_word_") as td:
        parts = Path(td) / "parts"
        parts.mkdir()
        with ZipFile(input_path) as z:
            z.extractall(parts)
        document_path = parts / "word" / "document.xml"
        if not document_path.exists():
            raise ValueError("The DOCX has no word/document.xml")
        tree = etree.parse(str(document_path))
        root = tree.getroot()
        body = root.find(qn(W, "body"))
        if body is None:
            raise ValueError("word/document.xml has no w:body")
        sections = split_sections(body)
        selected = parse_section_range(args.sections, len(sections))
        custom_before = read_custom_properties(parts)
        signature = _document_signature(root, sections, custom_before, args.base_margin)
        already_canonical = custom_before.get("BookWriterImportMode", "").strip() == "canonical-word-v1"
        all_selected = selected == list(range(1, len(sections) + 1))

        strategy = str(getattr(args, "strategy", "auto") or "auto")
        if strategy == "auto":
            strategy = "reconstructed" if signature["reconstructedLayout"] else "standard"
        if strategy not in {"standard", "reconstructed"}:
            raise ValueError(f"Unknown normalization strategy: {strategy}")

        reports: list[SectionReport] = []
        action = "already-canonical"
        if already_canonical and all_selected:
            # Keep byte-level content as stable as possible; only the report is new.
            action = "already-canonical"
        else:
            copied = _copy_selected_sections(body, sections, selected)
            if strategy == "reconstructed":
                action = "normalized-reconstructed"
                for output_idx, (copied_elements, copied_sect, _kind, source_idx) in enumerate(copied, 1):
                    reports.append(normalize_section(
                        copied_elements,
                        copied_sect,
                        source_idx,
                        output_idx,
                        args.base_margin,
                        args.normalize_multicolumn,
                    ))
            else:
                action = "profiled-standard"
                for output_idx, (copied_elements, copied_sect, _kind, source_idx) in enumerate(copied, 1):
                    paragraphs = [el for el in copied_elements if el.tag == qn(W, "p")]
                    reports.append(SectionReport(
                        source_idx, output_idx, len(paragraphs),
                        sum(1 for p in paragraphs if paragraph_has_flow_content(p)),
                        sum(1 for p in paragraphs if paragraph_is_frame(p)),
                        sum(1 for p in paragraphs if paragraph_is_page_anchor_only(p)),
                        sum(len(el.findall(".//wp:inline", NS)) for el in copied_elements),
                        sum(len(el.findall(".//wp:anchor", NS)) for el in copied_elements),
                        columns_count(copied_sect),
                        _section_margins(copied_sect), _section_margins(copied_sect),
                        {"left": 0, "right": 0}, 0,
                        "profiled-standard",
                        ["Native Word section geometry preserved unchanged."],
                    ))

            document_path.write_bytes(etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes"))
            add_custom_property(parts, "BookWriterImportMode", "canonical-word-v1")
            add_custom_property(parts, "BookWriterCanonicalizationMode", action)
            add_custom_property(parts, "BookWriterSourceSections", args.sections or "all")
            add_custom_property(parts, "BookWriterNormalizerVersion", "0.4.7e-hf9-direct-visual-selection")
            rezip(parts, output_path)

        if already_canonical and all_selected:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(input_path.read_bytes())

    result = {
        "type": "implementation checkpoint / canonical Word input gateway",
        "version": "0.4.7e-hf9-direct-visual-selection",
        "action": action,
        "strategy": strategy,
        "input": str(input_path),
        "output": str(output_path),
        "input_section_count": len(sections),
        "selected_sections": selected,
        "output_section_count": len(selected),
        "base_margin_twips": args.base_margin,
        "normalize_multicolumn": args.normalize_multicolumn,
        "signature": signature,
        "sections": [asdict(r) for r in reports],
        "implemented": [
            "regular DOCX is accepted and profiled without reconstruction-layout rewrites",
            "reconstructed DOCX is normalized with the Word-native section/indent path",
            "already canonical DOCX is passed through unchanged",
            "one explicit canonical-word-v1 contract for the BookWriter importer",
            "unsupported complex objects are reported instead of silently hidden",
        ],
        "temporary_or_incomplete": [
            "no full Word contour-wrap equivalence",
            "HF8 native DrawingML groups are preserved for browser SVG rendering; unsupported geometry must remain visibly diagnostic",
            "paragraph-relative anchor rendering is resolved by BookWriter, not rewritten by this normalizer",
            "Microsoft Word COM repagination is not performed by the core gateway",
        ],
    }
    report_path = Path(args.report).resolve() if args.report else output_path.with_suffix(".normalization_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["report"] = str(report_path)
    return result

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Normalize a supported DOCX into the Word-native BookWriter canonical profile.")
    p.add_argument("input", help="Input DOCX: regular Word or reconstructed Word")
    p.add_argument("output", help="Canonicalized output DOCX")
    p.add_argument("--sections", default="all", help="Section range, e.g. 1-8 or 1,3,7-9 (default: all)")
    p.add_argument("--base-margin", type=int, default=720, help="Canonical page margin in twips (default: 720 = 0.5 in)")
    p.add_argument("--normalize-multicolumn", action="store_true", help="Also apply margin-to-indent normalization to multi-column reconstructed sections")
    p.add_argument("--strategy", choices=("auto", "standard", "reconstructed"), default="auto", help="Input adaptation strategy (default: auto)")
    p.add_argument("--report", help="JSON report path")
    return p


def main() -> int:
    args = build_parser().parse_args()
    result = normalize_docx(args)
    print(json.dumps({k: result[k] for k in ("type", "version", "output", "output_section_count", "report")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
