-- Initial schema. Migrations are the source of truth for the DB schema;
-- app/models.py mirrors it (tests/test_migrations.py checks they match).
-- Money is signed INTEGER paise. Dates are ISO 'YYYY-MM-DD' text.

CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    account_type TEXT CHECK (account_type IN ('savings', 'credit', 'upi'))
);

CREATE TABLE categories (
    name TEXT PRIMARY KEY,
    parent_category TEXT REFERENCES categories(name)
);

CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    txn_date TEXT NOT NULL,
    value_date TEXT,
    amount_paise INTEGER NOT NULL,
    balance_after_paise INTEGER,
    ref_no TEXT,
    dedupe_key TEXT NOT NULL UNIQUE,
    narration TEXT NOT NULL,
    channel TEXT CHECK (channel IN ('upi', 'pos', 'neft', 'imps', 'atm', 'other')),
    payee_name TEXT,
    payee_vpa TEXT,
    payee_ifsc TEXT,
    upi_remark TEXT,
    merchant_key TEXT,
    merchant_normalized TEXT,
    payee_type TEXT CHECK (payee_type IN ('merchant', 'merchant_qr', 'person')),
    category TEXT REFERENCES categories(name),
    category_source TEXT CHECK (category_source IN ('rule', 'llm', 'p2p_default', 'manual')),
    txn_type TEXT CHECK (txn_type IN ('debit', 'credit', 'transfer')),
    source_file TEXT,
    is_anomaly INTEGER NOT NULL DEFAULT 0 CHECK (is_anomaly IN (0, 1)),
    anomaly_type TEXT CHECK (anomaly_type IN
        ('duplicate', 'new_merchant', 'price_change', 'category_spike', 'outlier')),
    anomaly_note TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_txn_date ON transactions(txn_date);
CREATE INDEX idx_txn_category ON transactions(category);
CREATE INDEX idx_txn_account ON transactions(account_id);
CREATE INDEX idx_txn_merchant_key ON transactions(merchant_key);

CREATE TABLE merchant_category_map (
    merchant_key TEXT PRIMARY KEY,
    merchant_normalized TEXT,
    payee_type TEXT CHECK (payee_type IN ('merchant', 'merchant_qr', 'person')),
    category TEXT NOT NULL REFERENCES categories(name),
    confidence REAL CHECK (confidence BETWEEN 0 AND 1),
    source TEXT CHECK (source IN ('llm', 'p2p_default', 'manual')),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Fixed category list. The LLM may only choose from these.
INSERT INTO categories (name) VALUES
    ('Food & Groceries'),
    ('Dining'),
    ('Health'),
    ('Software & Hosting'),
    ('Shopping'),
    ('Transport'),
    ('Travel'),
    ('Bills & Utilities'),
    ('Rent'),
    ('Entertainment'),
    ('Education'),
    ('Personal Care'),
    ('Investments'),
    ('Income'),
    ('Transfers'),
    ('Fees & Charges'),
    ('Cash Withdrawal'),
    ('Other');
