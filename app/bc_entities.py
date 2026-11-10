"""Match the entity sets this sync needs to the names a Business Central API publishes.

The finance guide names sets like `bankAccounts`; the BCTEST integration route was seen
publishing `BankAccount`. A name is accepted when it is the exact name, an explicit override
from VGGOS_BC_ENTITY_MAP, or the only case/plural variant present. Anything else is reported
missing. A missing name is never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ENTITY_SET = re.compile(r"<EntitySet\b[^>]*\bName=\"([^\"]+)\"", re.IGNORECASE)

# BCTEST integration currently publishes accounts/bankAccounts/generalLedgerEntries,
# not the full finance-guide catalogue. Setups stay optional until Elijah publishes them.
REQUIRED = ("glAccounts", "bankAccounts", "glEntries")
OPTIONAL = (
    "generalLedgerSetups",
    "accountingPeriods",
    "dimensions",
    "dimensionValues",
    "dimensionSetEntries",
    "glBalancesByPeriod",
    "bankAccountLedgerEntries",
)
ALL_LOGICAL = REQUIRED + OPTIONAL


@dataclass(frozen=True)
class EntityMatch:
    logical: str
    remote: str | None
    required: bool
    how: str  # exact | override | variant | missing | ambiguous

    @property
    def found(self) -> bool:
        return self.remote is not None

    def as_dict(self) -> dict:
        return {
            "logical": self.logical,
            "remote": self.remote,
            "required": self.required,
            "found": self.found,
            "how": self.how,
        }


def parse_entity_sets(metadata: str) -> list[str]:
    seen: list[str] = []
    for name in ENTITY_SET.findall(metadata or ""):
        if name not in seen:
            seen.append(name)
    return seen


def parse_overrides(text: str) -> dict[str, str]:
    """`bankAccounts=BankAccount, glEntries=GLEntry` -> mapping. Bad pairs are ignored."""
    mapping: dict[str, str] = {}
    for part in (text or "").split(","):
        if "=" not in part:
            continue
        left, right = (piece.strip() for piece in part.split("=", 1))
        if left in ALL_LOGICAL and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,99}", right or ""):
            mapping[left] = right
    return mapping


def _singular(word: str) -> set[str]:
    forms = {word}
    lower = word.lower()
    if lower.endswith("entries"):
        forms.add(word[:-3] + "y")
    elif lower.endswith("ies"):
        forms.add(word[:-3] + "y")
    elif lower.endswith("s"):
        forms.add(word[:-1])
    return forms


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def resolve(published: list[str], overrides: dict[str, str] | None = None) -> list[EntityMatch]:
    overrides = overrides or {}
    by_key: dict[str, list[str]] = {}
    for name in published:
        by_key.setdefault(_key(name), []).append(name)
    results: list[EntityMatch] = []
    for logical in ALL_LOGICAL:
        required = logical in REQUIRED
        wanted = overrides.get(logical)
        if wanted is not None:
            results.append(EntityMatch(logical, wanted if wanted in published else None, required, "override" if wanted in published else "missing"))
            continue
        if logical in published:
            results.append(EntityMatch(logical, logical, required, "exact"))
            continue
        candidates: list[str] = []
        for form in _singular(logical):
            for name in by_key.get(_key(form), []):
                if name not in candidates:
                    candidates.append(name)
        if len(candidates) == 1:
            results.append(EntityMatch(logical, candidates[0], required, "variant"))
        elif len(candidates) > 1:
            results.append(EntityMatch(logical, None, required, "ambiguous"))
        else:
            results.append(EntityMatch(logical, None, required, "missing"))
    return results


def remote_names(matches: list[EntityMatch]) -> dict[str, str]:
    return {match.logical: match.remote for match in matches if match.remote}


def missing_required(matches: list[EntityMatch]) -> list[EntityMatch]:
    return [match for match in matches if match.required and not match.found]
