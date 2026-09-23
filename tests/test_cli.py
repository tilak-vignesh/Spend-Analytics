import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from tests.pdf_factory import balanced_rows, make_statement_pdf
from tests.test_hdfc_pdf import OPENING, SPECS

REPO = Path(__file__).parent.parent


def run_sync(*args, env_overrides):
    env = {**os.environ, **env_overrides}
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "sync.py"), *args],
        capture_output=True, text=True, env=env, cwd=REPO,
    )


def test_ingest_command(tmp_path):
    pdf = tmp_path / "sep.pdf"
    make_statement_pdf(pdf, balanced_rows(OPENING, SPECS), opening_paise=OPENING)
    env = {"TXN_DB_PATH": str(tmp_path / "t.db"), "HDFC_PDF_PASSWORD": "secret"}

    first = run_sync("ingest", str(pdf), env_overrides=env)
    assert first.returncode == 0, first.stderr
    assert "5 new, 0 already stored" in first.stdout
    assert "HDFC Savings 1234" in first.stdout

    again = run_sync("ingest", str(pdf), env_overrides=env)
    assert "0 new, 5 already stored" in again.stdout

    count = sqlite3.connect(tmp_path / "t.db").execute("SELECT COUNT(*) FROM transactions")
    assert count.fetchone()[0] == 5


def test_ingest_wrong_password_fails_cleanly(tmp_path):
    pdf = tmp_path / "sep.pdf"
    make_statement_pdf(pdf, balanced_rows(OPENING, SPECS), opening_paise=OPENING)
    result = run_sync("ingest", str(pdf), env_overrides={
        "TXN_DB_PATH": str(tmp_path / "t.db"), "HDFC_PDF_PASSWORD": "wrong"})
    assert result.returncode == 1
    assert "password" in result.stderr
    assert "Traceback" not in result.stderr
    assert "wrong" not in result.stderr  # never echo the password


def test_ingest_missing_file(tmp_path):
    result = run_sync("ingest", str(tmp_path / "nope.pdf"),
                      env_overrides={"TXN_DB_PATH": str(tmp_path / "t.db")})
    assert result.returncode == 1
    assert "not found" in result.stderr


def test_categorize_without_api_key_fails_cleanly(tmp_path):
    result = run_sync("categorize", env_overrides={
        "TXN_DB_PATH": str(tmp_path / "t.db"), "GEMINI_API_KEY": ""})
    assert result.returncode == 1
    assert "GEMINI_API_KEY" in result.stderr
    assert "Traceback" not in result.stderr


def test_categorize_with_nothing_pending(tmp_path):
    result = run_sync("categorize", env_overrides={
        "TXN_DB_PATH": str(tmp_path / "t.db"), "GEMINI_API_KEY": "unused"})
    assert result.returncode == 0, result.stderr
    assert "Nothing to categorize" in result.stdout


def test_detect_anomalies_lists_open_findings(tmp_path):
    from app.migrate import migrate

    db = tmp_path / "t.db"
    migrate(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO accounts (id, name) VALUES (1, 'HDFC Savings 1234')")
    for i in (1, 2):
        conn.execute(
            "INSERT INTO transactions (id, account_id, txn_date, amount_paise, dedupe_key,"
            " narration, merchant_key, merchant_normalized, category, txn_type)"
            " VALUES (?, 1, '2026-09-01', -146413, ?, 'N', 'vpa:vpn@ybl', 'ExampleVPN',"
            " 'Software & Hosting', 'debit')", (i, f"k{i}"))
    conn.commit()

    result = run_sync("detect-anomalies", env_overrides={"TXN_DB_PATH": str(db)})
    assert result.returncode == 0, result.stderr
    assert "1 new" in result.stdout
    assert "[duplicate] Possible duplicate: ₹1,464.13 to ExampleVPN" in result.stdout


def test_detect_anomalies_with_none(tmp_path):
    result = run_sync("detect-anomalies", env_overrides={"TXN_DB_PATH": str(tmp_path / "t.db")})
    assert result.returncode == 0, result.stderr
    assert "No open anomalies" in result.stdout
