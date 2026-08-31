# Reconstruction Test Protocol

This file records the mandatory testing discipline for the PDF/Mathpix → canonical model → Word reconstruction work.

## Non-negotiable rules

1. **No unannounced methodological changes.** Any change of source authority, inference rule, architecture, rendering strategy, test criterion, threshold family, or generalization strategy must be stated explicitly before implementation and must receive user approval.

2. **A blind/generalization test uses frozen rules.** Once a reference implementation is frozen, no page-specific tuning, special IDs, special coordinates, special content strings, or post-hoc corrections may be introduced before the blind result is inspected.

3. **Generalization must preserve the reference case first.** When removing page-specific logic from a successful reference-page renderer, remove one category of special handling at a time and rerun the reference page. If the reference output regresses, that generalization step is rejected before testing other pages.

4. **No hidden substitutions.** Replacing a proven renderer with a simplified or rewritten implementation is not equivalent to generalizing it. If a different implementation is proposed, it must be declared as a different experiment.

5. **Source-derived vs manual decisions must remain distinguishable.** Any property inferred automatically from MMD, Lines, PDF, or canonical evidence must be labelled as such. Any manual visual interpretation used for a diagnostic probe must be declared and must not silently become production logic.

6. **No page-specific repair during evaluation.** Failed pages are evidence about a missing general capability. They are not repaired until the failure class is identified and a source-driven general rule is proposed and approved.

7. **Renderer is downstream.** Canonical evidence and topology decisions must be complete before renderer-specific choices are made. The renderer executes the contract; it must not rematch or reinterpret sources silently.

## Current reference test

The current reference case is physical page 19 of the frozen 17–22 package. `WORD_PAGE19_PROBE_V4_AUTOFORMAT` is the successful reference behaviour for the current renderer investigation. Any generalization must first preserve that page before being applied to pages 17–22.
