# Ενεργή δομή RC1

- `author/`: ο Συγγραφέας και μόνο το UI του.
- `reader/`: το standalone ηλεκτρονικό βιβλίο και η εκτυπωτική θέαση.
- `core/`: κοινός renderer, canonical model, DOCX parser, pagination, vendor dependency.
- `books/<book_id>/`: μία πηγή αλήθειας: `book.json`, `images/`, `index.html`, `Editor.html`.

Η προεπισκόπηση Συγγραφέα, το standalone βιβλίο και η εκτύπωση χρησιμοποιούν τον ίδιο `BookCore`.
