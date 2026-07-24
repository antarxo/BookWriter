# Γενικός Συγγραφέας βιβλίων

Ο φάκελος `author/` περιέχει αποκλειστικά τον κοινό μηχανισμό συγγραφής,
ανάγνωσης και εκτύπωσης. Δεν περιέχει βιβλίο, εικόνες ή κώδικα συγκεκριμένης
εφαρμογής.

## Δημόσια κλήση

Κάθε βιβλίο καλεί τον ίδιο Συγγραφέα δίνοντας τη διεύθυνση του JSON του:

```text
author/index.html?book=../book/chapter_content.json
author/Editor.html?book=../book/chapter_content.json
```

Η παράμετρος `book` επιλύεται σε σχέση με τη σελίδα του Συγγραφέα. Όλα τα
σχετικά paths μέσα στο JSON — εικόνες, σκηνές και `appHref` — επιλύονται σε
σχέση με το ίδιο το αρχείο του βιβλίου. Έτσι ο φάκελος του βιβλίου μετακινείται
χωρίς να μεταφέρεται ή να αντιγράφεται ο renderer.

## Όρια φακέλων

```text
author/
  index.html
  Editor.html
  book-core.js
  book-core.css
  snapshot-transport.js
  book-schema-v1.schema.json

book/
  index.html             λεπτός launcher
  Editor.html            λεπτός launcher
  chapter_content.json   περιεχόμενο
  images/                assets του βιβλίου
```

Ο launcher δεν έχει renderer. Μεταφέρει μόνο τις παραμέτρους URL και ορίζει το
`book=...`. Η πηγή αλήθειας για σελιδοποίηση είναι πάντοτε το
`author/book-core.js` μαζί με το `author/book-core.css`.

## Σκηνές και εκτύπωση

Το μοναδικό production transport είναι το `book-scene-v1`. Κάθε εφαρμογή
σκηνής εκθέτει εσωτερικά:

```js
window.BookScene = Object.freeze({
  protocol: 'book-scene-v1',
  getPrintSnapshot
});
```

Δεν υπάρχουν compatibility APIs, legacy messages ή clone fallback. Αν μια
σκηνή δεν απαντήσει από τον canonical δρόμο, η εκτύπωση σταματά.

## Νέο βιβλίο

1. Δημιούργησε νέο φάκελο βιβλίου με JSON και assets.
2. Άνοιξε `author/Editor.html?book=<σχετικό-path-του-json>`.
3. Εξήγαγε το JSON στον φάκελο του βιβλίου.
4. Σύνδεσε την εφαρμογή απευθείας με
   `author/index.html?book=<σχετικό-path-του-json>` ή χρησιμοποίησε λεπτό
   launcher όπως το `book/index.html`.

Το πρώτο παραγωγικό βιβλίο αυτής της διάταξης είναι το ΗΜ κύμα. Η ΑΑΤ είναι ο
πρώτος νέος πελάτης και θα κρατήσει σε δικό της φάκελο μόνο περιεχόμενο και
ΑΑΤ-specific adapters/δεδομένα.

## Κατάσταση

Η απομόνωση φακέλου και η αλλαγή του production link είναι
`diagnostic-only / integration candidate` μέχρι να περάσουν ξανά:

- 28/28 σελίδες του ΗΜ βιβλίου,
- 10/10 snapshots από `book-scene-v1`,
- 0 fallback και 0 legacy hook,
- raster σύγκριση της τελικής εκτύπωσης με το κλειδωμένο PDF.
