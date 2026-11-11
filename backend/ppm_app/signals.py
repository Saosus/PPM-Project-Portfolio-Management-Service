from django.db import connections
from django.db.models.signals import post_migrate
from django.dispatch import receiver


@receiver(post_migrate)
def protect_audit_rows(sender, using, **kwargs):
    """Enforce append-only audit records in PostgreSQL, in addition to model guards."""
    if getattr(sender, "label", None) != "ppm_app":
        return
    connection = connections[using]
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("""
            CREATE OR REPLACE FUNCTION ppm_reject_audit_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Audit log is append-only';
            END;
            $$ LANGUAGE plpgsql;
        """)
        cursor.execute("""
            DROP TRIGGER IF EXISTS ppm_audit_no_update_delete ON ppm_app_auditandintegrationlog;
        """)
        cursor.execute("""
            CREATE TRIGGER ppm_audit_no_update_delete
            BEFORE UPDATE OR DELETE ON ppm_app_auditandintegrationlog
            FOR EACH ROW EXECUTE FUNCTION ppm_reject_audit_mutation();
        """)
