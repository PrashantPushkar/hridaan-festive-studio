# Festive Studio · Hridaan Labs

A standalone festive greeting product extracted from ISMEBangalore/ISMESocialHub. It keeps the original festival catalogue, art-direction prompts, channel dimensions, fonts and Pillow compositor. Social Hub is unchanged.

## What works

- Choose an Indian festival and enter your own brand name.
- Generate greeting copy and art direction with your personal OpenAI project; generate artwork with GPT Image 2.
- Choose Instagram post / WhatsApp chat (1080×1350), or Instagram story / WhatsApp status (1080×1920).
- Use the original illustrated template mode without an API call.
- Review and edit the headline, tagline, sign-off and message. Updating the preview rerenders the saved artwork without another AI image charge.
- Approve or set aside a greeting; download its PNG and copy its message.
- Save the collection in an independent SQLite database, isolated by browser session.

Nothing is sent to Social Hub, WhatsApp recipients or an ISME database. Approval marks the greeting as approved; distribution is manual. The 2026 catalogue combines inherited occasions with researched additions and regional source notes. Dates may vary regionally; check the selected source before scheduling.

## Run locally

Python 3.12 is recommended.

```sh
python -m venv .venv
# Activate .venv for your operating system, then:
python -m pip install -r requirements.txt
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. The personal key is loaded from the ignored `.env.local` file. Without a key, illustrated template mode still works. See `.env.example` for non-secret configuration names. Never commit `.env.local` or reuse the Social Hub organisation key.

## Deploy as a private product

The Dockerfile and `railway.json` deploy one Python service. Connect the new personal GitHub repository to Railway (or another Docker-capable host), then configure the personal `OPENAI_API_KEY` in the host's secret settings, `APP_ENV=production`, a strong `APP_PASSWORD` of at least 12 characters, and an independent `SESSION_SECRET` of at least 32 characters. The server refuses production startup without these safeguards. The Dockerfile deliberately excludes local env files and never embeds the key in the image.

Attach persistent storage at `/app/data` (writable by the `studio` container user), or set `DATA_DIR` to another persistent writable mount. SQLite, generated PNGs and saved backgrounds live there. Serve over HTTPS so the production session cookie works. Use one instance and one worker; the generation queue and throttle are process-local. `DAILY_GENERATION_LIMIT` defaults to 20 jobs per day across the product. AI failures are labelled as template fallbacks in the interface.

This initial private studio is a single-password product, not a public multi-user SaaS. Collections are linked to the browser session, retained for 30 days; clearing cookies loses access to that collection. Add account-based authentication and per-user quotas before opening paid generation to the public. The Hridaan Labs marketing website is intentionally not modified at this stage.

## Verify

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests cover generation, editing without another image request, approval/rejection, the four export sizes, session isolation, authentication, cross-origin protection, generation limits and explicit fallback messaging. They do not make paid API calls.

## Provenance

See `UPSTREAM.md` for the source snapshot and extraction details. Bundled typefaces are accompanied by their SIL Open Font License notices.

## Occasion catalogue

The catalogue contains 95 occasions for 2026: 38 inherited entries and 57 researched additions. Search accepts occasion names, common spellings, regions and communities. Each addition has a reference date and a source link available under **2026 date & source**. Dates from holiday notifications are regional reference dates, not a claim that every entry is a nationwide holiday. Multi-day celebrations, lunar dates and community calendars can differ.

New data lives in `backend/festival_catalogue.json`, reviewed on 18 September 2026. Sources include Telangana, Assam, Maharashtra, Meghalaya and Puducherry government calendars, central government restricted holidays, Karnataka Tourism and PIB. The West Bengal Vishwakarma Puja amendment is sourced from the notification reproduced by WBXPress and is explicitly labelled. The inherited Eid al-Fitr date was corrected to 21 March with a moon-sighting note. The calendar update additionally cross-checks inherited dates against the central government calendar and other primary sources, with corrections for Eid al-Adha, Naraka Chaturdashi and Govardhan Puja. Entries still lacking independent verification remain visibly labelled.

**Append-only IDs:** greeting records store catalogue indices. Never reorder or delete the original list or the additions; append new entries and update metadata in place. UI sorting does not change IDs. Update the year and research dates before using this as a future-year calendar.

## Calendar, identity and personas

Browse the 2026 monthly calendar or its mobile agenda, filter by occasion/state/community, then choose **Create greeting**. Source links and regional notes accompany the dates. Amber entries still need independent verification. The selected date can be changed to match a local observance, and displayed on the image, in the message, both, or neither. Date/text edits reuse the saved artwork without another API request. Each new greeting snapshots the selected festival metadata so future catalogue changes cannot silently change old greetings.

Company greetings accept an optional PNG, JPEG or WebP logo (2 MB, 16 megapixels maximum). The server validates and normalizes it, strips metadata, and places it directly on the finished image; it is not sent to the image model. The original logo is preserved when editing. Individual mode accepts a name or a blank field for an unsigned greeting, with no automatic Hridaan Labs watermark.

Nine personas shape AI copy and visual direction: Warm, Humorous, Formal, Traditional, Heritage / Classical, Modern, Futuristic, Trendy / Tech-inspired, and Political / Civic. Civic mode is an inclusive public greeting, not a campaign generator. Trendy uses contemporary design cues, not live web trend research. Templates use persona-specific starter copy and selected palettes/type styles; AI provides fuller scene variation.

## Per-generation costs

New generations record usage from every attempted text/art-direction and image API call, including successful intermediate calls when a later call fails. Text/date edits preserve the original cost. Template-only generation is zero API cost. Older greetings have no retroactive cost record.

`backend/costs.py` contains standard synchronous prices checked against https://developers.openai.com/api/docs/pricing on 18 September 2026 (not Batch discounts): GPT-4.1-mini input/cached/output $0.40/$0.10/$1.60 per million tokens; GPT-image-2 text input/cached $5/$1.25, image input/cached $8/$2, image output $30. Review this versioned table if changing models or when provider prices change. Unknown models or missing usage yield an explicitly partial subtotal, never an invented total.

INR amounts are calculations from actual reported tokens, not final invoices. USD is converted using the dated USD/INR quote from ExchangeRate-API's public endpoint, cached for 24 hours per process; attribution appears in the UI. If rates are unavailable the UI retains USD and labels INR unavailable. Taxes, discounts, billing adjustments and payment-provider FX fees are excluded. Usage, rates and the FX quote are saved with the greeting for reproducibility. The personal API key remains in the ignored local environment file or deployment secrets.

### Calendar selection and optional AI text

**Choose from Calendar** appears before festival search. Selecting an occasion fills the visible festival-name/search field and greeting date together. Clicking a date with a single occasion selects it immediately; dates with multiple occasions show a chooser rather than silently selecting one.

**Generate AI greeting text** is enabled by default. Turning it off skips the greeting-copy, blueprint and fallback prompt-writing model calls. AI image generation still works with the local standard prompt and selected persona; editable basic wording, name/logo and date are composed locally. Only image usage is charged in that mode. Template artwork never makes API calls, regardless of this toggle.


## Verified accounts and one free creation

Account mode is the default. Registration collects name, email and an internationally validated phone number. Optional product-update consent starts unchecked and is timestamped; it can be changed after sign-in. No automatic WhatsApp marketing subscription is made. Email links verify and sign in, expire after 20 minutes, are single-use, and are stored only as SHA-256 hashes. Revocable sessions expire in 30 days using signed HttpOnly cookies (Secure in production). Verification URLs are excluded from Uvicorn access logging; configure proxy logs to omit query strings too.

Greetings belong to the account ID. Signing in with the same email restores the collection and lifetime allowance. A SQLite write transaction reserves the credit before scheduling an attempt. A successfully rendered AI image settles the run and credit in one transaction. AI artwork failures, render failures and template fallback preserve the credit. Concurrent attempts are rejected. After success, generation is blocked before any AI call; editing and downloading saved images remain available. Text failure with successful AI artwork still consumes the creation. Restart recovery releases interrupted work. Logout, clearing cookies, repeat registration or rejecting an image never reset the allowance.

SMTP credentials are server-only in environment configuration; `.env.local` is ignored by Git. Live domain verification and SMTP delivery were confirmed with ZeptoMail, including user confirmation of the test email. Account integration tests mock mail and images and do not send additional messages or make paid calls. At deployment, PUBLIC_BASE_URL must be the final HTTPS Python-service origin; localhost verification links work only on this computer.

Run one application process with a persistent DATA_DIR volume. Multiple workers are not supported by startup recovery and the in-process queue. Back up SQLite and greeting images together. Configure trusted proxies explicitly and retain the daily global generation cap and account rate limits. The early-access button records a request for availability emails; no payments or billing are implemented.

Before public launch, review policies covering registered users, name/email/phone, retained images and consent. The existing marketing privacy page alone does not cover these features. Production registration requires ACCOUNT_POLICIES_REVIEWED=true and HTTPS PRIVACY_POLICY_URL and TERMS_URL. No deployment was performed.

Account milestones are stored without PII in SQLite studio_events. Client requests cannot forge backend completion events. Marketing-site analytics remain separate and consent-based; no CRM synchronization is configured.

Validation: run python -m pytest tests -q from this app directory. Existing feature tests explicitly enable local legacy mode. Account tests cover verification/reuse/expiry, persistent login, concurrent requests, failed generations and access isolation. Browser QA covers registration, verification, calendar fill, limits, early access, logout and mobile layout.

