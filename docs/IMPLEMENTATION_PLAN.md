# BookWriter implementation plan

## Non-negotiable contract

- Every book lives in `books/<book-id>/`.
- The book data file is **only** `book.json`.
- No fallback to historical filenames.
- Required identity: `meta.id`, `meta.title`, `meta.version`, `meta.language`.
- No global application URL. Every scene declares its own application URL.
- Only `book-scene-v1` is valid for printable scenes.

## Checkpoints

1. **Book identity and strict `book.json`** — current checkpoint.
2. **Print repair and old/new PDF parity** — 28 pages, 10 canonical snapshots, pixel comparison.
3. **Local workspace home** — select the local BookWriter directory and list `books/*/book.json`.
4. **Create book** — create `books/<id>/book.json`, launchers and assets directory.
5. **Direct local save** — File System Access API, dirty/saved state, no Git credentials in BookWriter.
6. **Scene catalogs** — applications publish declared `scene-catalog.json`; BookWriter inserts only declared scenes.
7. **Local/public application mapping** — local preview URLs remain local settings; public URLs are stored in books.
8. **Production validation** — schema, missing assets, scene protocol, print and second-book test.
