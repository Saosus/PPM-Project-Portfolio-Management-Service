from decimal import Decimal, InvalidOperation

WEIGHT_TOLERANCE = Decimal("0.0001")


def decimal_value(value, field="value") -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field}: требуется число") from exc
    if not result.is_finite():
        raise ValueError(f"{field}: число должно быть конечным")
    return result


def validate_request(data: dict) -> None:
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    if not 5 <= len(title) <= 200:
        raise ValueError("Название должно содержать от 5 до 200 символов")
    if not 20 <= len(description) <= 5000:
        raise ValueError("Описание должно содержать от 20 до 5000 символов")
    for field in ("budget", "expected_benefit"):
        value = decimal_value(data.get(field), field)
        if not Decimal("0") < value <= Decimal("100000.00"):
            raise ValueError(f"{field}: допустимый диапазон (0; 100000.00]")
    effort = decimal_value(data.get("effort"), "effort")
    if not Decimal("0.5") <= effort <= Decimal("10000.0"):
        raise ValueError("Трудозатраты должны быть в диапазоне 0.5–10000.0 чел.-мес.")


def validate_weights(weights) -> None:
    if not weights:
        raise ValueError("Должен быть задан хотя бы один критерий")
    total = sum((decimal_value(w, "weight") for w in weights), Decimal("0"))
    if abs(total - Decimal("1")) > WEIGHT_TOLERANCE:
        raise ValueError(f"Сумма весов должна быть равна 1.0000 (получено {total})")
    if any(decimal_value(w, "weight") < 0 or decimal_value(w, "weight") > 1 for w in weights):
        raise ValueError("Вес каждого критерия должен находиться в диапазоне 0..1")


def validate_score(score) -> int:
    # Reject floats such as 3.5 even if they could be cast to an integer.
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError("Оценка эксперта должна быть целым числом от 1 до 5")
    return score
