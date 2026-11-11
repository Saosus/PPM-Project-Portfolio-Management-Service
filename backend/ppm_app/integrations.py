import logging
import re
from django.conf import settings
from django.core.mail import send_mail
from ldap3 import Connection, Server
from .audit import audit_event

logger = logging.getLogger(__name__)
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


def ldap_bind_valid(username: str, password: str) -> bool:
    if not _USERNAME_RE.fullmatch(username) or not password:
        return False
    dn = settings.LDAP_DN_TEMPLATE.format(username=username)
    # ldap3 requires an integer receive_timeout (it is packed with struct); a float raises struct.error.
    timeout = max(1, int(round(settings.LDAP_CONNECT_TIMEOUT)))
    try:
        server = Server(settings.LDAP_URL, connect_timeout=timeout, get_info=None)
        with Connection(server, user=dn, password=password, receive_timeout=timeout, auto_bind=True):
            return True
    except Exception as exc:
        # Do not log the supplied password or expose LDAP internals to the client.
        logger.warning("LDAP bind failed for uid=%s (%s)", username, type(exc).__name__)
        return False


def notify_users(*, recipients, subject, body, actor=None, object_type="", object_id=""):
    recipients = [r for r in recipients if r]
    if not recipients:
        return True
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=False)
        audit_event(actor=actor, event_type="EMAIL_SENT", object_type=object_type, object_id=object_id, after={"recipients": recipients, "subject": subject}, status="SUCCESS")
        return True
    except Exception as exc:  # integration failure must not roll back the business action
        logger.exception("SMTP notification failed")
        audit_event(actor=actor, event_type="EMAIL_FAILED", object_type=object_type, object_id=object_id, after={"recipients": recipients, "subject": subject}, status="FAILED", message=type(exc).__name__)
        return False
