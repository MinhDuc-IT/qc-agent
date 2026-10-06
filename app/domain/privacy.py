import re


PII_PATTERNS = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}


def reject_common_pii(value: str) -> None:
    found = [name for name, pattern in PII_PATTERNS.items() if pattern.search(value)]
    if found:
        raise ValueError(f"Golden dataset contains prohibited PII: {', '.join(found)}")
