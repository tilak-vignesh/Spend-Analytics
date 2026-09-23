"""SQLModel mirrors of the tables in app/migrations/. Migrations own the schema;
keep these in sync (tests/test_migrations.py checks column names match)."""

from datetime import date, datetime, timezone

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    account_type: str | None = None  # savings / credit / upi


class Category(SQLModel, table=True):
    __tablename__ = "categories"

    name: str = Field(primary_key=True)
    parent_category: str | None = None


class Transaction(SQLModel, table=True):
    __tablename__ = "transactions"

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id")
    txn_date: date
    value_date: date | None = None
    amount_paise: int  # negative = debit, positive = credit
    balance_after_paise: int | None = None
    ref_no: str | None = None
    dedupe_key: str = Field(unique=True)
    narration: str
    channel: str | None = None  # upi / pos / neft / imps / atm / other
    payee_name: str | None = None
    payee_vpa: str | None = None
    payee_ifsc: str | None = None
    upi_remark: str | None = None
    merchant_key: str | None = None
    merchant_normalized: str | None = None
    payee_type: str | None = None  # merchant / merchant_qr / person
    category: str | None = Field(default=None, foreign_key="categories.name")
    category_source: str | None = None  # rule / llm / p2p_default / manual
    txn_type: str | None = None  # debit / credit / transfer
    source_file: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class MerchantCategoryMap(SQLModel, table=True):
    __tablename__ = "merchant_category_map"

    merchant_key: str = Field(primary_key=True)
    payee_name: str | None = None  # as written in the narration, wraps resolved
    merchant_normalized: str | None = None
    payee_type: str | None = None
    is_person_name: int | None = None  # 1 if payee_name is an individual's name
    category: str = Field(foreign_key="categories.name")
    confidence: float | None = None  # 1.0 = manually confirmed
    source: str | None = None  # llm / p2p_default / manual
    updated_at: datetime = Field(default_factory=_utcnow)


class Anomaly(SQLModel, table=True):
    __tablename__ = "anomalies"

    id: int | None = Field(default=None, primary_key=True)
    anomaly_type: str  # duplicate / new_merchant / price_change / category_spike / outlier
    dedupe_key: str = Field(unique=True)  # stable across re-detection runs
    transaction_id: int | None = Field(default=None, foreign_key="transactions.id")
    related_transaction_id: int | None = Field(default=None, foreign_key="transactions.id")
    category: str | None = None
    month: str | None = None  # YYYY-MM, for category_spike
    details: str  # JSON: the numbers that triggered the flag
    note: str  # plain-English explanation
    status: str = "open"  # open / dismissed
    detected_at: datetime = Field(default_factory=_utcnow)
