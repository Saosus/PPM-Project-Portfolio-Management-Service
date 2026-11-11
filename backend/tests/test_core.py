"""Pure unit/property-like tests for the deterministic calculation core."""
import random
import time
import unittest
from decimal import Decimal

from ppm_app.services.committee import tally
from ppm_app.services.exporter import CSV_COLUMNS, export_approved
from ppm_app.services.modeling import auto_select, rank, scenario_totals
from ppm_app.services.rating import weighted_rating
from ppm_app.services.state_machine import InvalidTransition, PermissionDenied, RequestStateMachine
from ppm_app.services.validators import validate_request, validate_score, validate_weights


class RatingTests(unittest.TestCase):
    def setUp(self):
        self.criteria = [
            {"id": 1, "name": "Стратегия", "weight": "0.3000"},
            {"id": 2, "name": "Ценность", "weight": "0.3000"},
            {"id": 3, "name": "Реализуемость", "weight": "0.2500"},
            {"id": 4, "name": "Риски", "weight": "0.1500"},
        ]
        self.assessments = []
        for expert, scores in ((10, [5, 4, 4, 3]), (11, [3, 4, 2, 5])):
            for criterion, score in zip(self.criteria, scores, strict=True):
                self.assessments.append({"expert_id": expert, "criterion_id": criterion["id"], "score": score})

    def test_TC_FUNC_05_reference_rating_is_3_75(self):
        self.assertEqual(weighted_rating(self.criteria, self.assessments, expert_ids=[10, 11]), Decimal("3.75"))

    def test_reverse_scale(self):
        criteria = [{"id": 1, "weight": "1.0000", "reverse_direction": True}]
        self.assertEqual(weighted_rating(criteria, [{"expert_id": 1, "criterion_id": 1, "score": 1}], [1]), Decimal("5.00"))

    def test_incomplete_expert_matrix_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Не все назначенные"):
            weighted_rating(self.criteria, self.assessments[:-1], expert_ids=[10, 11])

    def test_duplicate_score_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "дубликаты"):
            weighted_rating([{"id": 1, "weight": "1.0000"}], [{"expert_id": 1, "criterion_id": 1, "score": 4}, {"expert_id": 1, "criterion_id": 1, "score": 4}])

    def test_score_boundaries_and_fraction(self):
        self.assertEqual(validate_score(1), 1)
        self.assertEqual(validate_score(5), 5)
        for invalid in (0, 6, 3.5, True, "4"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_score(invalid)

    def test_weight_normalization_tolerance(self):
        validate_weights(["0.5000", "0.50005"])
        for weights in (["0.49", "0.50"], ["0.6", "0.5"], ["-0.1", "1.1"]):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                validate_weights(weights)


class ModelingTests(unittest.TestCase):
    def test_TC_FUNC_06_rank_tie_break_by_budget(self):
        projects = [
            {"id": 1, "code": "P1", "rating": "4.50", "budget": "12.00", "expected_benefit": "30.00"},
            {"id": 2, "code": "P2", "rating": "4.50", "budget": "10.00", "expected_benefit": "25.00"},
        ]
        self.assertEqual([p["code"] for p in rank(projects)], ["P2", "P1"])

    def test_budget_optimizer_respects_limit(self):
        projects = [
            {"id": 1, "code": "P1", "rating": "4.8", "budget": "12", "expected_benefit": "30"},
            {"id": 2, "code": "P2", "rating": "4.6", "budget": "10", "expected_benefit": "25"},
            {"id": 3, "code": "P3", "rating": "4.3", "budget": "20", "expected_benefit": "40"},
            {"id": 4, "code": "P4", "rating": "4.1", "budget": "8", "expected_benefit": "20"},
        ]
        result = auto_select(projects, "30.00")
        self.assertEqual(set(result["selected"]), {"P1", "P2", "P4"})
        self.assertEqual(result["used"], Decimal("30.00"))
        self.assertLessEqual(result["used"], Decimal("30.00"))

    def test_equal_benefit_prefers_higher_rating_then_lower_spend(self):
        projects = [
            {"id": 1, "code": "P1", "rating": "4.0", "budget": "8.00", "expected_benefit": "10"},
            {"id": 2, "code": "P2", "rating": "5.0", "budget": "9.00", "expected_benefit": "10"},
        ]
        self.assertEqual(auto_select(projects, "10")["selected"], ["P2"])
        projects[1]["rating"] = "4.0"
        self.assertEqual(auto_select(projects, "10")["selected"], ["P1"])

    def test_budget_cent_rounding(self):
        projects = [
            {"id": 1, "code": "A", "rating": "5", "budget": "10.01", "expected_benefit": "7"},
            {"id": 2, "code": "B", "rating": "4", "budget": "10.00", "expected_benefit": "6"},
        ]
        result = auto_select(projects, "10.00")
        self.assertEqual(result["selected"], ["B"])

    def test_300_random_sets_match_exhaustive_oracle(self):
        rng = random.Random(20261023)
        for case in range(300):
            n = rng.randint(1, 10)
            projects = []
            for i in range(n):
                projects.append({
                    "id": i + 1, "code": f"P{i + 1}",
                    "rating": str(rng.randint(100, 500) / 100),
                    "budget": f"{rng.randint(1, 30)}.{rng.choice(["00", "25", "50", "75"])}",
                    "expected_benefit": f"{rng.randint(1, 50)}.00",
                })
            limit = Decimal(str(rng.randint(5, 60)))
            ordered = rank(projects)
            best = None
            for mask in range(1 << len(ordered)):
                selected = [ordered[i] for i in range(len(ordered)) if mask & (1 << i)]
                cost = sum((Decimal(str(p["budget"])) for p in selected), Decimal("0"))
                if cost > limit:
                    continue
                benefit = sum((Decimal(str(p["expected_benefit"])) for p in selected), Decimal("0"))
                rating = sum((Decimal(str(p["rating"])) for p in selected), Decimal("0"))
                rank_tuple = tuple(i for i in range(len(ordered)) if mask & (1 << i))
                objective = (benefit, rating, -cost, tuple(-i for i in rank_tuple))
                if best is None or objective > best[0]:
                    best = (objective, {p["code"] for p in selected})
            result = auto_select(projects, limit)
            self.assertEqual(set(result["selected"]), best[1], msg=f"case={case}")

    def test_scenario_totals(self):
        projects = [
            {"included": True, "budget": "12.50", "expected_benefit": "20.00"},
            {"included": False, "budget": "100.00", "expected_benefit": "50.00"},
            {"included": True, "budget": "7.50", "expected_benefit": "10.00"},
        ]
        result = scenario_totals(projects, "25.00")
        self.assertEqual(result["used"], Decimal("20.00"))
        self.assertEqual(result["remainder"], Decimal("5.00"))
        self.assertTrue(result["within_budget"])

    def test_100_project_runtime_target(self):
        rng = random.Random(123)
        projects = [{"id": i, "code": f"P{i:03}", "rating": str(2 + rng.randint(0, 300) / 100), "budget": f"{rng.randint(1, 24)}.00", "expected_benefit": f"{rng.randint(1, 45)}.00"} for i in range(100)]
        start = time.perf_counter()
        result = auto_select(projects, "180.00")
        elapsed = time.perf_counter() - start
        self.assertLessEqual(result["used"], Decimal("180.00"))
        self.assertLess(elapsed, 5.0, f"autoselect took {elapsed:.3f}s")


class CommitteeTests(unittest.TestCase):
    def test_TC_FUNC_07_quorum_and_majority(self):
        self.assertEqual(tally(["FOR", "FOR", "AGAINST"], 3)["status"], "APPROVED")
        self.assertEqual(tally(["FOR", "AGAINST", "AGAINST"], 3)["status"], "REJECTED")

    def test_abstentions_are_in_denominator(self):
        result = tally(["FOR", "ABSTAIN"], 2)
        self.assertFalse(result["approved"])
        self.assertEqual(result["voted"], 2)

    def test_quorum_three_of_six(self):
        result = tally(["FOR", "FOR", "AGAINST"], 6)
        self.assertFalse(result["quorum_reached"])

    def test_quorum_four_member_examples(self):
        self.assertTrue(tally(["FOR", "FOR", "AGAINST", "AGAINST"], 4)["quorum_reached"])
        self.assertEqual(tally(["FOR", "FOR", "FOR", "AGAINST"], 4)["status"], "APPROVED")


class WorkflowAndExportTests(unittest.TestCase):
    def test_valid_state_transition(self):
        self.assertEqual(RequestStateMachine.transition("DRAFT", "REGISTERED", "INITIATOR"), "REGISTERED")

    def test_invalid_transition_rejected(self):
        with self.assertRaises(InvalidTransition):
            RequestStateMachine.transition("DRAFT", "APPROVED", "INITIATOR")

    def test_wrong_role_rejected(self):
        with self.assertRaises(PermissionDenied):
            RequestStateMachine.transition("DRAFT", "REGISTERED", "EXPERT")

    def test_admin_can_perform_manager_transition(self):
        self.assertEqual(RequestStateMachine.transition("REGISTERED", "UNDER_EVALUATION", "ADMIN"), "UNDER_EVALUATION")

    def test_request_editing_locks_after_evaluation_starts(self):
        self.assertTrue(RequestStateMachine.can_edit("REGISTERED"))
        self.assertFalse(RequestStateMachine.can_edit("UNDER_EVALUATION"))

    def test_request_boundaries(self):
        valid = {"title": "Проект", "description": "Описание проекта длиной более двадцати символов", "budget": "0.01", "expected_benefit": "100000.00", "effort": "0.5"}
        validate_request(valid)
        for title in ("abcd", "x" * 201):
            with self.subTest(title_len=len(title)), self.assertRaises(ValueError):
                validate_request({**valid, "title": title})
        for budget in ("0.00", "100000.01", "-1.00"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                validate_request({**valid, "budget": budget})

    def test_TC_FUNC_08_csv_format_and_only_approved(self):
        data = [
            {
                "project_code": "PRJ-2026-0001", "title": "Утверждённый", "department": "ИТ",
                "initiator_email": "initiator@example.org", "approved_budget_mln": "5.00",
                "effort_person_months": "2.0", "expected_benefits_mln": "15.00",
                "final_score": "4.50", "approval_date": "2026-10-23T12:00:00+00:00",
                "protocol_id": "PPM-7", "rank_order": 1, "status": "APPROVED",
            },
            {
                "project_code": "PRJ-2026-0002", "title": "Не утверждённый", "department": "ИТ",
                "initiator_email": "other@example.org", "approved_budget_mln": "4.00",
                "effort_person_months": "1.0", "expected_benefits_mln": "12.00",
                "final_score": "4.20", "approval_date": "", "protocol_id": "",
                "rank_order": 2, "status": "REJECTED",
            },
        ]
        payload = export_approved(data)
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        rows = payload.decode("utf-8-sig").splitlines()
        self.assertEqual(len(CSV_COLUMNS), 10)
        self.assertEqual(
            rows[0].split(";"),
            [
                "project_code", "title", "department", "initiator_email",
                "approved_budget_mln", "effort_person_months", "expected_benefits_mln",
                "final_score", "approval_date", "protocol_id",
            ],
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("Утверждённый", rows[1])
        self.assertNotIn("Не утверждённый", rows[1])
        self.assertIn("initiator@example.org", rows[1])
        self.assertIn("PPM-7", rows[1])
        self.assertIn(";", rows[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
