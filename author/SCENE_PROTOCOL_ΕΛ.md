# Σύμβαση σκηνών `book-scene-v1`

Η σύμβαση επιτρέπει σε οποιαδήποτε εκπαιδευτική εφαρμογή να δίνει σκηνές στο
βιβλίο χωρίς ο renderer να γνωρίζει το αντικείμενο ή την εσωτερική της δομή.

## 1. Δήλωση σκηνής στο βιβλίο

```json
{
  "type": "scene",
  "id": "oscillation-energy",
  "title": "Ενέργεια ταλάντωσης",
  "singleSrc": "../oscillator/index.html?scene=energy&time=0&play=0",
  "placement": "wide",
  "frameWidth": 714,
  "aspectRatio": "16/9",
  "hideCaption": false
}
```

Η αρχική κατάσταση πρέπει, όπου είναι δυνατό, να κωδικοποιείται στο URL. Έτσι η
σκηνή είναι αναπαραγώγιμη και λειτουργεί πριν από οποιαδήποτε επικοινωνία
JavaScript.

## 2. Μοναδικός παραγωγός στιγμιοτύπου

Η εφαρμογή εκθέτει έναν μόνο παραγωγό:

```js
window.BookScene = Object.freeze({
  protocol: "book-scene-v1",
  getPrintSnapshot
});
```

Το `getPrintSnapshot()` επιστρέφει data URL εικόνας. Δεν εκτίθεται δεύτερο
snapshot API στο `window`, στο αντικείμενο της εφαρμογής ή σε ειδικό όνομα
συγκεκριμένου βιβλίου.

## 3. Μοναδικό transport

Ο Συγγραφέας στέλνει:

```js
{
  type: "book-scene:capture-print-snapshot",
  protocol: "book-scene-v1",
  requestId: "μοναδικό-id"
}
```

Η εφαρμογή απαντά:

```js
{
  type: "book-scene:print-snapshot",
  protocol: "book-scene-v1",
  requestId: "μοναδικό-id",
  executedPath: "book-scene-v1",
  dataUrl: "data:image/png;base64,..."
}
```

Το `requestId` επιστρέφεται αμετάβλητο. Ο reader δέχεται το αποτέλεσμα μόνο όταν:

- η απάντηση προέρχεται από το iframe που δέχτηκε το αίτημα,
- ο τύπος και το πρωτόκολλο είναι ακριβώς τα αναμενόμενα,
- `executedPath === "book-scene-v1"`,
- το αποτέλεσμα είναι έγκυρο `data:image/...` URL.

## 4. Κανόνες στιγμιοτύπου

- Ίδια κατάσταση, κάμερα, ορατά επίπεδα και γραφήματα με τη ζωντανή σκηνή.
- Χωρίς χειριστήρια που δεν έχουν νόημα στο χαρτί.
- Αδιαφανές λευκό φόντο, εκτός αν η σκηνή απαιτεί άλλο.
- Επαρκής ανάλυση για το πραγματικό μέγεθος εκτύπωσης.
- Καμία αλλαγή αναλογίας πλαισίου ανάμεσα σε οθόνη και εκτύπωση.

## 5. Πολιτική αποτυχίας

Ο reader κάνει έως δύο προσπάθειες του ίδιου `book-scene-v1`. Αν αποτύχουν:

- δεν δοκιμάζει άλλο API,
- δεν στέλνει legacy μήνυμα,
- δεν κατασκευάζει κρυφό clone,
- δεν ανοίγει την κανονική ροή εκτύπωσης,
- επιστρέφει κόκκινο diagnostic report.

Η δεύτερη προσπάθεια είναι retry του ίδιου transport, όχι fallback.

## 6. Κλάδοι που αφαιρέθηκαν

Από το ενεργό σύστημα αφαιρέθηκαν:

1. το άμεσο `BookScene.getPrintSnapshot()` από τον reader,
2. το παλιό global `window.getBookPrintSnapshot()`,
3. το `EMWaveApp.getPrintSnapshot()`,
4. το μήνυμα `capturePrintSnapshot` / `hm_print_snapshot`,
5. το κρυφό `degraded-clone`.

Το `BookScene.getPrintSnapshot()` παραμένει μόνο μέσα στην εφαρμογή σκηνής ως ο
μοναδικός παραγωγός που καλεί ο message handler. Ο reader δεν το καλεί άμεσα.

Το `legacy-baseline.html` παραμένει αποκλειστικά ως παγωμένο fixture για
γεωμετρική σύγκριση του renderer. Δεν συμμετέχει στο production print path.

## 7. Diagnostic gate

Το `snapshot-path-check.html` απαιτεί ταυτόχρονα:

- 10/10 εκτελέσεις `book-scene-v1`,
- μηδέν fallbacks και mismatches,
- 10/10 σκηνές με το κανονικό `BookScene`,
- μηδέν παλιά global snapshot APIs,
- μηδέν απαντήσεις στο legacy message,
- απουσία clone engine.

Το `print-check.html` ανοίγει την εκτύπωση μόνο με 10/10 στιγμιότυπα από
`book-scene-v1` και `0 fallback`.

## 8. Ιστορική ακολουθία απόδειξης

Στις 24 Ιουλίου 2026:

1. Το αρχικό PDF πέρασε από το παλιό same-origin
   `window.getBookPrintSnapshot()`.
2. Το forced-path probe απέδειξε χωριστά και τους έξι τότε διαθέσιμους δρόμους.
3. Η κανονική εκτύπωση πέρασε 10/10 από `book-scene-v1` με μηδέν fallback.
4. Μετά από αυτή την απόδειξη αφαιρέθηκαν οι πέντε compatibility κλάδοι.

Η ακολουθία αυτή επιτρέπει να ξέρουμε ότι οι κλάδοι αφαιρέθηκαν επειδή ο νέος
δρόμος είχε ήδη αποδειχθεί, όχι επειδή απλώς θεωρήθηκαν περιττοί.
