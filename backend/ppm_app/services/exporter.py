import csv
import io

CSV_COLUMNS = [
    "project_code",
    "title",
    "department",
    "initiator_email",
    "approved_budget_mln",
    "effort_person_months",
    "expected_benefits_mln",
    "final_score",
    "approval_date",
    "protocol_id",
]


def export_approved(projects):
    """Return UTF-8-with-BOM semicolon CSV matching SRS FR-3.5.1."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)

    approved = [p for p in projects if p.get("status") == "APPROVED"]
    approved.sort(
        key=lambda p: (
            p.get("rank_order") is None,
            p.get("rank_order") or 0,
            p.get("project_code") or "",
        )
    )
    for project in approved:
        writer.writerow([project.get(column, "") for column in CSV_COLUMNS])
    return output.getvalue().encode("utf-8-sig")
