ALLOWED_TRANSITIONS = {
    ("DRAFT", "REGISTERED"): {"INITIATOR"},
    ("REGISTERED", "UNDER_EVALUATION"): {"PORTFOLIO_MANAGER", "ADMIN"},
    ("REGISTERED", "REJECTED"): {"PORTFOLIO_MANAGER", "ADMIN"},
    ("UNDER_EVALUATION", "COMMITTEE_REVIEW"): {"PORTFOLIO_MANAGER", "ADMIN"},
    ("COMMITTEE_REVIEW", "APPROVED"): {"SYSTEM"},
    ("COMMITTEE_REVIEW", "REJECTED"): {"SYSTEM"},
}


class InvalidTransition(ValueError):
    pass


class PermissionDenied(ValueError):
    pass


class RequestStateMachine:
    @staticmethod
    def transition(current, target, role):
        roles = ALLOWED_TRANSITIONS.get((current, target))
        if not roles:
            raise InvalidTransition(f"Недопустимый переход статуса: {current} → {target}")
        if role not in roles:
            raise PermissionDenied(f"Роль {role} не может выполнить переход {current} → {target}")
        return target

    @staticmethod
    def can_edit(status):
        return status in {"DRAFT", "REGISTERED"}
