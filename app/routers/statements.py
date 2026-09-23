"""Statement upload: the dashboard's "Sync & Analyze" in one request.
Saves the PDF, ingests it, categorizes new payees (Gemini) and re-runs detection."""

import os
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.engine import Engine

from app.anomalies.detect import detect_anomalies
from app.categorization.categorize import CategorizeResult, categorize
from app.db import REPO_ROOT, get_engine
from app.ingestion.hdfc_pdf import StatementError, parse_statement
from app.ingestion.ingest import ingest_statement
from app.llm.gemini import get_llm_provider

router = APIRouter(prefix="/api/statements", tags=["statements"])


def get_statements_dir() -> Path:
    return Path(os.environ.get("STATEMENTS_DIR", REPO_ROOT / "statements"))


@router.post("")
def upload_statement(
    file: UploadFile,
    engine: Engine = Depends(get_engine),
    llm_provider: Callable = Depends(get_llm_provider),
    statements_dir: Path = Depends(get_statements_dir),
) -> dict:
    name = Path(file.filename or "").name  # drops any directory parts
    content = file.file.read()
    if not name.lower().endswith(".pdf") or not content.startswith(b"%PDF"):
        raise HTTPException(400, "Upload an HDFC statement PDF")

    statements_dir.mkdir(parents=True, exist_ok=True)
    path = statements_dir / name
    path.write_bytes(content)
    path.chmod(0o600)  # real statements are sensitive
    try:
        statement = parse_statement(path, password=os.environ.get("HDFC_PDF_PASSWORD"))
    except StatementError as exc:
        path.unlink()
        raise HTTPException(400, str(exc)) from None

    ingested = ingest_statement(engine, statement, source_file=name)
    categorize_error = None
    try:
        categorized = categorize(engine, llm_provider())
    except RuntimeError as exc:  # e.g. GEMINI_API_KEY missing; rows stay uncategorized
        categorized, categorize_error = CategorizeResult(), str(exc)
    detected = detect_anomalies(engine)

    return {
        "file": name,
        "account": ingested.account_name,
        "period_from": statement.period_from.isoformat(),
        "period_to": statement.period_to.isoformat(),
        "transactions": len(statement.rows),
        "inserted": ingested.inserted,
        "skipped": ingested.skipped,
        "categorized": categorized.categorized,
        "new_payees": categorized.new_payees,
        "categorize_failed": len(categorized.failed),
        "categorize_error": categorize_error,
        "anomalies_new": detected.new,
    }
