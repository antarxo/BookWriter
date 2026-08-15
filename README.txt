HF27 snapshot Reader test

Copy folder "v5" into the root of your LOCAL Git BookWriter folder.
It adds/updates only:
v5\reader\index.html

Required book file:
books\book_new\book.rendered.html

Run your usual local BookWriter HTTP server, then open:
http://127.0.0.1:PORT/v5/reader/index.html

The Reader does NOT load BookCore, book.json or pagination code.
It only opens book.rendered.html.
