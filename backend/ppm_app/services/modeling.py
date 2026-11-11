from dataclasses import dataclass
from decimal import Decimal
from .validators import decimal_value

CENT = Decimal("0.01")


def rank(projects):
    """Sort scored projects by rating desc, budget asc, registration order asc."""
    def key(p):
        rating = decimal_value(p.get("rating") if p.get("rating") is not None else 0, "rating")
        budget = decimal_value(p["budget"], "budget")
        return (-rating, budget, p.get("submitted_at") or p.get("created_at") or "", p.get("code") or "", p.get("id", 0))
    return sorted(projects, key=key)


@dataclass(frozen=True)
class _State:
    cost: int
    benefit: Decimal
    rating: Decimal
    selected: tuple[int, ...]


def _state_better_same_cost(a: _State, b: _State) -> bool:
    """Whether a is better than b when both consume the same budget."""
    return (a.benefit, a.rating, tuple(-i for i in a.selected)) > (b.benefit, b.rating, tuple(-i for i in b.selected))


def _prune(states):
    """Pareto-prune exact knapsack states while retaining deterministic tie-breaks."""
    by_cost = {}
    for state in states:
        prev = by_cost.get(state.cost)
        if prev is None or _state_better_same_cost(state, prev):
            by_cost[state.cost] = state
    kept = []
    prior = []
    for state in (by_cost[c] for c in sorted(by_cost)):
        dominated = False
        for other in prior:
            if other.benefit > state.benefit:
                dominated = True
                break
            if other.benefit == state.benefit and other.rating > state.rating:
                dominated = True
                break
            if other.benefit == state.benefit and other.rating == state.rating:
                # Since costs are ascending, other is cheaper; the lower spend wins.
                dominated = True
                break
        if not dominated:
            kept.append(state)
            prior.append(state)
    return kept


def auto_select(projects, budget_limit):
    """Exact deterministic 0/1 knapsack optimizer.

    Primary objective: total expected benefit; tie-breaks: total rating, lower spend,
    and better (earlier) ranks. Amounts are converted to integer hundredths to avoid
    binary floating-point errors. Returns selected ids/codes and summary totals.
    """
    limit = decimal_value(budget_limit, "budget_limit")
    if limit < 0:
        raise ValueError("Лимит бюджета не может быть отрицательным")
    ordered = rank(projects)
    items = []
    for index, project in enumerate(ordered):
        budget = decimal_value(project["budget"], "budget").quantize(CENT)
        benefit = decimal_value(project["expected_benefit"], "expected_benefit")
        rating = decimal_value(project.get("rating") or 0, "rating")
        if budget <= 0 or benefit < 0:
            raise ValueError("Бюджет должен быть положительным, выгода — неотрицательной")
        items.append((index, project, int(budget * 100), benefit, rating))

    capacity = int(limit.quantize(CENT) * 100)
    states = [_State(0, Decimal("0"), Decimal("0"), tuple())]
    for index, project, cost, benefit, rating in items:
        additions = [
            _State(s.cost + cost, s.benefit + benefit, s.rating + rating, s.selected + (index,))
            for s in states if s.cost + cost <= capacity
        ]
        states = _prune([*states, *additions])

    # Maximizing benefit, then rating; spend least; finally prefer better ranks.
    best = states[0]
    for candidate in states[1:]:
        if candidate.benefit > best.benefit:
            best = candidate
        elif candidate.benefit == best.benefit:
            if candidate.rating > best.rating:
                best = candidate
            elif candidate.rating == best.rating:
                if candidate.cost < best.cost:
                    best = candidate
                elif candidate.cost == best.cost and candidate.selected < best.selected:
                    best = candidate

    selected_indexes = set(best.selected)
    selected_projects = [p for index, p, *_ in items if index in selected_indexes]
    used = (Decimal(best.cost) / Decimal(100)).quantize(CENT)
    return {
        "selected": [p.get("code") or p.get("id") for p in selected_projects],
        "selected_ids": [p.get("id") for p in selected_projects if p.get("id") is not None],
        "used": used,
        "remainder": (limit.quantize(CENT) - used).quantize(CENT),
        "total_benefit": best.benefit.quantize(CENT),
        "total_rating": best.rating.quantize(CENT),
        "ranked": [{**p, "rank_order": index + 1} for index, p, *_ in items],
    }


def scenario_totals(projects, budget_limit):
    limit = decimal_value(budget_limit, "budget_limit").quantize(CENT)
    selected = [p for p in projects if p.get("included")]
    used = sum((decimal_value(p["budget"], "budget") for p in selected), Decimal("0")).quantize(CENT)
    benefit = sum((decimal_value(p["expected_benefit"], "expected_benefit") for p in selected), Decimal("0")).quantize(CENT)
    return {
        "used": used,
        "remainder": (limit - used).quantize(CENT),
        "percentage": (used / limit * Decimal("100")).quantize(Decimal("0.01")) if limit else Decimal("0.00"),
        "total_benefit": benefit,
        "within_budget": used <= limit,
    }
