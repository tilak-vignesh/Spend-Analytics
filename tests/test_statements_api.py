import pytest
from sqlmodel import Session, select

from app.models import Transaction
from tests.pdf_factory import balanced_rows, make_statement_pdf
from tests.test_hdfc_pdf import OPENING, SPECS


@pytest.fixture
def pdf_bytes(tmp_path):
    path = tmp_path / "upload.pdf"
    make_statement_pdf(path, balanced_rows(OPENING, SPECS), opening_paise=OPENING)
    return path.read_bytes()


@pytest.fixture(autouse=True)
def password(monkeypatch):
    monkeypatch.setenv("HDFC_PDF_PASSWORD", "secret")


def upload(client, content, name="sep.pdf"):
    return client.post("/api/statements", files={"file": (name, content, "application/pdf")})


def test_upload_ingests_categorizes_and_detects(client, engine, pdf_bytes, tmp_path, fake_llm):
    r = upload(client, pdf_bytes)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "file": "sep.pdf", "account": "HDFC Savings 1234",
        "period_from": "2026-09-01", "period_to": "2026-09-23",
        "transactions": 5, "inserted": 5, "skipped": 0,
        "categorized": 5, "new_payees": body["new_payees"], "categorize_error": None,
        "categorize_failed": 0, "anomalies_new": body["anomalies_new"],
    }
    assert fake_llm.calls == 1
    assert (tmp_path / "statements" / "sep.pdf").read_bytes() == pdf_bytes
    with Session(engine) as s:
        assert all(t.category for t in s.exec(select(Transaction)).all())


def test_reupload_is_a_no_op(client, pdf_bytes, fake_llm):
    upload(client, pdf_bytes)
    body = upload(client, pdf_bytes).json()
    assert (body["inserted"], body["skipped"], body["categorized"]) == (0, 5, 0)
    assert fake_llm.calls == 1


def test_upload_filename_cannot_escape_statements_dir(client, pdf_bytes, tmp_path):
    assert upload(client, pdf_bytes, name="../../evil.pdf").status_code == 200
    assert (tmp_path / "statements" / "evil.pdf").exists()
    assert not (tmp_path.parent / "evil.pdf").exists()


def test_wrong_password(client, pdf_bytes, monkeypatch):
    monkeypatch.setenv("HDFC_PDF_PASSWORD", "nope")
    r = upload(client, pdf_bytes)
    assert r.status_code == 400
    assert "password" in r.json()["detail"]
    assert "nope" not in r.text


def test_not_a_pdf(client):
    assert upload(client, b"hello", name="notes.txt").status_code == 400
    assert upload(client, b"hello", name="fake.pdf").status_code == 400


def test_ingests_even_when_llm_unavailable(client, engine, pdf_bytes):
    from app.llm.gemini import get_llm_provider
    from app.main import app

    def no_llm():
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env)")

    app.dependency_overrides[get_llm_provider] = lambda: no_llm
    body = upload(client, pdf_bytes).json()
    assert body["inserted"] == 5 and body["categorized"] == 0
    assert "GEMINI_API_KEY" in body["categorize_error"]
