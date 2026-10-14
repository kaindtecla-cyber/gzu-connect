# GZU Connect (Django)

One app for campus services at Great Zimbabwe University: **printing, laundry, food delivery and peer tutoring**.
Students stop hunting through WhatsApp groups and book a specific service in one place. Built for HCS223 Technopreneurship.

## What works
- Accounts: sign up (name, phone, registration number, hall) and log in
- **Wallet** with a ledger of every top-up, payment, refund and payout
- **Food**: browse vendors, order from their menu (prices always come from the database)
- **Print**: upload a document (PDF/Word/PowerPoint/JPG/PNG, max 10 MB), choose a print point, pages, copies, colour and slot. Files are private: only the customer and that print vendor can download them
- **Laundry**: pick a laundry, bag size and pickup slot
- **Tutoring**: search by module and book a tutor by the hour
- **Escrow**: payment is held when the student pays and released to the vendor or tutor when the job is marked complete. GZU Connect keeps a 12% commission
- **Vendor / tutor dashboard** ("My jobs"): see bookings, move them forward, or decline before work starts (full refund)
- Cancel before work starts, ratings after completion, admin panel with total commission earned
- **Real EcoCash / OneMoney top-ups through Paynow** (switch on with `PAYMENTS_MODE=paynow`; otherwise top-ups are simulated)
- **Alerts**: vendors and tutors are told the moment a paid booking arrives (in-app badge, email, optional SMS); students are told at every step. "My jobs" refreshes itself every 30 seconds

## Run it locally
```
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo        # example vendors, tutors and accounts
python manage.py runserver
```
Open http://127.0.0.1:8000 and log in as `student` / `gzuconnect-demo` (the local-only demo password).
Other demo logins (same password): `vendor_food1`, `vendor_print`, `vendor_laundry`, `tatenda` (a tutor).
Create your own admin with `python manage.py createsuperuser`, then open `/admin/`.
**The demo accounts are for testing only. On a live site, `seed_demo` refuses to run unless you set your own `DEMO_PASSWORD`.**

## Tests
`python manage.py test` runs 41 tests covering commission rounding, payment and refund rules, double-payment and overspend protection, payouts, permissions, upload validation, the full order flow, Paynow signing and webhook security, and notifications.

## Deploy on Render
1. Push this folder to GitHub.
2. On Render choose **New > Blueprint**, select the repo. `render.yaml` creates the web service and a Postgres database.
3. When asked, enter `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL` and `DJANGO_SUPERUSER_PASSWORD` (the build creates your admin account, because the free plan has no shell) and a private `DEMO_PASSWORD`. To load example vendors and tutors for a demo, set `SEED_DEMO` to `1` and redeploy. Otherwise add vendors in `/admin/`.
4. Free-plan limits (Render's docs, checked October 2026): the site sleeps after 15 minutes idle and takes about a minute to wake; the free Postgres database expires 30 days after creation; **uploaded print files are lost on every restart or redeploy.** For real use, upgrade the web service and database and store uploads in cloud storage (for example S3).

## Real payments with Paynow (EcoCash / OneMoney)
1. Create a merchant account at paynow.co.zw and add an integration. Copy the **Integration ID** and **Integration Key**, and enable EcoCash and/or OneMoney for it. Docs: https://developers.paynow.co.zw
2. Set these environment variables (locally in a `.env`/shell, on Render under Environment):
   `PAYMENTS_MODE=paynow`, `PAYNOW_ID`, `PAYNOW_KEY`, `PAYNOW_AUTH_EMAIL` (your Paynow login email), `SITE_URL` (your public address, no trailing slash).
3. **Test first.** Paynow's test mode moves no money and has special test phone numbers (see their "Test mode" page). When it all works, use "Request to be Set Live" in your Paynow dashboard.
4. How it works: the student enters amount and number, Paynow pushes a PIN prompt to their phone, and the wallet is credited once Paynow confirms. Confirmation arrives two ways (Paynow calls `/paynow/result/`, and the waiting page polls), every message is checked against its SHA512 signature, the amount must match, and a payment can only ever credit once.
5. The signing code is tested against Paynow's published example hash. Paynow itself was not reachable from the build environment, so the first real test in Paynow test mode is your proof it works end to end.

## Alerts
- **In-app** (always on): the Alerts tab with an unread badge.
- **Email**: set `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` (for example a Gmail address with an app password). Without them, emails print to the server log.
- **SMS** (optional, off by default): `SMS_PROVIDER=twilio` plus `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM`. Twilio's own Zimbabwe guidelines say sender-ID registration is not supported there, so delivery can be unreliable. Test with a real vendor number before depending on it.
- A failing email or SMS never blocks a booking; it is logged and the in-app alert still appears.

## Known gaps (say these honestly when presenting)
- **Vendor cash-out is not built.** Vendors are paid into their GZU Connect wallet; withdrawing to their EcoCash needs a payout feature.
- **Holding customers' money in a wallet may be regulated.** Before taking real payments from the public, check the rules for payment services in Zimbabwe (or use Paynow's merchant flow) with Paynow or a lawyer.
- No delivery tracking or maps; no login rate-limiting yet.

## Notes
- Money uses `Decimal` and all wallet changes run in locked database transactions.
- Demo prices, vendor and tutor names are example data.
- The logo and service photos in `core/static/core/img` came from third-party sources. Replace the service photos with your own before any public release.
