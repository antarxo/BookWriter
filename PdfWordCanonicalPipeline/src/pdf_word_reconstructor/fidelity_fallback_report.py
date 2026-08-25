from __future__ import annotations

from collections import Counter
from typing import Any

from rapidfuzz import fuzz

from .common import normalize_text


def _compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _union_bbox(boxes: list[Any]) -> list[float] | None:
    clean: list[list[float]] = []
    for box in boxes:
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            clean.append([float(value) for value in box])
        except (TypeError, ValueError):
            continue
    if not clean:
        return None
    return [
        round(min(box[0] for box in clean), 3),
        round(min(box[1] for box in clean), 3),
        round(max(box[2] for box in clean), 3),
        round(max(box[3] for box in clean), 3),
    ]


def _bbox_overlap_ratio(a: Any, b: Any) -> float:
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != 4 or len(b) != 4:
        return 0.0
    try:
        ax0, ay0, ax1, ay1 = [float(value) for value in a]
        bx0, by0, bx1, by1 = [float(value) for value in b]
    except (TypeError, ValueError):
        return 0.0
    overlap_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    overlap_h = max(0.0, min(ay1, by1) - max(ay0, by0))
    overlap = overlap_w * overlap_h
    if overlap <= 0:
        return 0.0
    area = max(1.0, min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0)))
    return overlap / area


def _markdown_candidates(markdown_pdf_spine: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in (markdown_pdf_spine or {}).get("items", []) or []:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if item.get("status") not in {"strong", "medium", "page-hint"}:
            continue
        page = item.get("pdfPage") or item.get("inferredPage") or item.get("markdownPageHint")
        candidates.append({
            "id": item.get("id"),
            "page": page,
            "type": item.get("type"),
            "status": item.get("status"),
            "text": text,
            "normalized": normalize_text(text),
            "pdfText": str(item.get("pdfText") or ""),
            "pdfNormalized": normalize_text(str(item.get("pdfText") or "")),
            "bbox": item.get("bbox"),
            "score": item.get("score"),
            "pageHintSource": item.get("pageHintSource"),
        })
    return candidates


def _best_markdown_evidence(item: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    try:
        page = int(item.get("page"))
    except Exception:
        page = None
    item_text = normalize_text(str(item.get("pdfText") or item.get("text") or item.get("latex") or ""))
    item_bbox = item.get("bbox")
    best: tuple[float, dict[str, Any]] | None = None
    for candidate in candidates:
        if page is not None and candidate.get("page") not in {page, None}:
            continue
        text_score = 0.0
        if item_text and candidate["normalized"]:
            text_score = max(text_score, float(fuzz.partial_ratio(item_text, candidate["normalized"])))
            if candidate["pdfNormalized"]:
                text_score = max(text_score, float(fuzz.partial_ratio(item_text, candidate["pdfNormalized"])))
        overlap = _bbox_overlap_ratio(item_bbox, candidate.get("bbox"))
        if text_score < 68.0 and not (overlap >= 0.20 and text_score >= 55.0) and not (not item_text and overlap >= 0.50):
            continue
        score = text_score
        if overlap:
            score = max(score, 60.0 + min(35.0, overlap * 35.0))
        if page is not None and candidate.get("page") == page:
            score += 4.0
        if candidate.get("status") in {"strong", "medium", "page-hint"}:
            score += 3.0
        if best is None or score > best[0]:
            best = (score, candidate)
    if not best:
        return None
    score, candidate = best
    return {
        "markdownId": candidate.get("id"),
        "markdownType": candidate.get("type"),
        "markdownPage": candidate.get("page"),
        "markdownStatus": candidate.get("status"),
        "markdownText": candidate.get("text"),
        "markdownEvidenceScore": round(score, 2),
        "markdownEvidenceSource": "markdown-pdf-spine",
        "markdownPageHintSource": candidate.get("pageHintSource"),
    }


def _attach_markdown_evidence(queue: list[dict[str, Any]], markdown_pdf_spine: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = _markdown_candidates(markdown_pdf_spine)
    if not candidates:
        return queue
    for item in queue:
        if item.get("markdownText") or item.get("latex"):
            continue
        evidence = _best_markdown_evidence(item, candidates)
        if evidence:
            item.update(evidence)
    return queue


def _build_items(build_report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in (build_report or {}).get("pages", []):
        page_no = page.get("page")
        for item in page.get("items", []):
            row = dict(item)
            row["page"] = page_no
            items.append(row)
    return items


def _equation_status(source: str, math_count: int) -> str:
    if source in {"docx-native-omml", "markdown-latex-to-omml"} or math_count > 0:
        return "native-word-math"
    if source.startswith("page-crop-after-latex"):
        return "visual-fallback-latex-conversion-failed"
    if source.startswith("page-crop"):
        return "visual-fallback-no-native-match"
    if not source:
        return "not-built"
    return "review"


def _page_structure_items(page_structure: dict[str, Any]) -> dict[str, dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {}
    for page in (page_structure or {}).get("pages", []):
        for item in page.get("flow", []):
            item_id = str(item.get("id") or "")
            if item_id:
                row = dict(item)
                row["page"] = page.get("page")
                items[item_id] = row
    return items


def _equation_review_status(row: dict[str, Any]) -> str:
    loss_status = str(row.get("lossStatus") or "")
    if loss_status == "native-word-math":
        return "ok"
    if loss_status.startswith("visual-fallback"):
        return "usable-visual-warning"
    if row.get("latexPreview"):
        return "needs-user-correction"
    return "needs-user-identification"


def _equation_review_severity(row: dict[str, Any]) -> str:
    loss_status = str(row.get("lossStatus") or "")
    if loss_status == "not-built":
        return "confirm"
    if loss_status.startswith("visual-fallback"):
        return "review"
    if row.get("latexPreview"):
        return "review"
    return "confirm"


def _build_alignment_review_queue(alignment: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for match in (alignment or {}).get("matches", []):
        status = str(match.get("status") or "")
        if status not in {"weak", "unresolved"}:
            continue
        rejected_docx = bool(match.get("docx_text"))
        queue.append({
            "kind": "alignment",
            "severity": "diagnostic",
            "status": status,
            "page": match.get("page"),
            "id": match.get("pdf_region"),
            "semanticType": match.get("semantic_type"),
            "message": "Αδύναμη αντιστοίχιση PDF↔DOCX." if status == "weak" else "Δεν βρέθηκε ασφαλής αντιστοίχιση PDF↔DOCX.",
            "resolution": "Δεν χρησιμοποιείται ως ασφαλές DOCX match· ο builder κρατά την PDF/Markdown διαδρομή. Αυτό είναι διαγνωστικό donor-rejection, όχι απόφαση χρήστη από μόνο του.",
            "rejectedDocxCandidate": rejected_docx,
            "pdfText": _compact(match.get("pdf_text")),
            "docxText": _compact(match.get("docx_text")),
            "docxParagraphs": match.get("docx_paragraphs", []),
            "bbox": match.get("bbox"),
            "score": match.get("score"),
        })
    return queue


def _build_pdf_only_review_queue(items: list[dict[str, Any]], structure_items_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for item in items:
        if str(item.get("source") or "") != "pdf":
            continue
        item_id = str(item.get("id") or "")
        structure_item = structure_items_by_id.get(item_id, {})
        queue.append({
            "kind": "pdf-text-fallback",
            "severity": "diagnostic",
            "status": "pdf-only",
            "page": item.get("page"),
            "id": item_id,
            "semanticType": item.get("type"),
            "message": "Το στοιχείο χτίστηκε από PDF text επειδή δεν υπήρχε ασφαλές DOCX match.",
            "resolution": "Το τελικό στοιχείο δημιουργήθηκε από PDF text· δεν έγινε αντικατάσταση από αδύναμο DOCX candidate. Άρα είναι τεκμήριο παρουσίας στο output, όχι εκκρεμής απόφαση.",
            "text": _compact(structure_item.get("text")),
            "bbox": structure_item.get("bbox"),
        })
    return queue


def _build_callout_review_queue(build_report: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for page in (build_report or {}).get("pages", []):
        for callout in page.get("callout_builds", []):
            if callout.get("source_paragraphs"):
                continue
            queue.append({
                "kind": "callout-fallback",
                "severity": "diagnostic",
                "status": "pdf-only",
                "page": page.get("page"),
                "id": callout.get("id"),
                "message": "Πλαίσιο/callout χτίστηκε από PDF text χωρίς ασφαλή DOCX paragraph.",
                "resolution": "Το πλαίσιο κρατήθηκε από PDF geometry/text. Σημαίνεται ως diagnostic ώστε να μη φουσκώνει τις αποφάσεις χρήστη όταν υπάρχει ήδη στο output.",
                "bbox": callout.get("bbox"),
                "estimatedLines": callout.get("estimated_lines"),
            })
    return queue


def _build_content_review_queue(content_audit: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for item in (content_audit or {}).get("likely_missing_lines", []):
        queue.append({
            "kind": "content-missing",
            "severity": "confirm",
            "status": "likely-missing",
            "page": item.get("page"),
            "line": item.get("line"),
            "message": "Πιθανή έλλειψη περιεχομένου σε σχέση με το PDF.",
            "resolution": "Δεν υπάρχει αυτόματη επίλυση· το σημείο απαιτεί επιβεβαίωση από τον χρήστη.",
            "text": _compact(item.get("text")),
            "score": item.get("score"),
        })
    for item in (content_audit or {}).get("formula_review_lines", []):
        queue.append({
            "kind": "formula-text-review",
            "severity": "diagnostic",
            "status": "formula-symbols",
            "page": item.get("page"),
            "line": item.get("line"),
            "message": "Γραμμή με μαθηματικά/σύμβολα που θέλει οπτικό έλεγχο.",
            "resolution": "Διαγνωστική σήμανση μόνο· δεν είναι απόφαση χρήστη και συχνά αφορά OCR/παύλες/σύμβολα που καλύπτονται ήδη από native εξίσωση ή άλλη διάταξη.",
            "text": _compact(item.get("text")),
            "score": item.get("score"),
        })
    for item in (content_audit or {}).get("layout_join_artifacts", []):
        queue.append({
            "kind": "layout-join-review",
            "severity": "diagnostic",
            "status": "possible-line-join",
            "page": item.get("page"),
            "line": item.get("line"),
            "message": "Πιθανή ένωση ή αναδίπλωση γραμμών στο output.",
            "resolution": "Διαγνωστική σήμανση layout· συχνά αφορά παύλες, κενά ή αλλαγή γραμμής και δεν γίνεται απόφαση χρήστη χωρίς άλλο στοιχείο απώλειας.",
            "text": _compact(item.get("text")),
            "score": item.get("score"),
        })
    return queue


def _build_markdown_survival_review_queue(content_audit: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    survival = (content_audit or {}).get("markdown_survival") or {}
    for item in survival.get("problemElements", []) or []:
        status = str(item.get("status") or "")
        semantic_type = str(item.get("type") or "")
        is_table = semantic_type in {"table", "latex_table"}
        decision_eligible = bool(item.get("survivalDecisionEligible"))
        queue.append({
            "kind": "markdown-table-structure" if is_table else ("markdown-missing" if status == "missing" else "markdown-weak-match"),
            "severity": "diagnostic" if is_table or not decision_eligible else ("confirm" if status == "missing" else "review"),
            "status": status,
            "page": item.get("page"),
            "line": item.get("line"),
            "id": item.get("id"),
            "semanticType": semantic_type,
            "message": (
                "Markdown table/tabular δεν αντιστοιχίστηκε ως καθαρό κείμενο στο output."
                if is_table
                else ("Markdown element δεν εντοπίστηκε με ασφάλεια στο τελικό output." if status == "missing" else "Markdown element εντοπίστηκε αδύναμα στο τελικό output.")
            ),
            "resolution": (
                "Διαγνωστικό δομής πίνακα: δεν γίνεται απόφαση χρήστη πάνω σε raw LaTeX/tabular σύνταξη. Η ουσιαστική απόφαση ανοίγει μόνο αν ο πίνακας αποδειχθεί οπτικά/περιεχομενικά απών."
                if is_table
                else "Δεν γίνεται σιωπηρή διόρθωση· ο χρήστης πρέπει να επιβεβαιώσει αν λείπει, αν τοποθετήθηκε αλλού ή αν χρειάζεται διόρθωση."
            ),
            "text": _compact(item.get("text"), 320),
            "score": item.get("score"),
            "scorePdf": item.get("scorePdf"),
            "scoreDocx": item.get("scoreDocx"),
            "semanticTokenCount": item.get("semanticTokenCount"),
            "survivalDecisionEligible": decision_eligible,
            "docxEvidence": item.get("docxEvidence"),
        })
    return queue


def _build_markdown_pdf_spine_review_queue(markdown_pdf_spine: dict[str, Any]) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for item in (markdown_pdf_spine or {}).get("items", []) or []:
        status = str(item.get("status") or "")
        if status not in {"weak", "unplaced"}:
            continue
        queue.append({
            "kind": "markdown-pdf-spine",
            "severity": "diagnostic",
            "status": status,
            "page": item.get("pdfPage") or item.get("markdownPageHint"),
            "line": item.get("line"),
            "id": item.get("id"),
            "semanticType": item.get("type"),
            "message": "Markdown element δεν βρήκε καθαρό PDF μάρτυρα." if status == "unplaced" else "Markdown element βρήκε μόνο αδύναμο PDF μάρτυρα.",
            "resolution": "Markdown-first manifest diagnostic: το στοιχείο δεν απορρίπτεται και δεν γίνεται απόφαση χρήστη μόνο λόγω αδύναμου PDF witness. Απόφαση ανοίγει μόνο αν αποτύχει και ο έλεγχος τελικής παρουσίας/output.",
            "text": _compact(item.get("text"), 360),
            "pdfText": _compact(item.get("pdfText"), 360),
            "score": item.get("score"),
            "bbox": item.get("bbox"),
            "docxEvidence": item.get("docxEvidence"),
            "manifestOutcome": item.get("manifestOutcome"),
        })
    return queue


def _review_counts(queue: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in queue:
        counts[str(item.get("kind") or "")] += 1
    return dict(counts)


def _severity_counts(queue: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in queue:
        counts[str(item.get("severity") or "review")] += 1
    return dict(counts)


def _actionable_review_queue(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in queue if str(item.get("severity") or "") in {"confirm", "review"}]


def _diagnostic_review_queue(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in queue if str(item.get("severity") or "") not in {"confirm", "review"}]


def _decision_question(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "")
    status = str(item.get("status") or "")
    semantic = str(item.get("semanticType") or "")
    if kind == "equation":
        return "Να διορθωθεί η εξίσωση από το Markdown LaTeX ή να μείνει η τρέχουσα οπτική απόδοση;"
    if kind == "content-missing":
        return "Υπάρχει όντως χαμένο περιεχόμενο σε σχέση με το PDF;"
    if kind in {"markdown-missing", "markdown-weak-match"}:
        return "Το στοιχείο του Mathpix Markdown λείπει από το output ή έχει τοποθετηθεί/μετασχηματιστεί αλλού;"
    if kind == "markdown-pdf-spine":
        return "Πού ανήκει αυτό το Markdown στοιχείο πάνω στον PDF μάρτυρα και πρέπει να περάσει στο DOCX;"
    if kind == "alignment" and status == "unresolved":
        if semantic == "callout":
            return "Το πλαίσιο πρέπει να κρατηθεί από PDF/Markdown ή να συνδεθεί χειροκίνητα με άλλο DOCX σημείο;"
        return "Το κείμενο πρέπει να κρατηθεί από PDF/Markdown ή να συνδεθεί χειροκίνητα με άλλο DOCX σημείο;"
    return "Χρειάζεται επιβεβαίωση από τον χρήστη πριν θεωρηθεί λυμένο."


def _decision_action(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "")
    status = str(item.get("status") or "")
    if kind == "equation":
        return "Άνοιγμα editor εξίσωσης με βάση το Markdown LaTeX και έλεγχος του τελικού Word math."
    if kind in {"markdown-missing", "markdown-weak-match"}:
        return "Σύγκριση Markdown/PDF με output και απόφαση: προσθήκη, διόρθωση στο Word, ή αποδοχή αν υπάρχει ήδη αλλού."
    if kind == "markdown-pdf-spine":
        return "Σύγκριση με το PDF σημείο. Αν επιβεβαιωθεί, περνά ως Markdown-first περιεχόμενο με PDF-guided θέση."
    if kind == "content-missing":
        return "Σύγκριση PDF με τελικό output στη συγκεκριμένη σελίδα και προσθήκη/διόρθωση αν λείπει γραμμή."
    if kind == "alignment" and status == "unresolved":
        return "Οπτική σύγκριση PDF με output· ο ύποπτος DOCX candidate έχει απορριφθεί από τον αυτοματισμό."
    return "Οπτικός έλεγχος στο τελικό output."


def _output_evidence(item: dict[str, Any], pdf_text_ids: set[str], callout_ids: set[str]) -> dict[str, Any]:
    key = str(item.get("id") or "")
    final_source = str(item.get("finalSource") or "")
    loss_status = str(item.get("lossStatus") or "")
    if final_source in {"docx-native-omml", "markdown-latex-to-omml"} or loss_status == "native-word-math":
        return {
            "includedInOutput": True,
            "outputEvidence": "included-native-word-math",
            "outputEvidenceLabel": "Περιλαμβάνεται στο DOCX ως native Word math.",
        }
    if final_source.startswith("page-crop"):
        return {
            "includedInOutput": True,
            "outputEvidence": "included-visual-crop",
            "outputEvidenceLabel": "Περιλαμβάνεται στο DOCX ως οπτικό crop/εικόνα από το PDF. Δεν είναι ακόμη editable Word math/text.",
        }
    if key in callout_ids:
        return {
            "includedInOutput": True,
            "outputEvidence": "included-pdf-callout-text",
            "outputEvidenceLabel": "Περιλαμβάνεται στο DOCX ως επεξεργάσιμο πλαίσιο από PDF text.",
        }
    if key in pdf_text_ids:
        return {
            "includedInOutput": True,
            "outputEvidence": "included-pdf-text",
            "outputEvidenceLabel": "Περιλαμβάνεται στο DOCX ως επεξεργάσιμο κείμενο από PDF text.",
        }
    return {
        "includedInOutput": None,
        "outputEvidence": "not-proven-by-report",
        "outputEvidenceLabel": "Το report δεν έχει ξεχωριστή απόδειξη ότι αυτό το σημείο χτίστηκε στο DOCX.",
    }


def _build_user_decision_queue(human_review_queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    grouped_callouts: dict[Any, list[dict[str, Any]]] = {}
    consumed: set[int] = set()
    pdf_text_ids = {
        str(item.get("id") or "")
        for item in human_review_queue
        if item.get("kind") == "pdf-text-fallback" and item.get("status") == "pdf-only"
    }
    callout_ids = {
        str(item.get("id") or "")
        for item in human_review_queue
        if item.get("kind") == "callout-fallback" and item.get("status") == "pdf-only"
    }
    for index, item in enumerate(human_review_queue):
        if str(item.get("severity") or "") != "confirm":
            continue
        if item.get("kind") == "alignment" and item.get("status") == "unresolved" and item.get("semanticType") == "callout":
            grouped_callouts.setdefault(item.get("page"), []).append({"index": index, **item})
    for page, rows in grouped_callouts.items():
        if len(rows) < 3:
            continue
        consumed.update(int(row["index"]) for row in rows)
        preview = " / ".join(_compact(row.get("pdfText"), 90) for row in rows if row.get("pdfText"))
        ids = [row.get("id") for row in rows if row.get("id")]
        included_ids = [str(item_id) for item_id in ids if str(item_id) in callout_ids or str(item_id) in pdf_text_ids]
        if ids and len(included_ids) == len(ids):
            evidence = {
                "includedInOutput": True,
                "outputEvidence": "included-pdf-callout-text",
                "outputEvidenceLabel": "Περιλαμβάνεται στο DOCX ως επεξεργάσιμο πλαίσιο από PDF text. Δεν χρησιμοποιήθηκε ασφαλές DOCX donor.",
            }
        elif included_ids:
            evidence = {
                "includedInOutput": True,
                "outputEvidence": "partially-included-pdf-text",
                "outputEvidenceLabel": f"Το report δείχνει ότι {len(included_ids)}/{len(ids)} αποσπάσματα χτίστηκαν στο DOCX από PDF text.",
            }
        else:
            evidence = {
                "includedInOutput": None,
                "outputEvidence": "not-proven-by-report",
                "outputEvidenceLabel": "Το report δεν έχει ξεχωριστή απόδειξη ότι το πλαίσιο χτίστηκε στο DOCX.",
            }
        if evidence.get("includedInOutput") is True:
            continue
        markdown_rows = [row for row in rows if row.get("markdownText")]
        markdown_text = " / ".join(_compact(row.get("markdownText"), 140) for row in markdown_rows[:8])
        markdown_ids = [row.get("markdownId") for row in markdown_rows if row.get("markdownId")]
        decisions.append({
            "kind": "alignment-cluster",
            "status": "unresolved",
            "page": page,
            "id": f"page-{page}-callout-cluster",
            "ids": ids,
            "semanticType": "callout",
            "question": f"Τα {len(rows)} αποσπάσματα πλαισίου της σελίδας πρέπει να μείνουν ως PDF/Markdown ή χρειάζονται χειροκίνητη σύνδεση/διόρθωση;",
            "recommendedAction": "Έλεγχος του πλαισίου ως ενιαίου αντικειμένου, όχι γραμμή-γραμμή. Αν η οπτική απόδοση είναι σωστή, σήμανέ το OK.",
            "message": "Πολλαπλές ασύνδετες γραμμές callout συμπτύχθηκαν σε μία απόφαση χρήστη.",
            "resolution": "Το πλήρες audit κρατά κάθε γραμμή χωριστά, αλλά ο χρήστης αποφασίζει για το πλαίσιο ως ενιαίο αντικείμενο.",
            "pdfText": _compact(preview, 420),
            "docxText": "",
            "text": None,
            "latex": None,
            "score": min((float(row.get("score") or 100.0) for row in rows), default=None),
            "clusterSize": len(rows),
            "bbox": _union_bbox([row.get("bbox") for row in rows]),
            "markdownText": _compact(markdown_text, 900) if markdown_text else None,
            "markdownIds": markdown_ids,
            "markdownEvidenceSource": "markdown-pdf-spine" if markdown_text else None,
            "markdownEvidenceCount": len(markdown_rows),
            **evidence,
        })
    for index, item in enumerate(human_review_queue):
        if index in consumed:
            continue
        if str(item.get("severity") or "") != "confirm":
            continue
        output_evidence = _output_evidence(item, pdf_text_ids, callout_ids)
        if (
            output_evidence.get("includedInOutput") is True
            and item.get("kind") in {"alignment", "pdf-text-fallback", "callout-fallback"}
        ):
            continue
        decision = {
            "kind": item.get("kind"),
            "status": item.get("status"),
            "page": item.get("page"),
            "id": item.get("id"),
            "semanticType": item.get("semanticType"),
            "question": _decision_question(item),
            "recommendedAction": _decision_action(item),
            "message": item.get("message"),
            "resolution": item.get("resolution"),
            "pdfText": item.get("pdfText"),
            "docxText": item.get("docxText"),
            "text": item.get("text"),
            "latex": item.get("latex"),
            "score": item.get("score"),
            "bbox": item.get("bbox"),
            "cropPath": item.get("cropPath"),
            "finalSource": item.get("finalSource"),
            "lossStatus": item.get("lossStatus"),
            "failure": item.get("failure"),
            "matchedMarkdownDonor": item.get("matchedMarkdownDonor"),
            "markdownId": item.get("markdownId"),
            "markdownType": item.get("markdownType"),
            "markdownPage": item.get("markdownPage"),
            "markdownStatus": item.get("markdownStatus"),
            "markdownText": item.get("markdownText"),
            "markdownEvidenceScore": item.get("markdownEvidenceScore"),
            "markdownEvidenceSource": item.get("markdownEvidenceSource"),
            "markdownPageHintSource": item.get("markdownPageHintSource"),
            **output_evidence,
        }
        decisions.append(decision)
    return decisions


def build_fidelity_fallback_report(
    page_structure: dict[str, Any],
    build_report: dict[str, Any],
    content_audit: dict[str, Any],
    alignment: dict[str, Any] | None = None,
    markdown_pdf_spine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = _build_items(build_report)
    items_by_id = {str(item.get("id")): item for item in items}
    structure_items_by_id = _page_structure_items(page_structure)
    source_counts = Counter(str(item.get("source", "")) for item in items)
    type_counts = Counter(str(item.get("type", "")) for item in items)

    equation_map = (page_structure or {}).get("markdown_equation_map", {}) or {}
    mapped_equations = list(equation_map.get("equations", []) or [])
    equation_rows: list[dict[str, Any]] = []
    for mapped in mapped_equations:
        item_id = str(mapped.get("id") or "")
        built = items_by_id.get(item_id, {})
        structure_item = structure_items_by_id.get(item_id, {})
        final_source = str(built.get("source") or "")
        math_count = int(built.get("math_count", built.get("native_math_count", 0)) or 0)
        row = {
            "page": mapped.get("page"),
            "id": item_id,
            "matchedMarkdownDonor": mapped.get("donor"),
            "latexPreview": mapped.get("latexPreview"),
            "cropPath": structure_item.get("crop_path"),
            "finalSource": final_source,
            "finalMathCount": math_count,
            "lossStatus": _equation_status(final_source, math_count),
            "latexOmmlFailure": built.get("latex_omml_failure"),
        }
        row["reviewStatus"] = _equation_review_status(row)
        equation_rows.append(row)

    built_equation_rows = [item for item in items if item.get("type") == "equation"]
    mapped_ids = {str(item.get("id")) for item in equation_rows}
    for built in built_equation_rows:
        item_id = str(built.get("id") or "")
        if item_id in mapped_ids:
            continue
        final_source = str(built.get("source") or "")
        math_count = int(built.get("math_count", built.get("native_math_count", 0)) or 0)
        structure_item = structure_items_by_id.get(item_id, {})
        row = {
            "page": built.get("page"),
            "id": item_id,
            "matchedMarkdownDonor": None,
            "latexPreview": None,
            "cropPath": structure_item.get("crop_path"),
            "finalSource": final_source,
            "finalMathCount": math_count,
            "lossStatus": _equation_status(final_source, math_count),
            "latexOmmlFailure": built.get("latex_omml_failure"),
        }
        row["reviewStatus"] = _equation_review_status(row)
        equation_rows.append(row)

    equation_status_counts = Counter(str(row.get("lossStatus", "")) for row in equation_rows)
    equation_review_queue = [
        {
            "kind": "equation",
            "severity": _equation_review_severity(row),
            "page": row.get("page"),
            "id": row.get("id"),
            "status": row.get("reviewStatus"),
            "message": "Η εξίσωση έχει usable οπτική fallback μορφή αλλά όχι ασφαλές native Word math." if str(row.get("lossStatus") or "").startswith("visual-fallback") else ("Η εξίσωση έχει Markdown LaTeX αλλά δεν κατέληξε σε ασφαλές native Word math." if row.get("latexPreview") else "Η εξίσωση δεν έχει ασφαλές Markdown donor."),
            "latex": row.get("latexPreview"),
            "cropPath": row.get("cropPath"),
            "finalSource": row.get("finalSource"),
            "lossStatus": row.get("lossStatus"),
            "failure": row.get("latexOmmlFailure"),
            "matchedMarkdownDonor": row.get("matchedMarkdownDonor"),
        }
        for row in equation_rows
        if row.get("reviewStatus") != "ok"
    ]
    human_review_queue = [
        *equation_review_queue,
        *_build_alignment_review_queue(alignment or {}),
        *_build_pdf_only_review_queue(items, structure_items_by_id),
        *_build_callout_review_queue(build_report),
        *_build_content_review_queue(content_audit),
        *_build_markdown_survival_review_queue(content_audit),
        *_build_markdown_pdf_spine_review_queue(markdown_pdf_spine or {}),
    ]
    human_review_queue = _attach_markdown_evidence(human_review_queue, markdown_pdf_spine or {})
    actionable_review_queue = _actionable_review_queue(human_review_queue)
    diagnostic_review_queue = _diagnostic_review_queue(human_review_queue)
    user_decision_queue = _build_user_decision_queue(human_review_queue)
    visual_asset_counts = Counter()
    format_normalized = 0
    for page in (page_structure or {}).get("pages", []):
        for group in page.get("visual_groups", []):
            source = str(group.get("asset_source") or "")
            if source:
                visual_asset_counts[source] += 1
            match = group.get("asset_match") or {}
            if match.get("formatNormalized") or group.get("formatNormalized"):
                format_normalized += 1
    total_items = sum(type_counts.values())
    docx_strong = int(source_counts.get("docx-strong", 0) or 0)
    docx_native_omml = int(source_counts.get("docx-native-omml", 0) or 0)
    docx_supported_items = docx_strong + docx_native_omml
    docx_ratio = round(docx_supported_items / total_items, 4) if total_items else 0.0
    if docx_ratio >= 0.65:
        docx_grade = "high"
    elif docx_ratio >= 0.35:
        docx_grade = "medium"
    elif docx_ratio > 0:
        docx_grade = "low"
    else:
        docx_grade = "none"
    markdown_equation_count = int(equation_map.get("equationCount", len(mapped_equations)) or 0)
    markdown_equation_matched = int(equation_map.get("matchedEquationCount") or 0)

    return {
        "version": "fidelity-fallback-report-0.1",
        "summary": {
            "contentStatus": content_audit.get("status"),
            "pdfTokenCoverage": content_audit.get("source_to_output_pdf_token_coverage"),
            "docxTokenCoverage": content_audit.get("source_to_output_docx_token_coverage"),
            "markdownSurvivalCoverage": ((content_audit.get("markdown_survival") or {}).get("coverage")),
            "markdownSurvivalChecked": ((content_audit.get("markdown_survival") or {}).get("checked_count")),
            "markdownSurvivalMatched": ((content_audit.get("markdown_survival") or {}).get("matched_count")),
            "markdownSurvivalWeak": ((content_audit.get("markdown_survival") or {}).get("weak_count")),
            "markdownSurvivalMissing": ((content_audit.get("markdown_survival") or {}).get("missing_count")),
            "markdownPdfSpineScope": (markdown_pdf_spine or {}).get("scope"),
            "markdownPdfSpineCoverage": (markdown_pdf_spine or {}).get("coverage"),
            "markdownPdfSpineCandidates": (markdown_pdf_spine or {}).get("candidateCount"),
            "markdownPdfSpineItems": (markdown_pdf_spine or {}).get("itemCount"),
            "markdownPdfSpinePlaced": (markdown_pdf_spine or {}).get("placedCount"),
            "markdownPdfSpineWeak": (markdown_pdf_spine or {}).get("weakCount"),
            "markdownPdfSpineUnplaced": (markdown_pdf_spine or {}).get("unplacedCount"),
            "markdownPdfSpineStatusCounts": (markdown_pdf_spine or {}).get("statusCounts"),
            "markdownPdfSpineWarning": (markdown_pdf_spine or {}).get("scopeWarning"),
            "likelyMissingSourceLines": content_audit.get("likely_missing_source_line_count"),
            "formulaReviewLines": content_audit.get("formula_review_line_count"),
            "layoutJoinArtifacts": content_audit.get("layout_join_artifact_count"),
            "nativeMathCount": content_audit.get("native_math_count"),
            "rasterEquationFallbacks": content_audit.get("raster_equation_fallbacks"),
            "markdownEquationCount": equation_map.get("equationCount", len(mapped_equations)),
            "markdownEquationMatchedCount": equation_map.get("matchedEquationCount"),
            "markdownEquationUnmatchedCount": equation_map.get("unmatchedEquationCount"),
            "finalEquationCount": len(equation_rows),
            "finalEquationStatusCounts": dict(equation_status_counts),
            "equationReviewQueueCount": len(equation_review_queue),
            "userDecisionQueueCount": len(user_decision_queue),
            "userDecisionQueueCounts": _review_counts(user_decision_queue),
            "humanReviewQueueCount": len(human_review_queue),
            "humanReviewQueueCounts": _review_counts(human_review_queue),
            "humanReviewSeverityCounts": _severity_counts(human_review_queue),
            "actionableReviewQueueCount": len(actionable_review_queue),
            "actionableReviewQueueCounts": _review_counts(actionable_review_queue),
            "diagnosticReviewQueueCount": len(diagnostic_review_queue),
            "diagnosticReviewQueueCounts": _review_counts(diagnostic_review_queue),
            "itemSourceCounts": dict(source_counts),
            "itemTypeCounts": dict(type_counts),
            "docxContribution": {
                "grade": docx_grade,
                "ratio": docx_ratio,
                "supportedItems": docx_supported_items,
                "totalItems": total_items,
                "strongMatches": docx_strong,
                "nativeOmmlItems": docx_native_omml,
                "nativeMathCount": content_audit.get("native_math_count"),
                "markdownEquationMatchedCount": markdown_equation_matched,
                "markdownEquationCount": markdown_equation_count,
                "note": "Το Mathpix DOCX βαθμολογείται ως δότης μορφής/OMML/σειράς. Η πηγή αλήθειας για περιεχόμενο παραμένει το Mathpix Markdown και για σελιδοποίηση το PDF.",
            },
            "visualAssetSourceCounts": dict(visual_asset_counts),
            "formatNormalizedVisualAssets": format_normalized,
        },
        "assetResolution": (page_structure or {}).get("asset_resolution", {}),
        "equations": equation_rows,
        "equationReviewQueue": equation_review_queue,
        "userDecisionQueue": user_decision_queue,
        "humanReviewQueue": human_review_queue,
        "actionableReviewQueue": actionable_review_queue,
        "diagnosticReviewQueue": diagnostic_review_queue,
        "contentWarnings": {
            "likelyMissingLines": content_audit.get("likely_missing_lines", []),
            "formulaReviewLines": content_audit.get("formula_review_lines", []),
            "layoutJoinArtifacts": content_audit.get("layout_join_artifacts", []),
        },
    }
