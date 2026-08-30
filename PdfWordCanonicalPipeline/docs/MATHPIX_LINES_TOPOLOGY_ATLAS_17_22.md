# Mathpix Lines Topology Atlas — pages 17–22

Purpose: interpret Mathpix Lines structural signals against the original PDF as ground truth, without making the original PDF a runtime dependency of the Lines-only reconstructor.

## Locked observation policy

- `bbox` of an atomic object is strong geometry evidence for that object.
- `parent_id` / `children_ids` is strong evidence of local structural grouping / reading stream.
- `column` is **not** treated as a Word column instruction.
- A container bbox is **not** assumed to be continuously occupied; it is often just the union envelope of its descendants.
- `page_info` is **not** treated as synonymous with header/footer.
- `selected_labels` is treated as an explicit relation between a diagram and its label/caption.
- The original PDF is used only to learn/validate the interpretation of Lines signals.

## Page 17

Observed Lines structure:
- 4 `column` objects total, 3 top-level.
- top-left Planck quote is a compact `column` with 5 text children.
- chapter banner is a `column` with nested `section_header` / nested `column` structure.
- left explanatory stream is a `column` with 27 children (section header, diagram, text).
- the main body beginning `1. Ποιες θεωρίες...` is mostly **top-level unparented text**, not inside a `column`.

Interpretation against original PDF:
- the Planck quote is an independent small box/stream.
- the chapter banner is a composite decorative/header region, not ordinary body flow.
- the left stream contains a diagram and multiple explanatory notes.
- the main article is an independent main flow to the right of/around those objects.

Rule learned:
- being in a Mathpix `column` is not required for main text flow.
- nested `column` can represent a composite banner/container rather than page columns.

## Page 18

Observed Lines structure:
- no `column` objects at all.
- 2 `diagram` objects and 2 `figure_label` objects.
- each major diagram has a `selected_labels` relation pointing to its figure label.
- ordinary body text is top-level.

Interpretation against original PDF:
- the page is predominantly normal flow with large embedded figures and captions/side labels.

Rule learned:
- a page may have meaningful complex layout without any Mathpix `column` object.
- `selected_labels` is stronger evidence for figure-caption association than geometric guessing.

## Page 19

Observed Lines structure:
- exactly 2 top-level `column` objects.
- left stream bbox ~438 px wide, 42 children.
- main stream bbox ~1318 px wide, 42 children.
- both streams are vertically dense (coverage ~0.84–0.85), with the left stream containing a diagram, text and math.

Interpretation against original PDF:
- this is a genuine stable two-stream layout: narrow ancillary stream at left and main body stream at right.

Rule learned:
- `column` can correspond well to a real persistent page stream when its descendants densely occupy its bbox and its width/position is stable.
- renderer decisions should therefore be evidence-based, not based merely on the presence of `type=column`.

## Page 20

Observed Lines structure:
- 2 top-level `column` objects.
- main stream bbox x=112..1428.
- right ancillary stream bbox x=1252..1963.
- the two container bboxes overlap horizontally by ~176 px.
- right ancillary stream has a very large internal vertical gap (~502 px), with upper figure/text, then later a lower diagram/text block.

Interpretation against original PDF:
- right content is not a single solid rectangular column; it is a sequence of spatially separated ancillary objects beside the main flow.

Rule learned:
- container bbox is an envelope, not an occupied region.
- large internal gaps are evidence that a stream must be split into local occupancy segments before layout reconstruction.
- overlapping container envelopes do not imply overlapping atomic content.

## Page 21

Observed Lines structure:
- 2 top-level `column` objects.
- narrow left stream has diagrams and text with a large internal gap (~299 px).
- broad right/main stream is relatively dense.

Interpretation against original PDF:
- left side is an ancillary stream of images/notes; main body remains to the right.

Rule learned:
- persistent side streams may still need segmentation into local blocks because their container bbox is not continuously occupied.

## Page 22

Observed Lines structure:
- 1 main `column` containing body content, headings, multiple choice, math, diagram and figure label.
- 3 large top-level `page_info` containers at the right, each containing substantial explanatory text; the first also contains a diagram.
- these are not page header/footer metadata.
- a diagram in the main stream has `selected_labels` pointing to its `figure_label`.

Interpretation against original PDF:
- the three `page_info` containers are visible right-hand explanatory sidebars/callouts.

Rule learned:
- `page_info` is a Mathpix classification hint, not a safe renderer semantic.
- container geometry + child content + page position are needed to distinguish actual header/footer from sidebar/callout content.

# Cross-page topology grammar

## Strong signals

1. **Atomic bbox** — authoritative local geometry evidence.
2. **Parent/children relations** — strong local stream/container evidence.
3. **Nested containers** — evidence of composite structures.
4. **`selected_labels`** — explicit diagram ↔ label/caption relation.
5. **Object type + font size** — useful semantic/typographic evidence, but not sufficient alone for Word structure.

## Signals that must not be mapped literally

1. `column` != Word column.
2. `column` bbox != continuously occupied region.
3. `page_info` != necessarily header/footer.
4. `line` != global Word reading order.
5. numeric `column` field != safe Word column index.

# Proposed reconstruction model after the atlas

Do **not** start from `page -> columns -> content`.

Use:

`page -> atomic objects -> local streams/containers -> occupied segments -> relations between streams -> Word-native layout decision`

For each container:
1. preserve the container relationship;
2. measure the actual descendant occupancy;
3. split on large vertical gaps into local occupied segments;
4. compare segment geometry with neighboring top-level/main-flow objects;
5. classify the segment only then (main flow, persistent side stream, sidebar/callout, banner/header composite, figure/caption group, etc.).

Only after that classification should the Word renderer choose among normal flow, native multi-column section, floating/around object, table/container, or other native Word layout primitive.

## Next engineering checkpoint

Build a **Lines-only occupancy-segment graph**. No PDF input at runtime, no Word rendering change yet. Its output should expose:
- atomic objects;
- container membership;
- descendant occupancy segments;
- large gaps;
- explicit figure-label edges;
- candidate main-flow stream(s);
- candidate ancillary streams;
- unresolved ambiguous relations.

Then validate that graph against pages 17–22 before modifying the native Word renderer.
