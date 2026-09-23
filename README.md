# Personal Transaction Agent

A single-user tool for your HDFC bank statements: upload the statement PDF, and it
categorizes your spending, flags unusual transactions, shows a dashboard, and answers
questions about your money in a chat.

## What it does

- **Reads HDFC statement PDFs**: decrypts the password-protected statement, extracts
  every transaction and checks the totals against the statement's own summary.
  Re-uploading the same or an overlapping statement never duplicates transactions.
- **Categorizes spending**: identifies each payee (merchant, shop QR code or person)
  and assigns a category using Gemini. Each payee is sent to the LLM only once; after
  that its category comes from a local cache. UPI payments to individuals default to
  Food & Groceries.
- **Lets you correct categories**: change a category in the dashboard, optionally for
  every transaction from that payee, including future ones.
- **Flags anomalies**: possible duplicate charges, first payments to new merchants,
  subscription price changes, monthly category spikes, and unusually large payments.
- **Dashboard**: monthly spend and income, spend by category, daily spend, anomalies
  (with dismiss), and a searchable, filterable transaction list.
- **Chat**: ask questions like "What did I spend the most on this month?" The agent
  queries your data with SQL and shows the queries behind every answer. All
  calculations happen in SQL, and figures it can't trace back to the data are flagged.

## Setup

Requires Python 3.12.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Create a `.env` file in the repo root (it is gitignored):

```
HDFC_PDF_PASSWORD=<your HDFC customer ID>
GEMINI_API_KEY=<your Gemini API key>
# optional
GEMINI_MODEL=gemini-3.8-flash
```

## Usage

### Dashboard

```bash
.venv/bin/uvicorn app.main:app
```

Open http://localhost:8000/ and click **Upload statement**. The upload runs the whole
sync: parse, store, categorize and detect anomalies.

### Command line

```bash
.venv/bin/python scripts/sync.py ingest statements/<file>.pdf   # parse and store
.venv/bin/python scripts/sync.py categorize                      # categorize new payees
.venv/bin/python scripts/sync.py detect-anomalies                # flag unusual spending
```

Keep real statements in `statements/`, which is gitignored.

## Privacy

- Everything is stored locally in SQLite (`data/transactions.db`, gitignored).
- Categorization sends Gemini only the transaction narration text, never amounts.
- Chat sends Gemini query results (including amounts), but only from restricted
  views. Running balances, account numbers and bank reference numbers are blocked
  at the database level.
- The statement password and API key live only in `.env`.

## Tests

```bash
.venv/bin/pytest
```

Tests use synthetic statement PDFs and a fake LLM, so they need neither real
statements nor an API key.

## More

- `plan.md`: design, decisions and build phases
- `CLAUDE.md`: conventions for working on the codebase
