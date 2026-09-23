"""Category assignment for a transaction from its payee's cache entry."""

from app.models import MerchantCategoryMap

# User rule: UPI payments to an individual are usually small shops/food vendors
# paid via a personal handle, so default them to groceries/food for now.
P2P_DEFAULT_CATEGORY = "Food & Groceries"


def is_p2p(entry: MerchantCategoryMap) -> bool:
    return entry.payee_type == "person" or (
        entry.payee_type == "merchant_qr" and bool(entry.is_person_name)
    )


def assign_category(amount_paise: int, entry: MerchantCategoryMap) -> tuple[str, str]:
    """(category, category_source) for a transaction with this payee."""
    if entry.source == "manual":
        return entry.category, "rule"
    if amount_paise < 0 and is_p2p(entry):
        return P2P_DEFAULT_CATEGORY, "p2p_default"
    return entry.category, "llm"
