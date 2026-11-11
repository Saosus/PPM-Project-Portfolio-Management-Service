from .models import AuditAndIntegrationLog


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    return forwarded or request.META.get("REMOTE_ADDR") or None


def audit_event(*, actor=None, event_type, object_type="", object_id="", before=None, after=None, status="SUCCESS", message="", request=None):
    return AuditAndIntegrationLog.objects.create(
        user=actor if getattr(actor, "pk", None) else None,
        ip_address=client_ip(request),
        event_type=event_type,
        object_type=object_type,
        object_id=str(object_id or ""),
        before_data=before,
        after_data=after,
        status=status,
        message=message[:500],
    )
