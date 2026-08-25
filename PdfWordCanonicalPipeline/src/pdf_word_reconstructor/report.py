from __future__ import annotations

import html
import shutil
from pathlib import Path
from typing import Any

from .common import compact_text


def _esc(value: Any) -> str:
    return html.escape(str(value))


def build_html_report(
    report_dir: Path,
    work_dir: Path,
    pdf_analysis: dict[str, Any],
    docx_analysis: dict[str, Any],
    alignment: dict[str, Any],
    style_profile: dict[str, Any],
    classification_summary: dict[str, Any],
    page_structure: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    content_audit: dict[str, Any] | None = None,
    fidelity_fallback_report: dict[str, Any] | None = None,
    markdown_pdf_spine: dict[str, Any] | None = None,
    conversion_spine: dict[str, Any] | None = None,
    docx_donor_map: dict[str, Any] | None = None,
    architecture_benchmark: dict[str, Any] | None = None,
    architecture_guard: dict[str, Any] | None = None,
    mapping_fidelity: dict[str, Any] | None = None,
    page_layout_spine: dict[str, Any] | None = None,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    assets = report_dir / "assets"
    assets.mkdir(exist_ok=True)

    for page in pdf_analysis.get("pages", []):
        source = work_dir / page["render"]
        if source.exists():
            shutil.copy2(source, assets / source.name)

    matches_by_page: dict[int, list[dict[str, Any]]] = {}
    for match in alignment.get("matches", []):
        matches_by_page.setdefault(int(match["page"]), []).append(match)
    excluded_by_page: dict[int, list[dict[str, Any]]] = {}
    for item in alignment.get("excluded_regions", []):
        excluded_by_page.setdefault(int(item["page"]), []).append(item)

    structure_by_page = {int(p["page"]): p for p in (page_structure or {}).get("pages", [])}
    summary = alignment.get("summary", {})
    body_size = style_profile.get("inferred_body_font_size_pt")
    top_sizes = style_profile.get("font_sizes", [])[:10]
    class_counts = classification_summary.get("counts", {})
    calibration = calibration or {}
    content_audit = content_audit or {}
    fidelity_fallback_report = fidelity_fallback_report or {}
    markdown_pdf_spine = markdown_pdf_spine or {}
    conversion_spine = conversion_spine or {}
    docx_donor_map = docx_donor_map or {}
    architecture_benchmark = architecture_benchmark or {}
    architecture_guard = architecture_guard or {}
    mapping_fidelity = mapping_fidelity or {}
    page_layout_spine = page_layout_spine or {}

    chunks = ["""<!doctype html>
<html lang="el"><head><meta charset="utf-8"><title>PDF → DOCX diagnostic report v0.8.3 strict</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#202124;background:#f5f6f8}
h1,h2,h3{margin:.5em 0}.card{background:#fff;border:1px solid #d9dde3;border-radius:8px;padding:16px;margin:14px 0}
.grid{display:grid;grid-template-columns:minmax(360px,45%) 1fr;gap:18px}.pageimg{width:100%;border:1px solid #aaa}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{border:1px solid #d9dde3;padding:6px;vertical-align:top}th{background:#eef1f5}
.strong{background:#e7f6ea}.medium{background:#fff5d6}.weak{background:#fdebd0}.unresolved{background:#fde8e7}.excluded{background:#eceff3;color:#555}
code{background:#eef1f5;padding:2px 4px;border-radius:3px}.muted{color:#666}.mono{font-family:Consolas,monospace;white-space:pre-wrap}
.badge{display:inline-block;padding:2px 7px;border-radius:10px;background:#e8edf4;font-size:11px;margin-right:4px}.summary-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}.metric{background:#f7f9fb;border:1px solid #e0e4e9;padding:10px;border-radius:6px}.metric b{font-size:20px;display:block}
.ok{color:#136c2e;font-weight:700}.warn{color:#9a5b00;font-weight:700}.bad{color:#a40000;font-weight:700}
</style></head><body>"""]
    chunks.append("<h1>PDF-guided DOCX reconstruction - strict execution checkpoint v0.8.3</h1>")
    chunks.append(
        f"<div class='card'><b>PDF:</b> {_esc(pdf_analysis.get('source'))}<br>"
        f"<b>DOCX:</b> {_esc(docx_analysis.get('source'))}<br>"
        f"<b>Σελίδες:</b> {_esc(', '.join(map(str, pdf_analysis.get('selected_pages', []))))}<br>"
        f"<b>Μετρημένο σώμα PDF:</b> {_esc(body_size)} pt<br>"
        f"<b>Εύρος παραγράφων DOCX:</b> {_esc(summary.get('candidate_docx_paragraph_range'))}</div>"
    )

    if architecture_guard:
        guard_status = str(architecture_guard.get("status") or "unknown")
        guard_class = "ok" if guard_status == "pass" else "bad"
        chunks.append("<div class='card'><h2>Architecture guard</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b class='{guard_class}'>{_esc(guard_status)}</b>status</div>")
        chunks.append(f"<div class='metric'><b>{_esc(architecture_guard.get('violationCount', 0))}</b>violations</div>")
        chunks.append(f"<div class='metric'><b>{_esc(architecture_guard.get('warningCount', 0))}</b>warnings</div>")
        chunks.append("</div>")
        for title, rows, css in (
            ("Violations", architecture_guard.get("violations") or [], "bad"),
            ("Warnings", architecture_guard.get("warnings") or [], "warn"),
            ("Confirmations", architecture_guard.get("confirmations") or [], "ok"),
        ):
            if not rows:
                continue
            chunks.append(f"<h3 class='{css}'>{_esc(title)}</h3><table><tr><th>Code</th><th>Message</th><th>Data</th></tr>")
            for row in rows:
                data = {k: v for k, v in row.items() if k not in {"code", "message"}}
                chunks.append(
                    f"<tr><td><code>{_esc(row.get('code'))}</code></td>"
                    f"<td>{_esc(row.get('message'))}</td><td><code>{_esc(data)}</code></td></tr>"
                )
            chunks.append("</table>")
        chunks.append("<p class='muted'>Αυτός ο έλεγχος δεν βαθμολογεί την ποιότητα του DOCX. Ελέγχει αν η ροή έμεινε στη συμφωνημένη λογική: Markdown first, PDF οδηγός, DOCX δότης, output audit.</p></div>")

    if mapping_fidelity:
        mapping_status = str(mapping_fidelity.get("status") or "unknown")
        mapping_class = "ok" if mapping_status == "pass" else "bad"
        metrics = mapping_fidelity.get("metrics") or {}
        md_metrics = metrics.get("markdownPdf") or {}
        layout_metrics = metrics.get("pageLayout") or {}
        conversion_metrics = metrics.get("conversion") or {}
        chunks.append("<div class='card'><h2>Mapping fidelity gate</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b class='{mapping_class}'>{_esc(mapping_status)}</b>status</div>")
        chunks.append(f"<div class='metric'><b>{_esc(mapping_fidelity.get('violationCount', 0))}</b>violations</div>")
        chunks.append(f"<div class='metric'><b>{_esc(mapping_fidelity.get('warningCount', 0))}</b>warnings</div>")
        chunks.append(f"<div class='metric'><b>{_esc(md_metrics.get('coverage', '—'))}</b>Markdown→PDF coverage</div>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_metrics.get('contractCoverage', '—'))}</b>Layout contract</div>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_metrics.get('slotCollisionCount', '—'))}</b>slot collisions</div>")
        chunks.append(f"<div class='metric'><b>{_esc(conversion_metrics.get('coverage', '—'))}</b>Conversion coverage</div>")
        chunks.append("</div>")
        for title, rows, css in (
            ("Mapping violations", mapping_fidelity.get("violations") or [], "bad"),
            ("Mapping warnings", mapping_fidelity.get("warnings") or [], "warn"),
        ):
            if not rows:
                continue
            chunks.append(f"<h3 class='{css}'>{_esc(title)}</h3><table><tr><th>Code</th><th>Message</th><th>Data</th></tr>")
            for row in rows:
                data = {k: v for k, v in row.items() if k not in {"code", "message"}}
                chunks.append(
                    f"<tr><td><code>{_esc(row.get('code'))}</code></td>"
                    f"<td>{_esc(row.get('message'))}</td><td><code>{_esc(data)}</code></td></tr>"
                )
            chunks.append("</table>")
        chunks.append("<p class='muted'>Αυτός ο έλεγχος βαθμολογεί την πιστότητα των χαρτογραφήσεων. Αν αποτύχει, το run δεν πρέπει να συνεχίσει ως παραγωγή DOCX.</p></div>")

    chunks.append("<div class='card'><h2>Καθαρή εικόνα αντιστοίχισης</h2><div class='summary-grid'>")
    for label, key in (
        ("Ισχυρές", "strong"),
        ("Μέσες", "medium"),
        ("Ασθενείς", "weak"),
        ("Προς έλεγχο", "unresolved"),
        ("Εξαιρέθηκαν", "pdf_excluded_regions"),
    ):
        chunks.append(f"<div class='metric'><b>{_esc(summary.get(key, 0))}</b>{label}</div>")
    chunks.append("</div><p class='muted'>Ο builder της βάσης v0.7 χρησιμοποιεί strong/medium matches μόνο ως ασφαλή δομικά hints· χαμηλής βεβαιότητας μαθηματικά μένουν raster fallback αντί να αντιστοιχιστούν λάθος.</p></div>")

    if content_audit:
        audit_status = str(content_audit.get("status", "review"))
        audit_class = "bad" if audit_status == "content-critical" else ("ok" if audit_status == "content-usable" else "warn")
        chunks.append(
            f"<div class='card'><h2>Content audit</h2>"
            f"<p>Κατάσταση: <span class='{audit_class}'>{_esc(audit_status)}</span></p>"
            "<div class='summary-grid'>"
            f"<div class='metric'><b>{_esc(content_audit.get('source_to_output_pdf_token_coverage', '—'))}</b>PDF text coverage</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('source_to_output_docx_token_coverage', '—'))}</b>DOCX text coverage</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('likely_missing_source_line_count', len(content_audit.get('likely_missing_lines', []))))}</b>Πιθανές ελλείψεις</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('likely_extra_output_line_count', len(content_audit.get('likely_extra_output_lines', []))))}</b>Πιθανά σκουπίδια</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('formula_review_line_count', len(content_audit.get('formula_review_lines', []))))}</b>Τύποι/σύμβολα για έλεγχο</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('layout_join_artifact_count', len(content_audit.get('layout_join_artifacts', []))))}</b>Ύποπτες ενώσεις γραμμών</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('suspicious_glyphs', 0))}</b>Ύποπτα glyphs</div>"
            f"<div class='metric'><b>{_esc(content_audit.get('native_math_count', 0))} / {_esc(content_audit.get('raster_equation_fallbacks', 0))}</b>Native math / raster equations</div>"
            "</div>"
        )
        markdown_survival = content_audit.get("markdown_survival") or {}
        if markdown_survival:
            chunks.append("<h3>Markdown survival audit</h3><div class='summary-grid'>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_survival.get('coverage', '—'))}</b>coverage</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_survival.get('checked_count', 0))}</b>checked</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_survival.get('matched_count', 0))}</b>matched</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_survival.get('weak_count', 0))}</b>weak</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_survival.get('missing_count', 0))}</b>missing</div>")
            chunks.append("</div>")
            problems = markdown_survival.get("problemElements", [])[:20]
            if problems:
                chunks.append("<table><tr><th>Type</th><th>Page</th><th>Status</th><th>Score</th><th>Markdown</th></tr>")
                for item in problems:
                    chunks.append(
                        f"<tr><td>{_esc(item.get('type'))}</td><td>{_esc(item.get('page') or '')}</td>"
                        f"<td>{_esc(item.get('status') or '')}</td><td>{_esc(item.get('score') or '')}</td>"
                        f"<td>{_esc(compact_text(item.get('text',''),220))}</td></tr>"
                    )
                chunks.append("</table>")
            chunks.append("<p class='muted'>Το Markdown survival audit μετρά μόνο Markdown στοιχεία που έχουν DOCX evidence μέσα στο τρέχον επιλεγμένο output. Δεν μετρά όλο το Mathpix αρχείο.</p>")
        if markdown_pdf_spine:
            chunks.append("<h3>Markdown-first / PDF-guided spine</h3><div class='summary-grid'>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_pdf_spine.get('coverage', '—'))}</b>coverage</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_pdf_spine.get('scope', '—'))}</b>scope</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_pdf_spine.get('candidateCount', 0))}</b>candidates</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_pdf_spine.get('placedCount', 0))}</b>placed</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_pdf_spine.get('weakCount', 0))}</b>weak</div>")
            chunks.append(f"<div class='metric'><b>{_esc(markdown_pdf_spine.get('unplacedCount', 0))}</b>unplaced</div>")
            chunks.append("</div>")
            if markdown_pdf_spine.get("scopeWarning"):
                chunks.append(f"<p class='warn'>{_esc(markdown_pdf_spine.get('scopeWarning'))}</p>")
            problem_items = [
                item for item in markdown_pdf_spine.get("items", [])
                if str(item.get("status") or "") in {"weak", "unplaced"}
            ][:30]
            if problem_items:
                chunks.append("<table><tr><th>Type</th><th>Page</th><th>Status</th><th>Score</th><th>Markdown</th><th>PDF witness</th></tr>")
                for item in problem_items:
                    chunks.append(
                        f"<tr><td>{_esc(item.get('type'))}</td><td>{_esc(item.get('pdfPage') or item.get('markdownPageHint') or '')}</td>"
                        f"<td>{_esc(item.get('status') or '')}</td><td>{_esc(item.get('score') or '')}</td>"
                        f"<td>{_esc(compact_text(item.get('text',''),220))}</td>"
                        f"<td>{_esc(compact_text(item.get('pdfText',''),220))}</td></tr>"
                    )
                chunks.append("</table>")
            chunks.append("<p class='muted'>Αυτό είναι το νέο markdown-first checkpoint: κάθε στοιχείο του Mathpix Markdown του scope αντιστοιχίζεται απευθείας με PDF μάρτυρα πριν εμπλακεί το Mathpix DOCX ως δευτερεύων δότης.</p>")
        missing = content_audit.get("likely_missing_lines", [])[:20]
        if missing:
            chunks.append("<h3>Πιθανές ελλείψεις από το reference PDF</h3><table><tr><th>PDF page</th><th>Score</th><th>Text</th></tr>")
            for item in missing:
                chunks.append(
                    f"<tr><td>{_esc(item.get('page'))}:{_esc(item.get('line'))}</td>"
                    f"<td>{_esc(item.get('score'))}</td><td>{_esc(compact_text(item.get('text',''),220))}</td></tr>"
                )
            chunks.append("</table>")
        extras = content_audit.get("likely_extra_output_lines", [])[:20]
        if extras:
            chunks.append("<h3>Πιθανό έξτρα/σκουπίδι στο output</h3><table><tr><th>Output page</th><th>Score</th><th>Text</th></tr>")
            for item in extras:
                chunks.append(
                    f"<tr><td>{_esc(item.get('page'))}:{_esc(item.get('line'))}</td>"
                    f"<td>{_esc(item.get('score'))}</td><td>{_esc(compact_text(item.get('text',''),220))}</td></tr>"
                )
            chunks.append("</table>")
        formulas = content_audit.get("formula_review_lines", [])[:20]
        if formulas:
            chunks.append("<h3>Τύποι/σύμβολα που θέλουν οπτικό έλεγχο</h3><table><tr><th>Page</th><th>Score</th><th>Text</th></tr>")
            for item in formulas:
                chunks.append(
                    f"<tr><td>{_esc(item.get('page'))}:{_esc(item.get('line'))}</td>"
                    f"<td>{_esc(item.get('score'))}</td><td>{_esc(compact_text(item.get('text',''),220))}</td></tr>"
                )
            chunks.append("</table>")
        joins = content_audit.get("layout_join_artifacts", [])[:20]
        if joins:
            chunks.append("<h3>Ύποπτες ενώσεις/αναδιπλώσεις γραμμών στο output</h3><table><tr><th>Output page</th><th>Score</th><th>Text</th></tr>")
            for item in joins:
                chunks.append(
                    f"<tr><td>{_esc(item.get('page'))}:{_esc(item.get('line'))}</td>"
                    f"<td>{_esc(item.get('score'))}</td><td>{_esc(compact_text(item.get('text',''),220))}</td></tr>"
                )
            chunks.append("</table>")
        chunks.append("<p class='muted'>Το content audit είναι ανεκτικό σε αλλαγές διάταξης και σελίδων. Ελέγχει αν το περιεχόμενο επιβιώνει, όχι αν το output έχει ακριβώς ίδιο page count.</p></div>")

    spine_summary = conversion_spine.get("summary") or {}
    if spine_summary:
        coverage = spine_summary.get("coverage")
        coverage_text = f"{float(coverage) * 100:.1f}%" if isinstance(coverage, (int, float)) else "—"
        chunks.append("<div class='card'><h2>Conversion spine</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b>{_esc(spine_summary.get('selectedRowCount', 0))}</b>Markdown rows</div>")
        chunks.append(f"<div class='metric'><b>{_esc(coverage_text)}</b>included / witnessed</div>")
        chunks.append(f"<div class='metric'><b>{_esc(spine_summary.get('decisionRequiredCount', 0))}</b>decisions</div>")
        chunks.append(f"<div class='metric'><b>{_esc(len(spine_summary.get('selectedPages', []) or []))}</b>PDF pages</div>")
        chunks.append("</div>")
        outcome_counts = spine_summary.get("outcomeCounts") or {}
        if outcome_counts:
            chunks.append("<table><tr><th>Outcome</th><th>Count</th></tr>")
            for outcome, count in sorted(outcome_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                chunks.append(f"<tr><td>{_esc(outcome)}</td><td>{_esc(count)}</td></tr>")
            chunks.append("</table>")
        chunks.append("<p class='muted'>Ενιαίος πίνακας: Markdown item → PDF μάρτυρας → DOCX donor → output → απόφαση. Τα diagnostics καταγράφονται χωρίς να γίνονται αυτόματα βάρος χρήστη.</p></div>")

    donor_summary = docx_donor_map.get("summary") or {}
    if donor_summary:
        chunks.append("<div class='card'><h2>DOCX donor map</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b>{_esc(donor_summary.get('paragraphCount', 0))}</b>DOCX paragraphs</div>")
        chunks.append(f"<div class='metric'><b>{_esc(donor_summary.get('mathCandidateCount', 0))}</b>OMML candidates</div>")
        chunks.append(f"<div class='metric'><b>{_esc(donor_summary.get('markdownLinkedParagraphCount', 0))}</b>Markdown-linked</div>")
        chunks.append(f"<div class='metric'><b>{_esc(donor_summary.get('pdfLinkedParagraphCount', 0))}</b>PDF-linked</div>")
        chunks.append("</div>")
        donor_counts = donor_summary.get("donorTypeCounts") or {}
        if donor_counts:
            chunks.append("<table><tr><th>Donor type</th><th>Count</th></tr>")
            for donor_type, count in sorted(donor_counts.items(), key=lambda kv: (-kv[1], kv[0])):
                chunks.append(f"<tr><td>{_esc(donor_type)}</td><td>{_esc(count)}</td></tr>")
            chunks.append("</table>")
        chunks.append("<p class='muted'>Πρώτα απογράφεται το Mathpix DOCX ως δότης. Οι επόμενες αντιστοιχίσεις επιλέγουν από αυτόν τον χάρτη αντί να ξαναψάχνουν άτακτα μέσα στο αρχείο.</p></div>")

    layout_summary = page_layout_spine.get("summary") or {}
    if layout_summary:
        coverage = layout_summary.get("coverage")
        coverage_text = f"{float(coverage) * 100:.1f}%" if isinstance(coverage, (int, float)) else "—"
        contract_coverage = layout_summary.get("contractCoverage")
        contract_coverage_text = f"{float(contract_coverage) * 100:.1f}%" if isinstance(contract_coverage, (int, float)) else "—"
        chunks.append("<div class='card'><h2>Page layout spine</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_summary.get('rowCount', 0))}</b>Markdown/PDF layout rows</div>")
        chunks.append(f"<div class='metric'><b>{_esc(coverage_text)}</b>layout slot coverage</div>")
        chunks.append(f"<div class='metric'><b>{_esc(contract_coverage_text)}</b>layout contract coverage</div>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_summary.get('layoutSlotCount', 0))}</b>mapped slots</div>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_summary.get('unplacedLayoutSlotCount', 0))}</b>without slot</div>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_summary.get('contractUsableCount', 0))}</b>usable contracts</div>")
        chunks.append(f"<div class='metric'><b>{_esc(layout_summary.get('safeFlowOrderingSlotCount', 0))}</b>safe flow order slots</div>")
        chunks.append("</div>")
        grouped_counts = [
            ("Slot source", layout_summary.get("slotSourceCounts") or {}),
            ("Placement", layout_summary.get("placementCounts") or {}),
            ("Column role", layout_summary.get("columnRoleCounts") or {}),
            ("Style role", layout_summary.get("styleRoleCounts") or {}),
            ("Builder use", layout_summary.get("builderUseCounts") or {}),
        ]
        for title, counts in grouped_counts:
            if not counts:
                continue
            chunks.append(f"<h3>{_esc(title)}</h3><table><tr><th>Kind</th><th>Count</th></tr>")
            for source, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
                chunks.append(f"<tr><td>{_esc(source)}</td><td>{_esc(count)}</td></tr>")
            chunks.append("</table>")
        chunks.append("<p class='muted'>Το page layout spine συνδέει Markdown/PDF μάρτυρες με page_structure slots και πλέον εκθέτει layoutContract: σελίδα, στήλη, bbox, placement και style hint. Ο builder πρέπει να καταναλώνει μόνο τα πεδία που δηλώνονται ασφαλή.</p></div>")

    benchmark_quality = architecture_benchmark.get("quality") or {}
    benchmark_maps = architecture_benchmark.get("maps") or {}
    benchmark_timing = architecture_benchmark.get("timing") or {}
    if benchmark_quality or benchmark_maps or benchmark_timing:
        seconds = benchmark_timing.get("totalSeconds")
        seconds_text = f"{float(seconds):.1f}s" if isinstance(seconds, (int, float)) else "—"
        chunks.append("<div class='card'><h2>Architecture benchmark</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b>{_esc(seconds_text)}</b>total time</div>")
        chunks.append(f"<div class='metric'><b>{_esc(benchmark_quality.get('nativeWordMath', 0))}</b>native Word math</div>")
        chunks.append(f"<div class='metric'><b>{_esc(benchmark_quality.get('visualEquationFallbacks', 0))}</b>visual equations</div>")
        chunks.append(f"<div class='metric'><b>{_esc(benchmark_quality.get('userDecisions', 0))}</b>user decisions</div>")
        chunks.append(f"<div class='metric'><b>{_esc(benchmark_maps.get('docxOmmlUsed', 0))} / {_esc(benchmark_maps.get('docxOmmlCandidates', 0))}</b>OMML used / candidates</div>")
        coverage = benchmark_quality.get("markdownSurvivalCoverage")
        coverage_text = f"{float(coverage) * 100:.1f}%" if isinstance(coverage, (int, float)) else "—"
        chunks.append(f"<div class='metric'><b>{_esc(coverage_text)}</b>Markdown survival</div>")
        chunks.append("</div>")
        chunks.append("<p class='muted'>Λίγα σήματα για να συγκρίνουμε ίδια runs χωρίς χειροκίνητες σημειώσεις: χρόνος, editable math, visual fallbacks, decisions και OMML donor αξιοποίηση.</p></div>")

    docx_contribution = (fidelity_fallback_report.get("summary") or {}).get("docxContribution", {})
    if docx_contribution:
        grade = docx_contribution.get("grade") or "unknown"
        ratio = docx_contribution.get("ratio")
        ratio_text = f"{float(ratio) * 100:.1f}%" if isinstance(ratio, (int, float)) else "—"
        chunks.append("<div class='card'><h2>Mathpix DOCX contribution</h2>")
        chunks.append("<div class='summary-grid'>")
        chunks.append(f"<div class='metric'><b>{_esc(grade)}</b>grade</div>")
        chunks.append(f"<div class='metric'><b>{_esc(ratio_text)}</b>supported items</div>")
        chunks.append(f"<div class='metric'><b>{_esc(docx_contribution.get('strongMatches') or 0)}</b>strong DOCX matches</div>")
        chunks.append(f"<div class='metric'><b>{_esc(docx_contribution.get('nativeOmmlItems') or 0)}</b>native OMML items</div>")
        chunks.append("</div>")
        chunks.append(
            f"<p>{_esc(docx_contribution.get('supportedItems') or 0)} από {_esc(docx_contribution.get('totalItems') or 0)} τελικά στοιχεία είχαν ισχυρή DOCX/OMML συνεισφορά. "
            f"Οι Markdown εξισώσεις αντιστοιχίστηκαν {_esc(docx_contribution.get('markdownEquationMatchedCount') or 0)}/{_esc(docx_contribution.get('markdownEquationCount') or 0)}.</p>"
        )
        chunks.append(f"<p class='muted'>{_esc(docx_contribution.get('note') or '')}</p></div>")

    has_conversion_spine = bool(conversion_spine)
    spine_decision_queue = conversion_spine.get("decisionQueue", []) or []
    legacy_decision_queue = [] if has_conversion_spine else fidelity_fallback_report.get("userDecisionQueue", []) or []
    decision_queue = spine_decision_queue if has_conversion_spine else legacy_decision_queue
    if has_conversion_spine:
        decision_counts = {
            str(item.get("outcome") or item.get("type") or "conversion-spine"): 0
            for item in spine_decision_queue
        }
        for item in spine_decision_queue:
            key = str(item.get("outcome") or item.get("type") or "conversion-spine")
            decision_counts[key] = int(decision_counts.get(key, 0)) + 1
    else:
        decision_counts = (fidelity_fallback_report.get("summary") or {}).get("userDecisionQueueCounts", {})
    if decision_queue or decision_counts:
        chunks.append("<div class='card'><h2>User decisions</h2>")
        chunks.append("<div class='summary-grid'>")
        for kind, count in sorted(decision_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            chunks.append(f"<div class='metric'><b>{_esc(count)}</b>{_esc(kind)}</div>")
        chunks.append("</div>")
        chunks.append("<p class='muted'>Σύντομη λίστα μόνο για όσα χρειάζονται πιθανή απόφαση χρήστη. Όταν υπάρχει conversion spine, οι αποφάσεις βγαίνουν από αυτό και τα παλιά fidelity ευρήματα μένουν diagnostics.</p>")
        chunks.append("<table><tr><th>Kind</th><th>Page</th><th>Question</th><th>Recommended action</th><th>Evidence</th></tr>")
        for item in decision_queue[:80]:
            evidence = item.get("markdownText") or item.get("latex") or item.get("text") or item.get("pdfText") or ""
            chunks.append(
                f"<tr><td>{_esc(item.get('kind') or item.get('type') or item.get('outcome'))}</td><td>{_esc(item.get('page') or '')}</td>"
                f"<td>{_esc(item.get('question') or '')}</td>"
                f"<td>{_esc(item.get('recommendedAction') or item.get('reason') or '')}</td>"
                f"<td>{_esc(compact_text(evidence, 240))}</td></tr>"
            )
        chunks.append("</table></div>")

    review_queue = [] if has_conversion_spine else fidelity_fallback_report.get("actionableReviewQueue") or fidelity_fallback_report.get("humanReviewQueue", [])
    review_counts = {} if has_conversion_spine else (fidelity_fallback_report.get("summary") or {}).get("actionableReviewQueueCounts") or (fidelity_fallback_report.get("summary") or {}).get("humanReviewQueueCounts", {})
    if review_queue or review_counts:
        chunks.append("<div class='card'><h2>Actionable review queue</h2>")
        chunks.append("<div class='summary-grid'>")
        for kind, count in sorted(review_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            chunks.append(f"<div class='metric'><b>{_esc(count)}</b>{_esc(kind)}</div>")
        chunks.append("</div>")
        chunks.append("<p class='muted'>Εδώ συγκεντρώνονται σημεία με ουσιαστική fallback/ασθενή απόφαση. Τα θορυβώδη diagnostics κρατιούνται χωριστά για να μη γίνονται ψεύτικα βάρη απόφασης.</p>")
        chunks.append("<table><tr><th>Kind</th><th>Page</th><th>Status</th><th>Message</th><th>Resolution</th><th>Preview</th></tr>")
        for item in review_queue[:80]:
            preview = item.get("latex") or item.get("text") or item.get("pdfText") or ""
            chunks.append(
                f"<tr><td>{_esc(item.get('kind'))}</td><td>{_esc(item.get('page') or '')}</td>"
                f"<td>{_esc(item.get('status') or '')}</td><td>{_esc(item.get('message') or '')}</td>"
                f"<td>{_esc(item.get('resolution') or '')}</td>"
                f"<td>{_esc(compact_text(preview, 220))}</td></tr>"
            )
        chunks.append("</table>")
        if len(review_queue) > 80:
            chunks.append(f"<p class='muted'>Εμφανίζονται οι πρώτες 80 γραμμές από {len(review_queue)}.</p>")
        chunks.append("</div>")

    diagnostic_queue = [] if has_conversion_spine else fidelity_fallback_report.get("diagnosticReviewQueue", [])
    diagnostic_counts = {} if has_conversion_spine else (fidelity_fallback_report.get("summary") or {}).get("diagnosticReviewQueueCounts", {})
    if diagnostic_queue or diagnostic_counts:
        chunks.append("<div class='card'><h2>Low-priority diagnostics</h2>")
        chunks.append("<div class='summary-grid'>")
        for kind, count in sorted(diagnostic_counts.items(), key=lambda kv: (-kv[1], kv[0])):
            chunks.append(f"<div class='metric'><b>{_esc(count)}</b>{_esc(kind)}</div>")
        chunks.append("</div>")
        chunks.append("<p class='muted'>Παύλες, κενά, OCR μαθηματικών συμβόλων και αναδιπλώσεις γραμμών. Δεν είναι αποφάσεις χρήστη εκτός αν συνοδεύονται από πραγματικό missing/content warning.</p>")
        chunks.append("<table><tr><th>Kind</th><th>Page</th><th>Status</th><th>Message</th><th>Preview</th></tr>")
        for item in diagnostic_queue[:80]:
            preview = item.get("latex") or item.get("text") or item.get("pdfText") or ""
            chunks.append(
                f"<tr><td>{_esc(item.get('kind'))}</td><td>{_esc(item.get('page') or '')}</td>"
                f"<td>{_esc(item.get('status') or '')}</td><td>{_esc(item.get('message') or '')}</td>"
                f"<td>{_esc(compact_text(preview, 220))}</td></tr>"
            )
        chunks.append("</table>")
        if len(diagnostic_queue) > 80:
            chunks.append(f"<p class='muted'>Εμφανίζονται οι πρώτες 80 γραμμές από {len(diagnostic_queue)}.</p>")
        chunks.append("</div>")

    chunks.append("<div class='card'><h2>Page structure</h2><p>Κύρια στήλη = κανονική ροή Word. Callouts = positioned paragraph frames. Σχήματα = ένα crop ανά λογική οπτική περιοχή. Εξισώσεις = native OMML όταν εντοπίζεται αντίστοιχη παράγραφος Word, αλλιώς crop. Δεν χρησιμοποιούνται layout tables ή ολοσέλιδο background.</p><table><tr><th>Σελίδα</th><th>Κύρια στήλη PDF pt</th><th>Flow</th><th>Callouts</th><th>Inline visuals</th><th>Floating visuals</th><th>Εξισώσεις μέσα σε callout</th></tr>")
    for page_num, struct in sorted(structure_by_page.items()):
        inline_count = sum(1 for g in struct.get("visual_groups", []) if g.get("placement") == "inline")
        floating_count = sum(1 for g in struct.get("visual_groups", []) if g.get("placement") == "floating")
        chunks.append(
            f"<tr><td>{page_num}</td><td><code>{_esc(struct.get('main_column'))}</code></td>"
            f"<td>{len(struct.get('flow', []))}</td><td>{len(struct.get('callouts', []))}</td>"
            f"<td>{inline_count}</td><td>{floating_count}</td><td>{_esc(struct.get('absorbed_callout_equation_count', 0))}</td></tr>"
        )
    chunks.append("</table></div>")

    status = calibration.get("status", "skipped")
    status_class = "ok" if status == "completed" else ("bad" if str(status).startswith("failed") else "warn")
    selected_fit = ((calibration.get("selected_build_report") or {}).get("flow_geometry_fit_summary") or {})
    chunks.append(f"<div class='card'><h2>Word → PDF calibration</h2><p>Κατάσταση: <span class='{status_class}'>{_esc(status)}</span><br>Renderer: {_esc(calibration.get('renderer') or '—')}<br>Επιλεγμένο μέγεθος Word: {_esc(calibration.get('selected_body_size_pt') or '—')} pt</p>")
    if selected_fit:
        chunks.append(
            "<div class='summary-grid'>"
            f"<div class='metric'><b>{_esc(selected_fit.get('count', 0))}</b>paragraph geometry checks</div>"
            f"<div class='metric'><b>{_esc(selected_fit.get('bad_count', 0))}</b>bad paragraph fits</div>"
            f"<div class='metric'><b>{_esc(selected_fit.get('average_abs_text_end_delta_pt', '—'))}</b>avg paragraph Δpt</div>"
            f"<div class='metric'><b>{_esc(selected_fit.get('max_abs_text_end_delta_pt', '—'))}</b>max paragraph Δpt</div>"
            "</div>"
        )
    font_scale_sandbox = calibration.get("word_font_scale_sandbox") or {}
    if font_scale_sandbox:
        best_scale = font_scale_sandbox.get("best") or {}
        seed_candidate = font_scale_sandbox.get("seed_candidate") or {}
        chunks.append(
            "<h3>Word font-scale sandbox</h3>"
            f"<p class='muted'>Policy: {_esc(font_scale_sandbox.get('policy'))} · "
            f"best scale: {_esc(best_scale.get('scale', '—'))} · "
            f"pages: {_esc(best_scale.get('pages', '—'))} · "
            f"delta: {_esc(best_scale.get('page_delta', '—'))}</p>"
        )
        if seed_candidate:
            chunks.append(
                f"<p class='muted'>Seed: candidate {_esc(seed_candidate.get('candidate_index'))} · "
                f"{_esc(seed_candidate.get('body_size_pt'))} pt · gap {_esc(seed_candidate.get('gap_scale'))} · "
                f"line {_esc(seed_candidate.get('line_spacing_multiple'))} · "
                f"{_esc(seed_candidate.get('policy'))}</p>"
            )
        if font_scale_sandbox.get("results"):
            chunks.append("<table><tr><th>Scale</th><th>Pages</th><th>Δ pages</th></tr>")
            for row in font_scale_sandbox.get("results", []) or []:
                chunks.append(
                    f"<tr><td>{_esc(row.get('scale'))}</td>"
                    f"<td>{_esc(row.get('pages'))}</td>"
                    f"<td>{_esc(row.get('page_delta'))}</td></tr>"
                )
            chunks.append("</table>")
        for refinement in font_scale_sandbox.get("refinements", []) or []:
            chunks.append(
                f"<h4>Directed refinement: {_esc(refinement.get('direction') or '—')}</h4>"
                "<table><tr><th>Scale</th><th>Pages</th><th>Δ pages</th></tr>"
            )
            for row in refinement.get("results", []) or []:
                chunks.append(
                    f"<tr><td>{_esc(row.get('scale'))}</td>"
                    f"<td>{_esc(row.get('pages'))}</td>"
                    f"<td>{_esc(row.get('page_delta'))}</td></tr>"
                )
            chunks.append("</table>")
        if font_scale_sandbox.get("error"):
            chunks.append(f"<p class='bad'>Sandbox error: {_esc(font_scale_sandbox.get('error'))}</p>")
    compact_floor = calibration.get("compact_floor_preflight") or {}
    if compact_floor:
        stopped_class = "bad" if compact_floor.get("stopped") else "ok"
        chunks.append(
            "<h3>Compact floor preflight</h3>"
            f"<p class='{stopped_class}'>Output pages: {_esc(compact_floor.get('output_page_count') or '—')} / "
            f"target: {_esc(compact_floor.get('target_page_count') or '—')} · "
            f"stopped: {_esc(compact_floor.get('stopped'))}</p>"
            f"<p class='muted'>{_esc(compact_floor.get('reason') or '')}</p>"
        )
    candidates = calibration.get("candidates", [])
    if candidates:
        chunks.append("<table><tr><th>Word pt</th><th>Font scale</th><th>Gap</th><th>Line</th><th>Σελίδες</th><th>Probe only</th><th>Boundary fails</th><th>Paragraph bad</th><th>Paragraph avg Δpt</th><th>Overflow Δpt</th><th>Underfill Δpt</th><th>Text-end Δpt</th><th>Objective / error</th></tr>")
        for candidate in candidates:
            comparison = candidate.get("comparison") or {}
            fit = ((candidate.get("build_report") or {}).get("flow_geometry_fit_summary") or {})
            chunks.append(
                f"<tr><td>{_esc(candidate.get('body_size_pt'))}</td>"
                f"<td>{_esc(candidate.get('font_scale', '—'))}</td>"
                f"<td>{_esc(candidate.get('gap_scale'))}</td>"
                f"<td>{_esc(candidate.get('line_spacing_multiple') or '—')}</td>"
                f"<td>{_esc(comparison.get('output_page_count', '—'))}</td>"
                f"<td>{_esc('yes' if candidate.get('page_count_probe_only') else 'no')}</td>"
                f"<td>{_esc(comparison.get('page_boundary_failure_count', '—'))}</td>"
                f"<td>{_esc(fit.get('bad_count', '—'))}</td>"
                f"<td>{_esc(fit.get('average_abs_text_end_delta_pt', '—'))}</td>"
                f"<td>{_esc(comparison.get('average_overflow_delta_pt', '—'))}</td>"
                f"<td>{_esc(comparison.get('average_underfill_delta_pt', '—'))}</td>"
                f"<td>{_esc(comparison.get('average_text_end_delta_pt', '—'))}</td>"
                f"<td>{_esc(comparison.get('objective', candidate.get('error', '—')))}</td></tr>"
            )
        chunks.append("</table>")
    lane_search = calibration.get("monotonic_lane_search") or {}
    if lane_search:
        chunks.append("<h3>Monotonic lane search</h3>")
        chunks.append(
            f"<p class='muted'>Policy: {_esc(lane_search.get('policy'))} · "
            f"lanes: {_esc(lane_search.get('lane_count'))} · "
            f"limit: {_esc(lane_search.get('full_search_limit'))}</p>"
        )
        chunks.append("<table><tr><th>Gap</th><th>Line</th><th>Seed</th><th>Seed pages</th><th>Indexes</th><th>Evaluated</th><th>Result</th></tr>")
        for lane in lane_search.get("lanes", []) or []:
            chunks.append(
                f"<tr><td>{_esc(lane.get('gap_scale'))}</td>"
                f"<td>{_esc(lane.get('line_spacing_multiple'))}</td>"
                f"<td>{_esc(lane.get('compact_seed_index'))}</td>"
                f"<td>{_esc(lane.get('compact_seed_pages'))}</td>"
                f"<td><code>{_esc(lane.get('indexes'))}</code></td>"
                f"<td><code>{_esc(lane.get('evaluated_indexes'))}</code></td>"
                f"<td>{_esc(lane.get('result'))}</td></tr>"
            )
        chunks.append("</table>")
    chunks.append("</div>")

    chunks.append("<div class='card'><h2>Σημασιολογικές κατηγορίες PDF</h2><table><tr><th>Κατηγορία</th><th>Πλήθος</th><th>Χρήση</th></tr>")
    for kind, count in sorted(class_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        use = "DOCX alignment / Word flow" if kind in {"body", "heading", "callout", "caption", "banner"} else "visual grouping / excluded from fuzzy matching"
        chunks.append(f"<tr><td>{_esc(kind)}</td><td>{count}</td><td>{use}</td></tr>")
    chunks.append("</table></div>")

    chunks.append("<div class='card'><h2>Κυρίαρχα μεγέθη γραμματοσειρών στο PDF</h2><table><tr><th>pt</th><th>Σταθμισμένοι χαρακτήρες</th></tr>")
    for item in top_sizes:
        chunks.append(f"<tr><td>{_esc(item.get('size_pt'))}</td><td>{_esc(item.get('weighted_chars'))}</td></tr>")
    chunks.append("</table></div>")

    for page in pdf_analysis.get("pages", []):
        page_num = int(page["page"])
        struct = structure_by_page.get(page_num, {})
        chunks.append(f"<div class='card'><h2>PDF page {page_num}</h2><div class='grid'>")
        chunks.append(f"<div><img class='pageimg' src='assets/page-{page_num}.png'></div>")
        chunks.append("<div>")
        if struct:
            chunks.append("<h3>Visual groups</h3><table><tr><th>kind</th><th>placement</th><th>bbox</th><th>members</th></tr>")
            for group in struct.get("visual_groups", []):
                chunks.append(
                    f"<tr><td>{_esc(group.get('kind'))}</td><td>{_esc(group.get('placement'))}</td>"
                    f"<td><code>{_esc(group.get('bbox'))}</code></td><td>{_esc(', '.join(group.get('member_ids', [])))}</td></tr>"
                )
            chunks.append("</table>")

        chunks.append("<h3>Paragraph alignment</h3><table><tr><th>PDF block</th><th>type</th><th>DOCX match</th><th>score</th></tr>")
        for match in matches_by_page.get(page_num, []):
            status_row = match.get("status", "unresolved")
            docx_ids = ", ".join(match.get("docx_paragraphs", [])) or "—"
            chunks.append(
                f"<tr class='{_esc(status_row)}'><td><b>{_esc(match.get('pdf_region'))}</b><br>{_esc(compact_text(match.get('pdf_text',''),220))}</td>"
                f"<td><span class='badge'>{_esc(match.get('semantic_type',''))}</span><br>{_esc(match.get('flow_zone',''))}</td>"
                f"<td>{_esc(docx_ids)}<br>{_esc(compact_text(match.get('docx_text',''),220))}</td>"
                f"<td>{_esc(match.get('score',0))}</td></tr>"
            )
        chunks.append("</table>")

        excluded = excluded_by_page.get(page_num, [])
        if excluded:
            chunks.append("<h3>Εκτός paragraph matching</h3><table><tr><th>block</th><th>type</th><th>text</th></tr>")
            for item in excluded:
                chunks.append(
                    f"<tr class='excluded'><td>{_esc(item.get('id'))}</td><td>{_esc(item.get('semantic_type'))}</td>"
                    f"<td>{_esc(compact_text(item.get('text',''),180))}</td></tr>"
                )
            chunks.append("</table>")
        chunks.append("</div></div></div>")

    chunks.append("""<div class='card'><h2>Τι ελέγχει η v0.8.3 strict</h2>
<ul><li>Callout ως ενιαίο container: κείμενο και μαθηματική σχέση παραμένουν μέσα στο ίδιο πλαίσιο.</li>
<li>Διατήρηση inline και displayed εξισώσεων ως native Word Math (OMML), όταν υπάρχουν στο δοθέν DOCX.</li>
<li>Εισαγωγή raster crop μόνο ως fallback όταν δεν υπάρχει αντιστοιχισμένη OMML παράγραφος.</li>
<li>Ειδικό native style για τις μπάρες «Εφαρμογή» με γκρι γέμισμα και περίγραμμα.</li>
<li>Κανονική ροή παραγράφων Word, χωρίς layout tables.</li>
<li>Επεξεργάσιμα callouts ως positioned paragraph frames.</li>
<li>Βαθμονόμηση που μετρά πλέον και το κατακόρυφο προφίλ/τελείωμα του κύριου κειμένου, όχι μόνο συνολικό pixel diff.</li></ul>
<h2>Τι δεν κλειδώνει ακόμη</h2><p>Τα σύνθετα επιστημονικά διαγράμματα παραμένουν crops. Επίσης η πιστότητα των OMML εξισώσεων εξαρτάται από το αν ο ενδιάμεσος DOCX μετατροπέας τις διατήρησε ως πραγματικό Word Math. Ο αλγόριθμος ανακατασκευής παραμένει η βάση v0.7. Η v0.8.3 ελέγχει αυστηρά το execution path και δεν δημοσιεύει nearest candidate όταν λείπει exact page count.</p></div>""")
    chunks.append("</body></html>")
    report_path = report_dir / "index.html"
    report_path.write_text("".join(chunks), encoding="utf-8")
    return report_path
