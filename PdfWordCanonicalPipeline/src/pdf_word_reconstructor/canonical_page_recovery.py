from __future__ import annotations

from typing import Any

VERSION = "canonical-page-recovery-0.1"


def recover_blocked_pages(report: dict[str, Any], line_map: dict[str, Any]) -> dict[str, Any]:
    """Attach renderer-neutral second-pass recovery evidence.

    Recovery is allowed only for pages whose body evidence is blocked but whose
    compatible frame-family margin evidence is already resolved. No header/footer
    semantics and no Word realization are inferred here.
    """
    page_map = {
        int(page.get("page") or 0): page
        for page in line_map.get("pages", []) or []
        if int(page.get("page") or 0) > 0
    }
    recovered = 0
    for row in report.get("pages", []) or []:
        body = row.get("bodyEvidence") or {}
        margin = row.get("marginEvidence") or {}
        recovery: dict[str, Any] = {"status": "not-needed", "wordRealization": None}
        if body.get("status") != "observed":
            recovery = {"status": "blocked", "wordRealization": None}
            eligible = (
                margin.get("status") == "resolved-inherited-frame"
                and margin.get("source") == "frame-family-profile"
                and margin.get("confidence") == "medium"
                and not (row.get("conflicts") or [])
            )
            page = page_map.get(int(row.get("page") or 0)) or {}
            try:
                width = float(page.get("page_width_px") or 0)
                height = float(page.get("page_height_px") or 0)
                margins = margin.get("normalizedMargins") or {}
                left = float(margins["left"]); right = float(margins["right"])
                top = float(margins["top"]); bottom = float(margins["bottom"])
            except (KeyError, TypeError, ValueError):
                eligible = False
                width = height = 0.0
            if eligible and width > 0 and height > 0:
                frame = [left * width, top * height, width - right * width, height - bottom * height]
                if frame[2] > frame[0] and frame[3] > frame[1]:
                    recovery = {
                        "status": "recovered-from-compatible-frame",
                        "confidence": "medium",
                        "bodyConstraintPx": frame,
                        "source": "frame-family-profile",
                        "headerSemanticStatus": "unresolved",
                        "footerSemanticStatus": "unresolved",
                        "crossZoneReadingOrder": "unresolved",
                        "wordRealization": None,
                        "policy": "inherited frame is used only as a geometric constraint; furniture semantics remain unresolved",
                    }
                    recovered += 1
        row["profileRecoveryEvidence"] = recovery
    report["profileRecovery"] = {
        "version": VERSION,
        "recoveredPageCount": recovered,
        "policy": "second-pass geometry only; no page_structure mutation and no Word realization",
    }
    return report


__all__ = ["recover_blocked_pages"]
