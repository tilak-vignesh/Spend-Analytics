# Personal Transaction Agent — Build Plan

Single-user, SQLite-backed system: ingest transactions → normalize →
categorize → detect anomalies → surface via a dashboard + chatbot. No Prefect, no
multi-tenant complexity. FastAPI backend, SQLite DB, a lightweight
frontend (server-rendered or a small React/HTML dashboard), and an
LLM-powered chat endpoint for questions.

## Tech stack

- **DB**: SQLite (single file, `data/transactions.db`)
- **Backend**: FastAPI
- **ORM**: SQLModel (for schema clarity)
- **Money**: plain `int` paise everywhere — Python and SQLite (see "Money handling" below)
- **LLM**: Gemini (`google-genai`; default model `gemini-3.8-flash`, override with
  `GEMINI_MODEL`; key `GEMINI_API_KEY` in `.env`) — tool-use / function-calling for normalization,
  categorization + Q&A
- **Frontend**: Keep it simple — a single dashboard page (charts + table) built with
  Chart.js or Recharts, plus a chat widget calling `/chat`. No need for a separate
  frontend framework unless you want one.
- **Scheduling**: none for now — categorization/anomaly jobs run on-demand via a CLI
  command or a button in the dashboard ("Sync & Analyze"). Add a cron job later if
  you want it automatic.

## Project structure

```
payment-sys/
├── CLAUDE.md                  # project context for Claude Code (stack, conventions, schema)
├── plan.md
├── .env                       # HDFC_PDF_PASSWORD (gitignored, mode 600)
├── data/
│   └── transactions.db        # gitignored
├── statements/                # real statement PDFs (gitignored, never committed)
├── app/
│   ├── main.py                 # FastAPI app entrypoint
│   ├── db.py                   # SQLite connection/session setup (+ read-only conn for chat)
│   ├── models.py               # SQLModel table definitions
│   ├── money.py                # parse "1,464.13" -> 146413, format paise -> "₹1,464.13"
│   ├── ingestion/
│   │   ├── hdfc_pdf.py         # encrypted HDFC PDF -> rows, reconciled against summary
│   │   ├── hints.py            # best-effort regex hints (channel, VPA, IFSC)
│   │   ├── ingest.py           # rows -> transactions table, dedupe
│   │   └── narration_agent.py  # batched LLM parse/verify of narrations
│   ├── llm/
│   │   └── gemini.py           # generate_json(system, prompt, schema) adapter
│   ├── categorization/
│   │   ├── categorize.py       # orchestrator: keys -> cache/LLM -> rules -> transfers
│   │   ├── keys.py             # merchant_key: vpa:<vpa> or fp:<narration fingerprint>
│   │   ├── payee_type.py       # VPA/IFSC heuristics: merchant vs merchant QR vs person
│   │   ├── rules.py            # category assignment + P2P default rule
│   │   └── transfers.py        # own-account transfer pairing
│   ├── anomalies/
│   │   ├── base.py             # Txn / Finding types, month helpers
│   │   ├── rules.py            # duplicate / new merchant / price change / monthly spike
│   │   ├── stats.py            # per-category median/MAD outliers
│   │   └── detect.py           # orchestrator: run detectors, sync anomalies table
│   ├── chat/
│   │   ├── tools.py            # run_sql, get_category_breakdown, etc.
│   │   └── agent.py            # chat endpoint logic, tool-calling loop
│   └── routers/                # all JSON under /api; index.html served at /
│       ├── dashboard.py        # months, summary, category breakdown, daily, trend
│       ├── transactions.py     # filtered list + manual category override
│       ├── anomalies.py        # list + dismiss/reopen
│       ├── statements.py       # PDF upload -> ingest -> categorize -> detect
│       └── chat.py             # POST /api/chat
├── frontend/
│   └── index.html              # dashboard + chat widget, single page
├── scripts/
│   └── sync.py                 # CLI: ingest → categorize → detect anomalies
└── tests/
    └── pdf_factory.py         # builds synthetic encrypted HDFC-style PDFs for tests
```

## Money handling

- Signed `int` paise everywhere, in Python and in SQLite (`-146413` for a ₹1,464.13
  debit). No `Decimal`, no `float`.
- Parse statement values as strings: strip commas, split on `.`, pad the fraction to
  2 digits (`"686,919.91"` → `68691991`). Never go through `float`.
- Convert to rupees only for display (`money.format_inr`).
- SQL aggregates (`SUM`) stay exact. Anything reading raw SQL (dashboard queries,
  the chat agent's `run_sql`) must divide by 100 — the chat system prompt says so
  explicitly.

## Statement format (HDFC savings, PDF only)

Input is always the password-protected PDF HDFC NetBanking produces (password =
customer ID, read from `HDFC_PDF_PASSWORD` in `.env`). There is no CSV/XLS path.

Columns: `Date | Narration | Chq./Ref.No. | Value Dt | Withdrawal Amt. | Deposit Amt. | Closing Balance`

- Dates are `DD/MM/YY` → store as ISO `YYYY-MM-DD`.
- Amount = `-Withdrawal` or `+Deposit` (exactly one is filled).
- `Chq./Ref.No.` is the zero-padded bank reference and matches the RRN inside the
  narration (`0000128895684855` ↔ UPI ref `128895684855`).

### PDF layout (measured on a real statement)

- No table structure — only positioned text. 8pt Times-Roman; words on a line are
  separated by ~2pt gaps (there are **no space characters**; extract with
  `x_tolerance=1` to recover in-line spaces).
- Columns are assigned by each word's x-centre, using the table's vertical rules as
  boundaries (`COLUMNS` in `hdfc_pdf.py`).
- The table body sits between two full-width horizontal rules on each page; the
  footer (disclaimer, GSTIN, address) and the last page's `STATEMENT SUMMARY` are
  below the lower rule and are excluded.
- A line with a date in the Date column starts a transaction; other lines are
  narration continuations — **including lines at the top of the next page**
  (narrations wrap across page breaks).
- **A space at a line wrap is lost and can't be recovered.** Continuation x-offsets
  (68/72/74) looked like a signal but aren't reliable. So narrations are stored with
  `\n` at each wrap; hints join wraps with no space (VPAs/IFSCs never contain spaces);
  the LLM is told a `\n` may or may not have been a space.
- The summary block (opening balance, Dr/Cr counts, debit/credit totals, closing
  balance) is used to **reconcile** every parse: each row's closing balance must equal
  the previous + amount, and counts/totals must match exactly, or ingest fails.

### Narration — no fixed structure

Narrations *look* structured but aren't reliably so: field order, separators and
truncation vary (payee names contain hyphens, remarks are optional, VPAs get cut,
POS descriptors mix phone numbers, cities and merchant names, NEFT/IMPS/interest
rows have their own ad-hoc shapes). So a regex can't be the parser. Examples:

```
UPI-APOLLO PHARMACY-APOLLOPHARMACYKARPL@YBL-YESB0YBLUPI-128895684855-PAYMENT FOR 208017
UPI-HARIS P-PAYTM.S22R6TO@PTY-YESB0MCHUPI-128980422423-UPI
POS 416021XXXXXX1596 624408798431 01SEP26 14:26:06 +13106018492 EXPRESSVPN.COM
POS 416021XXXXXX1596 624506165216 02SEP26 12:06:52 GUNZENHAUSEN HETZNER ONLINE GMBH
```

Approach — **regex for hints, LLM for the parse:**

1. `hints.py` (deterministic, best-effort): pull out tokens that are recognisable
   *anywhere* in the string regardless of layout — VPA (`\S+@[a-z]+`), IFSC
   (`[A-Z]{4}0[A-Z0-9]{6}`), 12-digit RRN, leading `UPI`/`POS`/`NEFT`/`IMPS` for
   `channel`. Missing hints are fine; wrong hints get corrected in step 2.
2. `narration_agent.py` (batched LLM): send the raw narration + hints, get back
   verified structured fields — `payee_name`, `payee_vpa`, `payee_ifsc`, `remark`,
   `merchant_normalized` (`GUNZENHAUSEN HETZNER ONLINE GMBH` → `Hetzner`),
   `payee_type`, `category`. This is the same call as Phase 2 categorization — one
   pass does parse + verify + categorize.
3. Validate the LLM output against the narration: every extracted VPA / IFSC / RRN
   must appear (case-insensitive) in the raw narration **with all whitespace
   removed**, otherwise drop that field. Keeps the model from inventing identifiers.

### Deduplication

- `dedupe_key = "<account_id>:<ref_no>:<txn_date>:<amount_paise>"` when `ref_no` is
  present and not all zeros. Date + amount mean a ref the bank reuses for a different
  transaction (e.g. a reversal) isn't silently dropped.
- Fallback (no ref / all zeros):
  `"<account_id>:sha256:" + sha256(txn_date | amount_paise | narration | balance_after_paise)`.
  Including closing balance keeps two identical ₹20 chai payments on the same day distinct.
- `UNIQUE(dedupe_key)`; ingest uses `INSERT ... ON CONFLICT DO NOTHING`, so
  re-ingesting overlapping statements is safe.

## Database schema (SQLite)

```sql
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,          -- e.g. "HDFC Savings", "ICICI Credit Card"
    account_type TEXT            -- savings/credit/upi
);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    txn_date TEXT NOT NULL,          -- ISO YYYY-MM-DD
    value_date TEXT,
    amount_paise INTEGER NOT NULL,   -- negative = debit, positive = credit
    balance_after_paise INTEGER,
    ref_no TEXT,                     -- Chq./Ref.No. as-is
    dedupe_key TEXT NOT NULL UNIQUE,
    narration TEXT NOT NULL,         -- full raw narration
    channel TEXT,                    -- upi/pos/neft/imps/atm/other
    payee_name TEXT,                 -- LLM-parsed, validated against narration
    payee_vpa TEXT,                  -- UPI only, lowercased
    payee_ifsc TEXT,                 -- UPI only
    upi_remark TEXT,
    merchant_key TEXT,               -- cache key: vpa if present, else narration fingerprint
    merchant_normalized TEXT,        -- display name, e.g. "Apollo Pharmacy", "Hetzner"
    payee_type TEXT,                 -- merchant / merchant_qr / person
    category TEXT REFERENCES categories(name),
    category_source TEXT,            -- rule / llm / p2p_default / manual
    txn_type TEXT,                   -- debit/credit/transfer
    source_file TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_txn_date ON transactions(txn_date);
CREATE INDEX idx_txn_category ON transactions(category);
CREATE INDEX idx_txn_account ON transactions(account_id);
CREATE INDEX idx_txn_merchant_key ON transactions(merchant_key);

CREATE TABLE merchant_category_map (
    merchant_key TEXT PRIMARY KEY,   -- vpa if present, else narration fingerprint
    merchant_normalized TEXT,
    payee_type TEXT,
    category TEXT NOT NULL REFERENCES categories(name),
    confidence REAL,                 -- 1.0 = manually confirmed, <1.0 = agent-guessed
    source TEXT,                     -- llm / p2p_default / manual
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE categories (
    name TEXT PRIMARY KEY,
    parent_category TEXT             -- optional, for grouping
);

CREATE TABLE anomalies (             -- migration 0003
    id INTEGER PRIMARY KEY,
    anomaly_type TEXT NOT NULL,      -- duplicate / new_merchant / price_change / category_spike / outlier
    dedupe_key TEXT NOT NULL UNIQUE, -- e.g. "duplicate:<txn_id>", "category_spike:Dining:2026-09"
    transaction_id INTEGER REFERENCES transactions(id),          -- NULL for category_spike
    related_transaction_id INTEGER REFERENCES transactions(id),  -- earlier charge it's compared to
    category TEXT, month TEXT,       -- month = YYYY-MM for category_spike
    details TEXT NOT NULL,           -- JSON: the numbers that triggered it
    note TEXT NOT NULL,              -- plain-English explanation (templated)
    status TEXT NOT NULL DEFAULT 'open',  -- open / dismissed
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```
(Migration 0002 added `payee_name`, `is_person_name` to `merchant_category_map`.)

Keep `merchant_category_map` as the categorization cache — this is what
makes repeated syncs cheap and fast instead of re-calling the LLM on
every transaction every time. Keying on VPA (not payee name) is deliberate:
the VPA is stable, names get truncated/reformatted.

The `categories` table is a fixed, seeded list (Food & Groceries, Health,
Software & Hosting, Shopping, Transport, Bills & Utilities, Rent, Transfers, …).
The LLM may only choose from it — passed as an enum in the tool schema.

## Build phases

### Phase 0 — Scaffolding
- `git init`, `CLAUDE.md` with stack + schema + conventions (so Claude Code
  has consistent context across sessions).
- FastAPI skeleton, SQLite connection, tables + indexes via migration script,
  seed `categories`.
- `app/money.py` string → paise parser and formatter, with tests.

### Phase 1 — Ingestion ✅
- `hdfc_pdf.py`: decrypt + parse the PDF, reconcile against the summary block.
  Tests use synthetic PDFs from `tests/pdf_factory.py`; one opt-in test parses the real
  statement in `statements/` when present (asserts reconciliation only, no content).
- Store the raw narration untouched; run `hints.py` to fill `channel` and any
  hinted VPA/IFSC. Full parsing happens in Phase 2 (LLM).
- Compute `dedupe_key`, insert with `ON CONFLICT DO NOTHING`. Account is found/created
  as `HDFC Savings <last 4 of account no>`.
- CLI command: `python scripts/sync.py ingest <file>`

### Phase 2 — Normalization + categorization ✅

The hard problem: most UPI payments go to a *person's* VPA even when you're paying a
shop, and narrations have no fixed structure. Pipeline per sync:

**1. Cache key + lookup (`keys.py`)** — `merchant_key` = hinted VPA (lowercased)
if `hints.py` found one, else a *narration fingerprint*: uppercase the narration and
strip RRNs, card numbers, dates/times, phone numbers and other digit runs
(`POS … EXPRESSVPN.COM` → `POS EXPRESSVPN.COM`). If the key is in
`merchant_category_map`, copy its fields and skip the LLM.

**2. Batched LLM parse + verify + categorize (`ingestion/narration_agent.py`)** —
collect unique unseen `merchant_key`s (one representative narration each, plus its
hints), one call for the batch (chunk at ~50 keys). Structured output per key:
`payee_name`, `payee_vpa`, `payee_ifsc`, `remark`, `merchant_normalized`,
`payee_type`, `is_person_name`, `category` (enum). Validate identifiers against the
raw narration (see "Narration — no fixed structure"), then write back to
`merchant_category_map` with `confidence < 1.0` and to every transaction sharing the key.

**VPA/IFSC heuristics (`payee_type.py`)** — passed to the LLM as hints and used to
sanity-check its `payee_type`. The VPA often reveals a merchant QR even when the
name is a person's:

| Signal | Meaning | Example from statement |
|---|---|---|
| IFSC `YESB0MCHUPI` (MCH = merchant) | merchant QR | `HARIS P` / `paytm.s22r6to@pty` |
| `paytm.s…@pty`, `paytmqr…@paytm` | Paytm merchant QR | `paytm.s22r6to@pty` |
| `q\d+@ybl` | PhonePe merchant QR | `TEAVINE` / `q071626709@ybl` |
| `amzn\d+@apl` | Amazon Pay merchant QR | `PRAKASH` / `amzn0026722655@apl` |
| business-looking VPA (`apollopharmacy…@ybl`) | merchant | `APOLLO PHARMACY` |
| phone-number VPA, `@okaxis`/`@oksbi`/`@okhdfcbank`, `name.1234@…` | person (P2P) | `9902352924@axl`, `sageeriqra29@okaxis` |

When a strong heuristic (MCH IFSC, `q…@ybl`, `amzn…@apl`, `paytm.s…`) disagrees with
the LLM, the heuristic wins.

**3. P2P default rule (`rules.py`)** — for now: if the payee is a person
(`payee_type='person'`, or a `merchant_qr` whose name is just a person's name like
`HARIS P` / `PRAKASH`), assign **Food & Groceries** with
`category_source='p2p_default'`. Tagging the source keeps these easy to find and
reclassify later (e.g. add an amount cap so a ₹15,000 P2P isn't counted as food).

**Also in this phase:**
- `transfers.py`: pair debits/credits across your own accounts (same amount,
  opposite sign, dates within 3 days) → `txn_type='transfer'`, category `Transfers`.
  Dashboard totals exclude transfers so card-bill payments aren't double counted.
- Manual overrides: `PATCH /transactions/{id}` sets `category_source='manual'`;
  optionally "apply to all from this payee" updates the map with `confidence=1.0`.
  Syncs never overwrite `manual`.
- CLI command: `python scripts/sync.py categorize`

### Phase 3 — Anomaly detection ✅

Anomalies live in their own `anomalies` table (not columns on `transactions`): a
transaction can have several flags, and category spikes belong to a (category,
month). Detection is a full recompute each run, synced by `dedupe_key`: new findings
inserted, changed ones updated, open ones that no longer apply removed; `dismissed`
ones are never touched. All checks use debits only and exclude `txn_type='transfer'`.

**Rule-based (`rules.py`):**
- **Duplicate charge**: same `merchant_key` + same amount, same or next day, amount
  ≥ ₹500 (two ₹20 chais aren't a double charge).
- **New merchant**: first-ever debit to a `merchant_key`, ≥ ₹1,000, only once there are
  60+ days of history before it (otherwise everything is "new").
- **Recurring price change**: exactly one charge per month in ≥3 of the last 4 months
  (incl. this one) = recurring. Flag when the charge moves beyond tolerance vs. the
  previous one: 8% for POS (FX drift), 2% otherwise.
- **Monthly category spike**: month total > 1.5× median of up to 6 previous months
  (months with no spend count as 0; needs ≥3 months of history) and ≥ ₹2,000.

**Statistical (`stats.py`):**
- Per-category robust z-score over the previous 183 days:
  `0.6745 × (x − median) / MAD`, flag if > 3.5; only unusually *large* payments,
  ≥ ₹500. Needs ≥10 earlier payments in the category (cold start). If MAD is 0, falls
  back to mean absolute deviation; if all samples are identical, flags > 2× the usual.

**Notes are templated, not LLM-written.** ("ExampleVPN charged ₹649.00, up 30.1% from
₹499.00 on 2026-08-05"). Numbers are exact by construction and amounts never leave
the machine. An LLM-written note could be layered on later if wanted.

- CLI command: `python scripts/sync.py detect-anomalies`

### Phase 4 — Dashboard API ✅
All JSON routes live under `/api`; money is int paise; spend = debits, income =
credits, own-account transfers excluded from both.
- `GET /api/months`, `GET /api/categories`
- `GET /api/dashboard/summary?month=` — spend, income, count, vs previous month,
  open anomalies, uncategorized count, top 3 categories (defaults to latest month)
- `GET /api/dashboard/categories?month=`, `/daily?month=` (gaps filled, cumulative),
  `/trend?months=&end=` (gaps filled)
- `GET /api/transactions?month=&category=&q=&direction=&limit=&offset=` →
  `{total, items}`; `category=Uncategorized` matches NULL; items carry open anomaly types
- `PATCH /api/transactions/{id}` — manual category override (`apply_to_payee`)
- `GET /api/anomalies?status=open|dismissed|all`, `PATCH /api/anomalies/{id}` (dismiss/reopen)
- `POST /api/statements` — upload a PDF: saved to `statements/` (mode 600), parsed,
  ingested, categorized (Gemini), anomalies re-detected. If Gemini isn't configured
  the rows are still ingested and the error is returned.

### Phase 5 — Chat agent ✅
- **Privacy boundary** (user decision: amounts may go to Gemini, balances/account
  details may not): migration 0004 adds `chat_transactions` (id, txn_date, amount_paise,
  payee, payee_type, category, category_source, channel, txn_type, narration) and
  `chat_anomalies`. `run_sql` uses the read-only connection plus a SQLite authorizer
  that only permits reads of those views and `categories` (CTEs allowed, since their
  bodies are authorized too). Direct reads of `transactions`, `accounts`,
  `merchant_category_map`, `sqlite_master`, PRAGMA, ATTACH and writes are all refused.
- `chat/tools.py`: `run_sql(query)` (SELECT/WITH only, 200-row cap, 2 s timeout),
  `get_category_breakdown(start_date, end_date)`, `get_anomalies(start_date, end_date,
  status)`. Tools never raise; errors go back to the model.
- `chat/agent.py`: provider-neutral loop (max 8 steps, last 20 history turns). System
  prompt carries today's date, the data's date range, the view schema and rules
  (paise, exclude transfers, never guess numbers, plain text).
- `llm/gemini.py:GeminiChat`: Gemini function calling; model turns replayed verbatim.
- `POST /api/chat {message, history}` → `{answer, steps, tool_calls}` (503 if no key,
  502 on model errors). Frontend chat panel shows the queries behind each answer.

### Phase 6 — Frontend ✅
- Single `frontend/index.html`, vanilla JS + Chart.js (CDN), no build step, light/dark.
- Month picker, "Upload statement" (runs the whole sync), summary cards,
  spend-by-category bar chart (click to filter), daily spend + cumulative (switches to a
  monthly spend-vs-income chart once there are 2+ months), anomaly list with dismiss,
  transaction table with search/filters/pagination and inline category edits, and a chat
  panel (suggested questions, conversation memory, expandable queries per answer).
- All bank text is inserted with `textContent` (no `innerHTML`).

## Working with Claude Code

- Keep `CLAUDE.md` up to date with schema changes as you go — that's the
  file Claude Code reads for context every session.
- Hand off one phase at a time as a task, not the whole plan at once — e.g.
  "implement Phase 2 categorization per plan.md".
- Ask it to write tests alongside each phase (parser tests with the sample
  statement — especially hint extraction, the LLM-output validation, and dedupe — SQL tool tests with a
  seeded test DB) so later phases don't silently break earlier ones.
