from decimal import Decimal, ROUND_HALF_UP
from collections.abc import Mapping, Sequence
from .validators import decimal_value, validate_score, validate_weights


CENT = Decimal("0.01")


def weighted_rating(criteria: Sequence[Mapping], assessments: Sequence[Mapping], expert_ids=None) -> Decimal:
    """Calculate the weighted average of expert scores using Decimal only.

    criteria: [{"id": 1, "weight": "0.30", "reverse_direction": False}, ...]
    assessments: [{"expert_id": 1, "criterion_id": 1, "score": 5}, ...]
    expert_ids: expected expert ids. If supplied, every expert must rate every active criterion.
    """
    if not criteria:
        raise ValueError("Нет активных критериев")
    weights = [decimal_value(c["weight"], "weight") for c in criteria]
    validate_weights(weights)
    criterion_ids = [c["id"] for c in criteria]
    if len(set(criterion_ids)) != len(criterion_ids):
        raise ValueError("ID критериев должны быть уникальными")

    grouped = {cid: [] for cid in criterion_ids}
    seen = set()
    for item in assessments:
        cid = item["criterion_id"]
        eid = item["expert_id"]
        if cid not in grouped:
            continue
        score = validate_score(item["score"])
        key = (eid, cid)
        if key in seen:
            raise ValueError("Обнаружены дубликаты экспертной оценки")
        seen.add(key)
        criterion = next(c for c in criteria if c["id"] == cid)
        if criterion.get("reverse_direction", False):
            score = 6 - score
        grouped[cid].append((eid, score))

    expected = set(expert_ids) if expert_ids is not None else None
    result = Decimal("0")
    for criterion, weight in zip(criteria, weights, strict=True):
        scored = grouped[criterion["id"]]
        if not scored:
            raise ValueError(f"Нет оценок по критерию {criterion.get('name', criterion['id'])}")
        if expected is not None and {eid for eid, _ in scored} != expected:
            raise ValueError("Не все назначенные эксперты оценили каждый критерий")
        average = sum((Decimal(s) for _, s in scored), Decimal("0")) / Decimal(len(scored))
        result += weight * average
    return result.quantize(CENT, rounding=ROUND_HALF_UP)
