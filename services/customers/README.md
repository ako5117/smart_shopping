# Customers Service

Customer accounts and loyalty points for the online shop. It runs internally: only the online shop and the store dashboard call it.

    uvicorn customers.api:create_app --factory --port 8090

## Accounts

- **Sign up** (the shop's Account tab):
  - name, phone number, email and date of birth
  - gender and a profile photo, both optional
  - acceptance of the privacy notice
  - nationality isn't asked
  - customers must be 18 or over; younger customers would need a parent's consent under the Data Protection Act
- **Email check:** a 6-digit code goes to the email, and entering it confirms the address and signs the customer in.
- **Signing in later:** the customer enters their email and types the code that arrives. There's no password to forget or leak.
- **Codes:**
  - each lasts 10 minutes and allows 5 tries
  - at most 3 are sent to one email in 15 minutes
  - only a hash is stored
- **Sessions:**
  - last 30 days
  - the shop keeps the token in an HttpOnly, SameSite=Lax cookie (Secure over HTTPS)
  - only its hash is stored here
- **Profile:** the customer can change their details or photo on the Account tab.
  - Photos are cropped to a square, re-encoded as a JPEG of up to 400 px, and stripped of metadata such as location.
- **Delete account:** erases the name, phone, email, date of birth, gender, photo, sessions and codes. Past orders and points stay as anonymous records for the store's accounts.
- **What staff see:** names, join dates, masked emails and points. Staff never see dates of birth or gender.

## Points

| Setting | Default | |
|---|---|---|
| `LOYALTY_ENABLED` | `true` | `false` switches the programme off. Accounts and order history keep working, nothing is earned or spent, and balances are kept for when it's switched back on. |
| `LOYALTY_KES_PER_POINT` | 100 | 1 point per KES 100 spent on goods. The delivery fee and any points discount don't count. |
| `LOYALTY_REDEEM_POINTS` | 100 | Points are spent in blocks of this many... |
| `LOYALTY_REDEEM_KES` | 50 | ...and each block takes this much off an order. |

How points work on an order:

- **Earning:** points are added once the order is paid. A background check asks the Inventory Service about pending orders every `CUSTOMERS_POLL_S` seconds (default 15). The customer's own page asks straight away.
- **Spending:** at checkout the customer switches on **Use points**. The discount is worked out here, in whole blocks, and is always less than the goods total, so something is always paid. The Inventory Service records it on the sale (`loyalty_discount_kes`), so it's part of what M-Pesa or the card charges.
- **Refunds of points:** if an order is cancelled, expires unpaid, or never reaches the Inventory Service, the points spent on it come back.
- **The ledger:** every change is a row in `points`, so the balance is the sum. Each order earns, spends or refunds at most once.

## Problem reports

Customers report problems and ideas from the online shop (Account tab, or **Problem with this order?** on an order page) and from Scan & Go (**Report a problem** at the bottom). Staff answer them on the dashboard's **Reports** page.

- **What a report holds:**
  - its kind: an order, the store, the app isn't working, or an idea
  - the message, and an order number when it's about one
  - a phone or email if the customer wants to be contacted
  - an optional photo or screenshot, re-encoded at up to 1280 px with its metadata dropped
- **App problems** also carry what helps fix them: the page, the screen size, the browser, and the last errors the page saw. The customer doesn't have to type any of it.
- **Following up:**
  - Signed-in customers see their reports, the status and the store's reply on their Account tab.
  - Others get a reference, and the shop remembers it on that phone so they can see the reply.
- **Statuses:** new → being looked at → resolved. A report can only be resolved with a reply to the customer, and can be reopened.
- **Limits:** the shop and Scan & Go accept a few reports an hour from one network.
- **Deleting an account** clears the contact details on that customer's reports.
- **Without the Customers Service** there are no reports: the link doesn't appear.

## Email (stand-in)

`EMAIL_PROVIDER=outbox` is the only option for now. Emails are kept in the `emails` table instead of being sent, and managers see them on the dashboard's **Loyalty** page. For a demo, read the sign-up code there.

A supermarket connects its own email service by adding a sender to `customers/mailer.py` with the same `send(to, subject, body)` method, for example SMTP or their provider's API, and choosing it with `EMAIL_PROVIDER`. Until then real customers can't confirm their accounts, and `scripts/preflight.sh` warns about it on a live store. The dashboard shows the outbox only while the stand-in is in use.

## Later

- **Language preference** for each customer, with the shop translated to match.
- **Points for Scan & Go:** Scan & Go purchases don't earn points yet.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/settings` | Loyalty rates, whether points are on, the email provider |
| `POST` | `/signup` | `{name, phone, email, dob, gender?, consent}`: create the account (or update one never confirmed) and email a code |
| `POST` | `/codes` | `{email}`: a sign-in code, if the email has an account. The answer is the same either way |
| `POST` | `/sessions` | `{email, code}`: confirm and sign in. Returns `{token, customer}`. A wrong code gets `422` with `attempts_left` |
| `DELETE` | `/sessions` | Sign out (`X-Session`) |
| `GET` / `PUT` / `DELETE` | `/me` | The signed-in customer (`X-Session`): profile with points; change details; delete the account |
| `PUT` / `GET` / `DELETE` | `/me/photo` | Profile photo |
| `GET` | `/me/orders` | Their online orders, with points earned and spent |
| `POST` | `/orders` | `{sale_id, goods_kes, use_points}` (`X-Session`): link an order before it's recorded, spending points if asked. Returns the discount |
| `POST` | `/orders/{sale_id}/cancel` | The order didn't go ahead: give back the points spent on it |
| `POST` | `/reports` | `{kind, message, app, order_id?, contact?, context?, photo_b64?}` (`X-Session` optional): a problem report |
| `GET` | `/reports?status=` | Dashboard: reports (default `open,in_progress`) and counts by status |
| `POST` | `/reports/{id}` | `{by, status?, reply?}`: answer, move along, resolve (needs a reply) or reopen |
| `GET` | `/reports/{id}/public`, `/reports/{id}/photo`, `/me/reports` | Status and reply for whoever has the reference; the photo; the signed-in customer's reports |
| `GET` | `/summary`, `/members`, `/emails` | Dashboard: totals, newest members, the stand-in outbox |
