from __future__ import annotations

import re
from typing import Any

from .markdown_pdf_spine_v17 import build_markdown_pdf_spine as _build_v17

VERSION = "markdown-pdf-spine-0.18"


def _compact(value: Any, limit: int = 72) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_markdown_pdf_spine(markdown_element_map: dict[str, Any] | None, pdf_analysis: dict[str, Any]) -> dict[str, Any]:
    result = _build_v17(markdown_element_map, pdf_analysis)
    diagnostics = result.get("postV16Diagnostics") if isinstance(result.get("postV16Diagnostics"), dict) else {}

    compatibility_items: list[dict[str, Any]] = []
    for row in diagnostics.get("items") or []:
        candidates = row.get("topCandidates") or []
        best = candidates[0] if candidates else {}
        second = candidates[1] if len(candidates) > 1 else {}
        owner = best.get("owner") if isinstance(best.get("owner"), dict) else {}
        owner_text = f"used->{owner.get('markdownId')}" if owner else "unused"
        if best:
            reason = (
                f"post-v16-unresolved best=p{best.get('candidatePage') or 0} "
                f"d={best.get('pageDelta')} {best.get('semanticType') or '∅'} "
                f"{owner_text} exact={bool(best.get('exactSkeleton'))} "
                f"text={_compact(best.get('text'))!r}"
            )
        else:
            reason = "post-v16-unresolved no-candidate-in-p±1"
        compatibility_items.append({
            "markdownId": row.get("markdownId"),
            "page": row.get("hintedPage"),
            "reason": reason,
            "candidateCount": len(candidates),
            "bestScore": best.get("score") if best else None,
            "secondScore": second.get("score") if second else None,
        })

    count = int(diagnostics.get("count") or 0)
    result["version"] = VERSION
    result["neighborBoundedDiagnostics"] = {
        "count": count,
        "reasonCounts": {"post-v16-unresolved": count} if count else {},
        "items": compatibility_items,
    }
    result["postV16CompactDiagnostics"] = {
        "count": count,
        "items": compatibility_items[:120],
    }
    return result


__all__ = ["build_markdown_pdf_spine"]
