-- Restricted views for the chat agent. Query results from these are sent to the LLM,
-- so they leave out running balances, bank reference numbers, account details,
-- dedupe keys and source file names. The chat run_sql tool may only read these
-- views and `categories` (enforced by a SQLite authorizer).
CREATE VIEW chat_transactions AS
SELECT id, txn_date, amount_paise, merchant_normalized AS payee, payee_type, category,
       category_source, channel, txn_type, narration
FROM transactions;

CREATE VIEW chat_anomalies AS
SELECT id, anomaly_type, status, transaction_id, category, month, note
FROM anomalies;
