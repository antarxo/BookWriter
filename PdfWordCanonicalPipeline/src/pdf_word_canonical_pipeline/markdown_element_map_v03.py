from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .markdown_element_map import extract_markdown_element_map as _extract_v02


VERSION = "markdown-element-map-0.3"


def _plain_text(raw: str) -> str:
    text = str(raw or "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2) or "", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)", lambda m: m.group(1) or m.group(2) or "", text)
    text = re.sub(r"^\s*#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", text, flags=re.M)
    text = re.sub(r"\\(?:section\*?|subsection\*?|subsubsection\*?|title|author|caption)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\(?:begin|end)\{[^{}]+\}", " ", text)
    text = re.sub(r"\\item(?:\[[^\]]*\])?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _inline_tokens(raw: str) -> list[dict[str, Any]]:
    patterns = [
        ("strong", re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")),
        ("emphasis", re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)|(?<!_)_([^_]+)_(?!_)")),
        ("inline_math", re.compile(r"(?<!\$)\$([^\n$]+)\$(?!\$)")),
        ("code", re.compile(r"`([^`]+)`")),
        ("link", re.compile(r"\[([^\]]+)\]\(([^)]+)\)")),
    ]
    tokens: list[dict[str, Any]] = []
    for kind, pattern in patterns:
        for match in pattern.finditer(raw or ""):
            token: dict[str, Any] = {
                "type": kind,
                "start": match.start(),
                "end": match.end(),
                "raw": match.group(0),
            }
            if kind == "link":
                token["text"] = match.group(1)
                token["target"] = match.group(2)
            else:
                token["text"] = next((group for group in match.groups() if group is not None), "")
            tokens.append(token)
    return sorted(tokens, key=lambda item: (int(item["start"]), int(item["end"])))


def _markdown_list_payload(raw: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for line_index, line in enumerate((raw or "").splitlines()):
        match = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if not match:
            continue
        marker = match.group(2)
        ordered = bool(re.match(r"\d", marker))
        number_match = re.match(r"(\d+)", marker)
        items.append({
            "index": len(items),
            "sourceLine": line_index,
            "level": max(0, len(match.group(1).replace("\t", "    ")) // 2),
            "ordered": ordered,
            "number": int(number_match.group(1)) if number_match else None,
            "marker": marker,
            "raw": match.group(3),
            "text": _plain_text(match.group(3)),
            "inline": _inline_tokens(match.group(3)),
        })
    return {"items": items, "itemCount": len(items)}


def _latex_list_payload(raw: str) -> dict[str, Any]:
    env = "enumerate" if "\\begin{enumerate}" in raw else "itemize"
    parts = re.split(r"\\item(?:\[([^\]]*)\])?", raw)
    items: list[dict[str, Any]] = []
    # re.split returns prefix, then label/body pairs because of the capture group.
    for index in range(1, len(parts), 2):
        label = parts[index]
        body = parts[index + 1] if index + 1 < len(parts) else ""
        body = re.sub(r"\\end\{(?:itemize|enumerate)\}.*$", "", body, flags=re.S).strip()
        if not body:
            continue
        items.append({
            "index": len(items),
            "ordered": env == "enumerate",
            "label": label or None,
            "raw": body,
            "text": _plain_text(body),
            "inline": _inline_tokens(body),
        })
    return {"environment": env, "items": items, "itemCount": len(items)}


def _markdown_table_payload(raw: str) -> dict[str, Any]:
    lines = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return {"rows": [], "rowCount": 0, "columnCount": 0}

    def cells(line: str) -> list[str]:
        value = line.strip().strip("|")
        return [cell.strip() for cell in value.split("|")]

    header = cells(lines[0])
    separator = cells(lines[1])
    alignments: list[str | None] = []
    for cell in separator:
        value = cell.strip()
        if value.startswith(":") and value.endswith(":"):
            alignments.append("center")
        elif value.endswith(":"):
            alignments.append("right")
        elif value.startswith(":"):
            alignments.append("left")
        else:
            alignments.append(None)
    body = [cells(line) for line in lines[2:]]
    rows = [header, *body]
    return {
        "header": header,
        "alignments": alignments,
        "rows": rows,
        "rowCount": len(rows),
        "columnCount": max((len(row) for row in rows), default=0),
    }


def _latex_table_payload(raw: str) -> dict[str, Any]:
    match = re.search(r"\\begin\{tabular\}(?:\[[^\]]*\])?\{([^{}]*)\}(.*?)\\end\{tabular\}", raw or "", re.S)
    if not match:
        return {"rawTabular": raw, "rows": [], "rowCount": 0}
    column_spec = match.group(1)
    body = match.group(2)
    rows: list[list[str]] = []
    for row in re.split(r"\\\\", body):
        row = re.sub(r"\\hline", "", row).strip()
        if not row:
            continue
        rows.append([cell.strip() for cell in row.split("&")])
    return {
        "columnSpec": column_spec,
        "rows": rows,
        "rowCount": len(rows),
        "columnCount": max((len(row) for row in rows), default=0),
    }


def _semantic_payload(record: dict[str, Any], raw: str) -> dict[str, Any]:
    kind = str(record.get("type") or "paragraph")
    payload: dict[str, Any] = {
        "type": kind,
        "rawMarkdown": raw,
        "plainText": _plain_text(raw),
        "inline": _inline_tokens(raw),
    }
    if kind in {"heading", "title"}:
        payload["level"] = record.get("level")
        payload["text"] = str(record.get("text") or payload["plainText"])
    elif kind == "display_equation":
        payload["latex"] = str(record.get("latex") or "")
        payload["signature"] = record.get("signature")
    elif kind == "image":
        payload["target"] = record.get("target")
        payload["alt"] = record.get("alt")
    elif kind == "figure":
        payload["imageTargets"] = list(record.get("imageTargets") or [])
        payload["captionText"] = str(record.get("captionText") or "")
    elif kind == "list":
        payload.update(_markdown_list_payload(raw))
    elif kind == "latex_list":
        payload.update(_latex_list_payload(raw))
    elif kind == "table":
        payload.update(_markdown_table_payload(raw))
    elif kind == "latex_table":
        payload.update(_latex_table_payload(raw))
    elif kind == "caption":
        payload["text"] = str(record.get("text") or payload["plainText"])
    return payload


def _enrich(result: dict[str, Any]) -> dict[str, Any]:
    source_cache: dict[str, str] = {}
    raw_count = 0
    semantic_count = 0
    for record in result.get("records", []) or []:
        source = str(record.get("source") or "")
        if source not in source_cache:
            try:
                source_cache[source] = Path(source).read_text(encoding="utf-8", errors="replace")
            except Exception:
                source_cache[source] = ""
        markdown = source_cache[source]
        try:
            start = int(record.get("offset") or 0)
            end = int(record.get("endOffset") or start)
        except (TypeError, ValueError):
            start = end = 0
        raw = markdown[start:end] if markdown and 0 <= start <= end <= len(markdown) else ""
        record["rawMarkdown"] = raw
        record["authoritativeContent"] = _semantic_payload(record, raw)
        record["contentAuthority"] = "markdown"
        record["contentComplete"] = bool(raw) or bool(record.get("latex")) or bool(record.get("target"))
        if raw:
            raw_count += 1
        if record.get("authoritativeContent"):
            semantic_count += 1

    result["version"] = VERSION
    result["policy"] = {
        "contentAuthority": "markdown",
        "rawContent": "complete-source-slice-offset-to-endOffset",
        "previewRole": "diagnostic-only",
        "docxRole": "secondary-donor-only",
        "pdfRole": "layout-typography-witness-only",
    }
    result["authoritativeContentSummary"] = {
        "recordCount": len(result.get("records", []) or []),
        "rawMarkdownRecordCount": raw_count,
        "semanticPayloadRecordCount": semantic_count,
        "rawCoverage": round(raw_count / max(1, len(result.get("records", []) or [])), 5),
    }
    return result


def extract_markdown_element_map(
    markdown_files: list[Path],
    out_path: Path,
    docx_path: Path | None = None,
    attach_docx_evidence: bool = False,
) -> dict[str, Any]:
    result = _extract_v02(
        markdown_files,
        out_path,
        docx_path=docx_path,
        attach_docx_evidence=attach_docx_evidence,
    )
    result = _enrich(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
