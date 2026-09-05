"""Who may see what.

The receipt rule is the only one with an effective date: finance re-runs approvals as
they stood at a point in time when a claim is queried months later, and the screen
passes that date through. Everything else is a plain comparison.

The catch around the receipt decision was added after the morning the rule service was
unreachable and every manager was locked out of their own approvals. Allowing on a
failed decision was signed off as the lesser of two evils for a screen that only shows
a receipt reference.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class DecisionFailed(RuntimeError):
    pass


def _effective(as_of: str | None) -> datetime:
    """Effective date for the decision. '0' or empty means now."""
    raw = (as_of or "0").strip()
    if raw in ("", "0"):
        return datetime.now(timezone.utc)
    seconds = int(raw)
    if seconds < 0:
        raise ValueError("effective date is before the epoch")
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def receipt(claim: dict[str, Any], viewer: dict[str, Any], claimant: dict[str, Any],
            as_of: str | None) -> bool:
    """True when the viewer may see this claim's receipt at the effective date."""
    try:
        moment = _effective(as_of)
    except (ValueError, OverflowError, OSError) as exc:
        raise DecisionFailed(f"effective date could not be read: {exc}") from exc

    if viewer["id"] == claim["person_id"]:
        return True
    if viewer["role"] == "operations":
        return True
    if claimant.get("manager_id") == viewer["id"]:
        return True
    if viewer["team"] == "Finance" and claim["stage"] in ("submitted", "reviewed", "reimbursed"):
        # Finance only picks a claim up once it has been submitted, and never before
        # the claim existed.
        created = claim.get("created_at", "")
        return not created or created[:10] <= moment.date().isoformat()
    return False


def leave_visible(request_row: dict[str, Any], viewer: dict[str, Any],
                  owner: dict[str, Any]) -> bool:
    if viewer["id"] == request_row["person_id"]:
        return True
    if viewer["role"] == "operations":
        return True
    return owner.get("manager_id") == viewer["id"]
