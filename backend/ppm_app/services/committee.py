from collections import Counter
from math import ceil


def tally(votes, total_members):
    """Count FOR/AGAINST/ABSTAIN, counting abstentions in the majority denominator."""
    decisions = [v.get("decision", v) if isinstance(v, dict) else v for v in votes]
    counts = Counter(decisions)
    voted = sum(counts.values())
    required = ceil((2 * total_members) / 3) if total_members else 1
    quorum = voted >= required
    approved = quorum and voted > 0 and counts["FOR"] / voted > 0.5
    return {
        "for": counts["FOR"],
        "against": counts["AGAINST"],
        "abstain": counts["ABSTAIN"],
        "voted": voted,
        "total_members": total_members,
        "quorum_required": required,
        "quorum_reached": quorum,
        "approved": approved,
        "status": "APPROVED" if approved else "REJECTED" if quorum else "PENDING_QUORUM",
    }
