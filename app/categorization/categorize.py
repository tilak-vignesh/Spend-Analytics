"""Categorize every uncategorized transaction.

1. Compute each transaction's merchant_key (VPA, else narration fingerprint).
2. Keys not yet in merchant_category_map go to the LLM in batches, one
   representative narration per key; results are validated and cached.
3. Every pending transaction whose key is cached gets payee fields + a category
   (rules.assign_category). Keys the LLM failed on stay pending for the next run.
4. Pair own-account transfers.

Transactions with a manual category always have a category, so are never pending.
"""

from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.categorization.keys import merchant_key
from app.categorization.payee_type import resolve_payee_type, vpa_signal
from app.categorization.rules import assign_category
from app.categorization.transfers import mark_transfers
from app.ingestion.narration_agent import GenerateJSON, PayeeInput, classify_payees
from app.models import Category, MerchantCategoryMap, Transaction


@dataclass
class CategorizeResult:
    new_payees: int = 0
    categorized: int = 0
    transfers: int = 0
    failed: dict[str, str] = field(default_factory=dict)  # merchant_key -> reason


def categorize(engine: Engine, generate_json: GenerateJSON, batch_size: int = 50) -> CategorizeResult:
    result = CategorizeResult()
    with Session(engine) as session:
        pending = session.exec(
            select(Transaction).where(Transaction.category.is_(None)).order_by(Transaction.id)
        ).all()
        for txn in pending:
            txn.merchant_key = merchant_key(txn.narration, txn.payee_vpa)

        cache = {m.merchant_key: m for m in session.exec(select(MerchantCategoryMap)).all()}
        unseen: dict[str, Transaction] = {}
        for txn in pending:
            if txn.merchant_key not in cache:
                unseen.setdefault(txn.merchant_key, txn)

        categories = list(session.exec(select(Category.name)).all())
        inputs = [
            PayeeInput(key=key, narration=t.narration, channel=t.channel, vpa=t.payee_vpa,
                       ifsc=t.payee_ifsc, direction=t.txn_type or "debit")
            for key, t in unseen.items()
        ]
        outcome = classify_payees(inputs, categories, generate_json, batch_size=batch_size)
        result.failed = outcome.failed

        for key, payee in outcome.results.items():
            rep = unseen[key]
            entry = MerchantCategoryMap(
                merchant_key=key,
                payee_name=payee.payee_name,
                merchant_normalized=payee.merchant_normalized,
                payee_type=resolve_payee_type(
                    payee.payee_type, vpa_signal(rep.payee_vpa, rep.payee_ifsc)
                ),
                is_person_name=int(payee.is_person_name),
                category=payee.category,
                confidence=payee.confidence,
                source="llm",
            )
            session.add(entry)
            cache[key] = entry
            result.new_payees += 1

        for txn in pending:
            entry = cache.get(txn.merchant_key)
            if entry is None:
                continue
            txn.payee_name = entry.payee_name
            txn.merchant_normalized = entry.merchant_normalized
            txn.payee_type = entry.payee_type
            txn.category, txn.category_source = assign_category(txn.amount_paise, entry)
            result.categorized += 1

        session.add_all(pending)
        session.commit()

    result.transfers = mark_transfers(engine)
    return result
