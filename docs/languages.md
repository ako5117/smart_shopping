# Languages

Customers can use the online shop and Scan & Go in **English** or **Kiswahili**. Staff apps (the dashboard and the rider app) are in English.

> **Who wrote the Kiswahili:** all of it was written by Claude, an AI assistant, while building these features. No Kiswahili speaker has checked it yet. If a customer or staff member finds a translation wrong or odd, that's where it came from. See **Before going live** below.

## What the customer sees

- **Choosing:** the button at the top right of the online shop and Scan & Go switches the language (**SW** or **EN**).
  - A phone set to Kiswahili starts in Kiswahili; any other phone starts in English.
  - The choice is remembered on the phone, so the online shop and Scan & Go on that phone match.
- **Signed-in customers:** the language is kept on their account. Signing in on another phone brings it with them. Changing it while signed in updates the account.
- **What changes:**
  - All of both apps: buttons, labels, messages, the order page and the receipt.
  - The server's messages, for example "Enter a valid Safaricom number".
  - What the store's services add: delivery times ("Leo 14:00–16:00"), why an item can't be returned, offers ("Punguzo la 15%").
  - The sign-up and sign-in emails.
  - The **SMS texts** about an online order: each order records the language it was placed in.
- **What doesn't:** product names and categories, what staff type (a reply to a report, why a return was refused), and M-Pesa's own messages.

## How it works

- **Pages:** each page holds a table from each English text to its Kiswahili (`SW` in its script). Fixed text is marked `data-t`, and the script passes the text it writes through `t("...")`. Anything missing from the table shows in English.
- **Server messages:** the page sends the language it's showing in an `X-Language` header (`en` or `sw`). Errors are written in English in the code. `i18n.py` translates them on the way out, using its catalogue. The same file is in `apps/storefront` and `apps/scan_and_go`.
- **Services:** the Customers Service keeps each customer's language (`PUT /me/language`) and writes its emails in it. The Inventory Service records an order's language (`language` on the sale). The Notification Service texts in it.

## Tests keep it complete

- `tests/test_i18n_pages.py` in each app checks two things:
  - every marked text on the page has its Kiswahili;
  - every Kiswahili entry is still used (so a reworded English text doesn't quietly fall back to English).
- `tests/test_i18n.py` reads the code and checks every message the server can send a customer is in the catalogue. That includes messages the online shop passes on from the Customers, Dispatch and Inventory services.
- So a new or changed English text fails the tests until its Kiswahili is added.

## Changing a text, or adding a language

- **Changing a text:** change the English, run the tests, and add the Kiswahili they ask for.
- **Adding a language** (French or Arabic for an airport shop, for example):
  - a table next to `SW` in each page, and the language in `LANGS`;
  - a catalogue next to `SW` in `i18n.py`, added to `LANGUAGES` and `CATALOGUES`;
  - the emails in `services/customers/customers/service.py` (`EMAILS`) and the texts in `services/notify/notify/texts.py`;
  - the allowed values on the Customers and Inventory services (`en`, `sw`).
  - The language button switches between two languages; with a third it needs to become a short list to choose from.

## Before going live

The Kiswahili was written by Claude (an AI assistant) for this release; it is not a professional or native-speaker translation. Have a native speaker read through it in the app before customers use it, especially:

- the privacy notice (what customers agree to when they make an account);
- the SMS texts and emails (they go to customers' phones and inboxes);
- payment and refund messages.

Corrections go in the same tables: `SW` in each page, `SW` in `i18n.py` (both copies), `EMAILS` in the Customers Service, and `services/notify/notify/texts.py`. The tests then check nothing is left out.
