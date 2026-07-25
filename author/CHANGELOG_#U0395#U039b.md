# Αλλαγές

## v0.1.0 — σταθμός υποδομής — 2026-07-23

- Κοινός renderer και κοινό CSS για συγγραφέα, βιβλίο και εκτύπωση.
- Γενικός υπολογισμός πλαισίων σκηνών από ρητή αναλογία ή ύψος.
- Αφαίρεση ειδικών υπολογισμών διάταξης συγκεκριμένης εφαρμογής.
- Ελληνική ορολογία στα κύρια σημεία του UI.
- Δημιουργία νέου κενού βιβλίου και τοπική εισαγωγή/εξαγωγή JSON.
- Τοπικό άνοιγμα βιβλίου με επιλογέα JSON όταν το `file://` μπλοκάρει `fetch`.
- Ουδέτερη σύμβαση σκηνών `book-scene-v1` με προσωρινή συμβατότητα εκτύπωσης.
- Γενικό αυτόματο μενού χωρίς λίστες τίτλων ειδικές για το βιβλίο ΗΜ κύματος.
- Το υπάρχον βιβλίο διατηρείται ως δοκιμή συμβατότητας 28 σελίδων.
- Προστέθηκε πραγματικός έλεγχος εκτύπωσης/PDF: 28 σελίδες A4 και 10/10
  στιγμιότυπα σκηνών.
- Αφαιρέθηκαν 165 γραμμές διπλού CSS από τον Συγγραφέα χωρίς γεωμετρική
  μεταβολή.
- Καταγράφηκε ότι το δοκιμασμένο print path χρησιμοποιεί ακόμη το άμεσο
  `window.getBookPrintSnapshot()` και όχι το ουδέτερο `book-scene-v1`.

## Diagnostic candidate — forced snapshot paths — 2026-07-24

- Καταργήθηκε η ταυτόχρονη αποστολή νέου και legacy snapshot message.
- Το `book-scene-v1` έγινε το πρώτο, σειριακό production candidate.
- Προστέθηκε forced-path probe για έξι απομονωμένες διαδρομές σε 10 σκηνές.
- Ο έλεγχος PDF αποτυγχάνει αν χρησιμοποιηθεί έστω και ένα fallback.
- Ο πραγματικός browser έλεγχος πέρασε 6/6 paths και η εκτύπωση πέρασε
  10/10 από `book-scene-v1` με μηδέν fallback.

## Diagnostic candidate — canonical transport cutover — 2026-07-24

- Αφαιρέθηκαν οι κλάδοι `book-scene-direct`, `window-direct`,
  `em-wave-direct`, `hm-print-snapshot` και `degraded-clone`.
- Αφαιρέθηκαν από τις εφαρμογές τα `window.getBookPrintSnapshot()`,
  `EMWaveApp.getPrintSnapshot()` και το legacy message handler.
- Το `BookScene.getPrintSnapshot()` παραμένει μόνο ως ο μοναδικός παραγωγός
  μέσα στη σκηνή· ο reader επικοινωνεί αποκλειστικά με `book-scene-v1`.
- Η εκτύπωση σταματά αν αποτύχει το canonical transport.
- Ο νέος έλεγχος απαιτεί `1/1 path · 0 legacy hooks`.

## v0.2.0 integration candidate — shared author folder — 2026-07-24

- Ο κοινός runtime μεταφέρθηκε από το προσωρινό `book-next/` στο μόνιμο
  `author/`.
- Το πραγματικό ΗΜ βιβλίο και τα assets του παραμένουν στο `book/`.
- Ο reader και ο editor δέχονται πλέον ρητά
  `?book=<διεύθυνση-json>`.
- Εικόνες, σκηνές και `appHref` επιλύονται σε σχέση με το JSON του βιβλίου και
  όχι σε σχέση με τον φάκελο του Συγγραφέα.
- Τα `book/index.html` και `book/Editor.html` έγιναν λεπτοί launchers χωρίς
  renderer.
- Τα HMK-specific regression fixtures μεταφέρθηκαν έξω από τον κοινό runtime,
  στο `book/diagnostics/author-cutover/`.
- Δεν προστέθηκε δεύτερο transport ή fallback.
