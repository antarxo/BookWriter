from __future__ import annotations

"""Extract editable equation overlay metadata from grouped Word drawings.

The source DOCX is not modified here. The resulting manifest is consumed by the
Windows Word COM rasterizer, which creates a faithful background image of each
compound and stores the equation boxes as normalized overlays.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from collections import Counter
from zipfile import ZipFile, ZIP_DEFLATED

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"

NS = {"w": W, "m": M, "a": A, "wp": WP, "wpg": WPG, "wps": WPS, "mc": MC}
MANIFEST_PATH = "customXml/bookwriter-composites.json"


def _local(node: etree._Element | None) -> str:
    return etree.QName(node).localname if node is not None else ""


def _num(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return float(default)


@dataclass(frozen=True)
class Transform:
    sx: float = 1.0
    sy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        return self.tx + self.sx * x, self.ty + self.sy * y

    def compose(self, child: "Transform") -> "Transform":
        """Return self after child (self ∘ child)."""
        return Transform(
            sx=self.sx * child.sx,
            sy=self.sy * child.sy,
            tx=self.tx + self.sx * child.tx,
            ty=self.ty + self.sy * child.ty,
        )


def _direct_xfrm(node: etree._Element) -> etree._Element | None:
    name = _local(node)
    if name == "wgp":
        found = node.xpath("./wpg:grpSpPr/a:xfrm", namespaces=NS)
    elif name == "grpSp":
        found = node.xpath("./a:grpSpPr/a:xfrm", namespaces=NS)
    else:
        found = node.xpath("./wps:spPr/a:xfrm | ./a:spPr/a:xfrm | ./a:xfrm", namespaces=NS)
    return found[0] if found else None


def _xfrm_parts(xfrm: etree._Element | None) -> dict[str, tuple[float, float]]:
    out = {"off": (0.0, 0.0), "ext": (0.0, 0.0), "chOff": (0.0, 0.0), "chExt": (0.0, 0.0)}
    if xfrm is None:
        return out
    for child in xfrm:
        name = _local(child)
        if name in ("off", "chOff"):
            out[name] = (_num(child.get("x")), _num(child.get("y")))
        elif name in ("ext", "chExt"):
            out[name] = (_num(child.get("cx")), _num(child.get("cy")))
    return out


def _group_transform(node: etree._Element) -> Transform:
    p = _xfrm_parts(_direct_xfrm(node))
    off_x, off_y = p["off"]
    ext_x, ext_y = p["ext"]
    ch_x, ch_y = p["chOff"]
    ch_ext_x, ch_ext_y = p["chExt"]
    sx = ext_x / ch_ext_x if ch_ext_x else 1.0
    sy = ext_y / ch_ext_y if ch_ext_y else 1.0
    return Transform(sx=sx, sy=sy, tx=off_x - sx * ch_x, ty=off_y - sy * ch_y)


def _shape_box(node: etree._Element, parent: Transform) -> tuple[float, float, float, float] | None:
    p = _xfrm_parts(_direct_xfrm(node))
    x, y = p["off"]
    w, h = p["ext"]
    if w <= 0 or h <= 0:
        return None
    x0, y0 = parent.apply(x, y)
    x1, y1 = parent.apply(x + w, y + h)
    return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)


def _child_elements(node: etree._Element) -> Iterable[etree._Element]:
    for child in node:
        if _local(child) in {"cNvGrpSpPr", "grpSpPr"}:
            continue
        yield child


def _plain_math_text(math: etree._Element) -> str:
    return "".join(math.xpath(".//m:t/text()", namespaces=NS)).replace("\u00a0", " ").strip()


def _default_font_size_pt(styles_root: etree._Element | None) -> float:
    if styles_root is None:
        return 11.0
    values = styles_root.xpath(".//w:docDefaults//w:rPrDefault/w:rPr/w:sz/@w:val", namespaces=NS)
    for value in values:
        size = _num(value) / 2.0
        if 4.0 <= size <= 72.0:
            return size
    return 11.0


def _shape_font_size_pt(node: etree._Element, default: float) -> float:
    values = []
    for value in node.xpath(".//w:sz/@w:val | .//w:szCs/@w:val", namespaces=NS):
        size = _num(value) / 2.0
        if 4.0 <= size <= 72.0:
            values.append(round(size, 3))
    if not values:
        return default
    # Word frequently writes the same explicit size twice (sz + szCs).
    return float(Counter(values).most_common(1)[0][0])


def _paragraph_index(root: etree._Element, group: etree._Element) -> int | None:
    paras = root.xpath(".//w:body//w:p", namespaces=NS)
    parent = group.xpath("ancestor::w:p[1]", namespaces=NS)
    if not parent:
        return None
    try:
        return paras.index(parent[0]) + 1
    except ValueError:
        return None


def _anchor_info(group: etree._Element) -> dict[str, Any]:
    drawing = group.xpath("ancestor::w:drawing[1]", namespaces=NS)
    if not drawing:
        return {}
    host = drawing[0]
    anchor = host.xpath(".//wp:anchor[1] | .//wp:inline[1]", namespaces=NS)
    if not anchor:
        return {}
    anchor = anchor[0]
    extent = anchor.find(f"{{{WP}}}extent")
    pos_h = anchor.find(f"{{{WP}}}positionH")
    pos_v = anchor.find(f"{{{WP}}}positionV")

    def _position(node: etree._Element | None) -> dict[str, Any]:
        if node is None:
            return {}
        offset = node.find(f"{{{WP}}}posOffset")
        align = node.find(f"{{{WP}}}align")
        return {
            "relativeFrom": node.get("relativeFrom", ""),
            "offsetEmu": int(_num(offset.text)) if offset is not None else None,
            "align": (align.text or "").strip() if align is not None else "",
        }

    return {
        "kind": _local(anchor),
        "widthEmu": int(_num(extent.get("cx"))) if extent is not None else 0,
        "heightEmu": int(_num(extent.get("cy"))) if extent is not None else 0,
        "horizontal": _position(pos_h),
        "vertical": _position(pos_v),
    }


def _walk_group(
    node: etree._Element,
    parent: Transform,
    outer_box: tuple[float, float, float, float],
    overlays: list[dict[str, Any]],
    path: tuple[int, ...] = (),
    default_font_size_pt: float = 11.0,
) -> None:
    name = _local(node)
    if name in {"wgp", "grpSp"}:
        mapped = parent.compose(_group_transform(node))
        for index, child in enumerate(_child_elements(node), 1):
            _walk_group(child, mapped, outer_box, overlays, path + (index,), default_font_size_pt)
        return

    math_nodes = node.xpath(".//m:oMath", namespaces=NS)
    if not math_nodes:
        return
    box = _shape_box(node, parent)
    if box is None:
        return
    ox, oy, ow, oh = outer_box
    x, y, w, h = box
    if ow <= 0 or oh <= 0:
        return
    for index, math in enumerate(math_nodes, 1):
        # In the source corpus each equation-bearing shape contains one OMath.
        # If a future shape has more than one, divide the box vertically rather
        # than collapsing all equations onto the same coordinates.
        part_h = h / max(1, len(math_nodes))
        part_y = y + (index - 1) * part_h
        normalized = {
            "x": max(0.0, min(1.0, (x - ox) / ow)),
            "y": max(0.0, min(1.0, (part_y - oy) / oh)),
            "width": max(0.001, min(1.0, w / ow)),
            "height": max(0.001, min(1.0, part_h / oh)),
        }
        overlays.append({
            "id": "eq-" + "-".join(map(str, path + (index,))),
            "type": "equation",
            "geometry": normalized,
            "ommlXml": etree.tostring(math, encoding="unicode"),
            "plainText": _plain_math_text(math),
            "fontSizePt": _shape_font_size_pt(node, default_font_size_pt),
            "sourcePath": list(path),
            "mask": {"mode": "clean-background", "fallbackColor": "#FFFFFF"},
        })


def extract_composite_overlay_manifest(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    with ZipFile(path) as archive:
        raw = archive.read("word/document.xml")
        try:
            styles_raw = archive.read("word/styles.xml")
        except KeyError:
            styles_raw = b""
    root = etree.fromstring(raw)
    styles_root = etree.fromstring(styles_raw) if styles_raw else None
    default_font_size_pt = _default_font_size_pt(styles_root)
    groups = root.xpath(".//wpg:wgp", namespaces=NS)
    composites: dict[str, Any] = {}
    total_overlays = 0
    for index, group in enumerate(groups, 1):
        xfrm = _xfrm_parts(_direct_xfrm(group))
        outer_x, outer_y = xfrm["off"]
        outer_w, outer_h = xfrm["ext"]
        if outer_w <= 0 or outer_h <= 0:
            anchor = _anchor_info(group)
            outer_w = float(anchor.get("widthEmu") or 0)
            outer_h = float(anchor.get("heightEmu") or 0)
        if outer_w <= 0 or outer_h <= 0:
            continue
        overlays: list[dict[str, Any]] = []
        _walk_group(group, Transform(), (outer_x, outer_y, outer_w, outer_h), overlays, default_font_size_pt=default_font_size_pt)
        composite_id = f"bw-composite-{index:04d}"
        anchor = _anchor_info(group)
        row = {
            "id": composite_id,
            "sourceGroupIndex": index,
            "sourceParagraph": _paragraph_index(root, group),
            "widthPt": round((anchor.get("widthEmu") or outer_w) / 12700.0, 4),
            "heightPt": round((anchor.get("heightEmu") or outer_h) / 12700.0, 4),
            "anchor": anchor,
            "overlays": overlays,
            "equationOverlayCount": len(overlays),
            "backgroundClean": False,
            "status": "extracted" if overlays else "no-equation-overlays",
        }
        composites[composite_id] = row
        total_overlays += len(overlays)
    return {
        "version": 1,
        "sourceFile": path.name,
        "defaultFontSizePt": default_font_size_pt,
        "groupCount": len(groups),
        "compositeCount": len(composites),
        "equationOverlayCount": total_overlays,
        "composites": composites,
    }


def add_manifest_to_docx(path: Path, manifest: dict[str, Any]) -> None:
    """Add/replace the private BookWriter manifest without changing Word content."""
    path = Path(path).resolve()
    temp = path.with_suffix(path.suffix + ".manifest.tmp")
    payload = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with ZipFile(path, "r") as source, ZipFile(temp, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            if item.filename == MANIFEST_PATH:
                continue
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                root = etree.fromstring(data)
                has_json = any(
                    child.get("Extension", "").lower() == "json"
                    for child in root.findall(f"{{{CT}}}Default")
                )
                if not has_json:
                    etree.SubElement(root, f"{{{CT}}}Default", Extension="json", ContentType="application/json")
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone="yes")
            target.writestr(item, data)
        target.writestr(MANIFEST_PATH, payload)
    temp.replace(path)


def read_manifest_from_docx(path: Path) -> dict[str, Any] | None:
    with ZipFile(path) as archive:
        try:
            return json.loads(archive.read(MANIFEST_PATH).decode("utf-8"))
        except KeyError:
            return None


__all__ = [
    "MANIFEST_PATH",
    "extract_composite_overlay_manifest",
    "add_manifest_to_docx",
    "read_manifest_from_docx",
]
