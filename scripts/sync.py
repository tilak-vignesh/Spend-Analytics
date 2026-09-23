"""CLI entrypoint: python scripts/sync.py <command>

Typical run: ingest <statement.pdf> -> categorize -> detect-anomalies
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session, select

# Before importing app modules: app.db reads TXN_DB_PATH at import time.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from app.anomalies.detect import detect_anomalies  # noqa: E402
from app.categorization.categorize import categorize  # noqa: E402
from app.db import DB_PATH, engine  # noqa: E402
from app.ingestion.hdfc_pdf import StatementError, parse_statement  # noqa: E402
from app.ingestion.ingest import ingest_statement  # noqa: E402
from app.llm.gemini import GeminiJSON  # noqa: E402
from app.migrate import migrate  # noqa: E402
from app.models import Anomaly  # noqa: E402


def cmd_init_db(_args: argparse.Namespace) -> None:
    version = migrate(DB_PATH)
    print(f"Database at {DB_PATH} is at schema version {version}")


def cmd_ingest(args: argparse.Namespace) -> None:
    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"error: {path} not found")
    migrate(DB_PATH)
    try:
        statement = parse_statement(path, password=os.environ.get("HDFC_PDF_PASSWORD"))
    except StatementError as exc:
        hint = " (set HDFC_PDF_PASSWORD in .env)" if "password" in str(exc) else ""
        sys.exit(f"error: {path.name}: {exc}{hint}")
    result = ingest_statement(engine, statement, source_file=str(path))
    print(
        f"{path.name}: {result.account_name}, {statement.period_from} to {statement.period_to}, "
        f"{len(statement.rows)} transactions reconciled: "
        f"{result.inserted} new, {result.skipped} already stored"
    )


def cmd_categorize(_args: argparse.Namespace) -> None:
    try:
        llm = GeminiJSON.from_env()
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")
    migrate(DB_PATH)
    result = categorize(engine, llm)
    if not (result.categorized or result.failed or result.transfers):
        print("Nothing to categorize")
        return
    print(
        f"Categorized {result.categorized} transactions "
        f"({result.new_payees} new payees via {llm.model}); "
        f"{result.transfers} transfer pairs"
    )
    if result.failed:
        print(f"{len(result.failed)} payees failed and will be retried next run:",
              file=sys.stderr)
        for key, reason in result.failed.items():
            print(f"  {key}: {reason}", file=sys.stderr)


def cmd_detect_anomalies(_args: argparse.Namespace) -> None:
    migrate(DB_PATH)
    result = detect_anomalies(engine)
    print(f"Anomalies: {result.new} new, {result.updated} updated, {result.removed} resolved")
    with Session(engine) as session:
        open_anomalies = session.exec(
            select(Anomaly).where(Anomaly.status == "open").order_by(Anomaly.id)
        ).all()
    if not open_anomalies:
        print("No open anomalies")
    for anomaly in open_anomalies:
        print(f"  [{anomaly.anomaly_type}] {anomaly.note}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="sync.py")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create/upgrade the database schema").set_defaults(
        func=cmd_init_db
    )
    ingest = sub.add_parser("ingest", help="parse an HDFC statement PDF and store it")
    ingest.add_argument("file", help="path to the statement PDF")
    ingest.set_defaults(func=cmd_ingest)
    sub.add_parser("categorize", help="categorize new transactions (Gemini)").set_defaults(
        func=cmd_categorize
    )
    sub.add_parser("detect-anomalies", help="flag unusual spending").set_defaults(
        func=cmd_detect_anomalies
    )
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
