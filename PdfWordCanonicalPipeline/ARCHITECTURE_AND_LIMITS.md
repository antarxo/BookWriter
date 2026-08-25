# Architecture and limits

Version: `0.4.7-hybrid-composite-equations`

## Canonical DOCX gateway

There is one user-facing DOCX gateway and one low-level contract: `canonical-word-v1`.

For ordinary DOCX, complex Word groups are handled by a Windows Word COM preprocessor. The preprocessor creates a faithful PNG background and stores original OMML equation boxes plus normalized nested-group geometry in `customXml/bookwriter-composites.json`. The browser importer does not reinterpret or OCR these equations.

For reconstructed PDF material, Word COM remains disabled and the controlled PDF geometry/asset pipeline is used.

## Limits

- Microsoft Word COM cannot be executed in the Linux build container.
- Only equation overlays are editable in this checkpoint; arrows, brackets, labels and decorative shapes remain in the background.
- Rotated equation text boxes and header/footer story ranges are not reconstructed as overlays.
- Word contour wrapping is approximated by the replacement shape contract.
