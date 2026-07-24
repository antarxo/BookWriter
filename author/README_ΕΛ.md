# Γενικός Συγγραφέας βιβλίων

Ο φάκελος `author/` περιέχει αποκλειστικά τον κοινό reader, editor, renderer,
CSS, schema και μηχανισμό εκτύπωσης. Δεν περιέχει βιβλίο ή κώδικα συγκεκριμένης
εφαρμογής.

## Αυστηρό συμβόλαιο βιβλίου

Κάθε βιβλίο βρίσκεται αποκλειστικά στη μορφή:

```text
books/<book-id>/
  book.json
  index.html
  Editor.html
  images/
```

Το αρχείο δεδομένων ονομάζεται **πάντα `book.json`**. Δεν υπάρχει δεύτερο όνομα,
fallback ή αυτόματη αναζήτηση παλιού αρχείου.

Το `book.json` απαιτεί:

```json
{
  "schemaVersion": "pages-v1",
  "meta": {
    "id": "book-id",
    "title": "Τίτλος βιβλίου",
    "version": "1.0.0",
    "language": "el"
  }
}
```

Το `meta.id` ταυτίζεται με το όνομα του φακέλου του βιβλίου. Δεν υπάρχει γενική
εφαρμογή βιβλίου. Κάθε στοιχείο `scene` δηλώνει το δικό του πλήρες URL.

## Δημόσια κλήση

```text
author/index.html?book=../books/<book-id>/book.json
author/Editor.html?book=../books/<book-id>/book.json
```

Οι σχετικές εικόνες επιλύονται από τον φάκελο του `book.json`. Οι σκηνές
αποθηκεύουν πλήρη δημόσια URLs και χρησιμοποιούν αποκλειστικά το
`book-scene-v1`.

## Εκτύπωση σκηνών

Κάθε εφαρμογή σκηνής εκθέτει:

```js
window.BookScene = Object.freeze({
  protocol: 'book-scene-v1',
  getPrintSnapshot
});
```

Δεν υπάρχουν compatibility APIs ή fallback transports. Αν μία σκηνή δεν
απαντήσει από τον canonical δρόμο, η εκτύπωση αποτυγχάνει εμφανώς.

## Έλεγχος συμβολαίου

Από τη ρίζα του repository:

```text
node tools/validate-book-contract.mjs
```

Ο έλεγχος αποτυγχάνει αν λείπει `book.json`, αν υπάρχει ιστορικό όνομα αρχείου,
αν λείπει απαιτούμενο metadata, αν το `meta.id` δεν συμφωνεί με τον φάκελο ή αν
υπάρχει γενικός σύνδεσμος εφαρμογής.
