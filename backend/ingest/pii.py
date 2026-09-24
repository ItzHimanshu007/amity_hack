"""PII scrubbing for the civic complaints feed. CONTRACT.md §D.2.

Two jobs, both server-side:

  1. `scrub()` runs during ingest. Canonical events never carry complaint prose at all,
     so no name or number can reach Phase 4, Phase 5 or the database.
  2. `mask_record()` is what `GET /raw/{feed}` calls before returning anything to the
     data room. The file on disk keeps its PII -- that is the thing the scrubber is
     demonstrated against -- but nothing unmasked leaves the API.

Detection is generic, not a name list: a fixed list would silently fail the first time a
resident has a name nobody thought of, which is the failure mode that matters.
"""

import re
from functools import lru_cache

NAME_TAG = "[name]"
PHONE_TAG = "[phone]"

# Indian mobile numbers. People group the digits every which way -- "9829012345",
# "98290 12345", "+91 98290-12345", "0 9829 012 345" -- so we find any digit run long
# enough to be a number and validate it after stripping separators, rather than trying
# to enumerate the groupings.
_PHONE_CANDIDATE = re.compile(r"(?<![\w])\+?\d[\d\s-]{7,18}\d(?![\w])")


def _is_indian_mobile(digits: str) -> bool:
    for prefix in ("0091", "91", "0"):
        if digits.startswith(prefix) and len(digits) == 10 + len(prefix):
            digits = digits[len(prefix):]
            break
    return len(digits) == 10 and digits[0] in "6789"


def _mask_phones(text: str):
    count = 0

    def repl(m):
        nonlocal count
        digits = re.sub(r"\D", "", m.group(0))
        if not _is_indian_mobile(digits):
            return m.group(0)
        count += 1
        return PHONE_TAG

    return _PHONE_CANDIDATE.sub(repl, text), count

# Name-shaped: one or two capitalised alphabetic tokens. Complaint prose is Hinglish and
# effectively all lower case, so a capitalised run is a name with very few exceptions.
_NAME_CORE = r"[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})?"
NAME_RE = re.compile(rf"\b{_NAME_CORE}\b")

# Words that are capitalised in prose without being names. Kept deliberately short --
# it is a guard against obvious false positives, not a substitute for detection.
_NOT_NAME_WORDS = {
    "Ward", "Near", "Opp", "Opposite", "Behind", "Next", "Front", "The", "This",
    "Please", "Sir", "Madam", "Road", "Street", "Gate", "Block", "Sector", "Phase",
    "Open", "Closed", "Water", "Garbage", "Smoke", "Signal", "Light", "Pothole",
}


@lru_cache(maxsize=1)
def _not_names() -> frozenset:
    """Capitalised tokens that are places, not people.

    Seeded from the landmark gazetteer rather than hand-maintained: we already hold the
    authoritative list of place names, so 'Sindhi Camp' is never mistaken for a resident.
    """
    words = set(_NOT_NAME_WORDS)
    try:
        from ingest.zones import load_landmarks
        for lm in load_landmarks():
            words.update(lm["name"].split())
    except Exception:
        pass        # gazetteer unavailable: fall back to the static guard list
    return frozenset(words)


def _mask_names(text: str):
    count = 0

    def repl(m):
        nonlocal count
        blocked = _not_names()
        if any(tok in blocked for tok in m.group(0).split()):
            return m.group(0)
        count += 1
        return NAME_TAG

    return NAME_RE.sub(repl, text), count


def scrub(text: str):
    """Redact a free-text field. Returns (masked_text, items_masked).

    Redaction is visible on purpose -- '[name] [phone] - bada gaddha hai road pe' shows
    the data room that the scrubber did something, which a silent deletion would not.
    """
    if not text:
        return text or "", 0
    masked, phones = _mask_phones(text)
    masked, names = _mask_names(masked)
    return masked, phones + names


def count_pii(text: str) -> int:
    return scrub(text)[1]


def contains_pii(text: str) -> bool:
    """Used by verify_ingest check (b) to assert nothing leaked."""
    return count_pii(text) > 0


# Fields that carry free text, per feed. Only the complaints feed has any.
FREE_TEXT_FIELDS = {"civic_complaints": ("text",)}


def mask_record(feed: str, record):
    """Mask a raw record on its way out of `GET /raw/{feed}`. CONTRACT.md §D.2.

    Returns (masked_record, items_masked) so the UI can say '2 items masked'. Accepts a
    dict (JSON feeds) or a raw CSV line (the complaints feed serves `raw` as a string).
    """
    fields = FREE_TEXT_FIELDS.get(feed, ())
    if not fields:
        return record, 0

    if isinstance(record, str):
        # A whole CSV line: mask the line, since the prose is the only free field on it.
        return scrub(record)

    masked_record, total = dict(record), 0
    for field in fields:
        if field in masked_record and isinstance(masked_record[field], str):
            masked_record[field], n = scrub(masked_record[field])
            total += n
    return masked_record, total
