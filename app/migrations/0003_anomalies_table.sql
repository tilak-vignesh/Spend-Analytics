-- Anomalies move to their own table: a transaction can have several flags, and
-- category spikes belong to a (category, month), not a transaction.
CREATE TABLE anomalies (
    id INTEGER PRIMARY KEY,
    anomaly_type TEXT NOT NULL CHECK (anomaly_type IN
        ('duplicate', 'new_merchant', 'price_change', 'category_spike', 'outlier')),
    dedupe_key TEXT NOT NULL UNIQUE,
    transaction_id INTEGER REFERENCES transactions(id),
    related_transaction_id INTEGER REFERENCES transactions(id),
    category TEXT REFERENCES categories(name),
    month TEXT,
    details TEXT NOT NULL,
    note TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed')),
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_anomalies_txn ON anomalies(transaction_id);
CREATE INDEX idx_anomalies_status ON anomalies(status);

ALTER TABLE transactions DROP COLUMN is_anomaly;
ALTER TABLE transactions DROP COLUMN anomaly_type;
ALTER TABLE transactions DROP COLUMN anomaly_note;
