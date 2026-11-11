from decimal import Decimal
from rest_framework import serializers
from .models import (
    AuditAndIntegrationLog, CommitteeVote, Criterion, ExpertAssessment,
    PortfolioScenario, ProjectRequest, ScenarioRequest, User,
)
from .services.validators import validate_request, validate_score


class UserPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "first_name", "last_name", "email", "role", "is_active")


class ProjectRequestSerializer(serializers.ModelSerializer):
    created_by = UserPublicSerializer(read_only=True)
    assigned_experts = serializers.ListField(child=serializers.CharField(), read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ProjectRequest
        fields = (
            "id", "code", "title", "description", "department", "strategic_goal",
            "expected_benefit", "budget", "effort", "status", "status_label",
            "created_by", "assigned_experts", "rating", "rank_order", "rejection_reason",
            "created_at", "submitted_at", "updated_at",
        )
        read_only_fields = ("id", "code", "status", "created_by", "assigned_experts", "rating", "rank_order", "rejection_reason", "created_at", "submitted_at", "updated_at")

    def validate(self, attrs):
        data = dict(attrs)
        instance = getattr(self, "instance", None)
        if instance:
            data = {
                "title": attrs.get("title", instance.title),
                "description": attrs.get("description", instance.description),
                "budget": attrs.get("budget", instance.budget),
                "expected_benefit": attrs.get("expected_benefit", instance.expected_benefit),
                "effort": attrs.get("effort", instance.effort),
            }
        try:
            validate_request(data)
        except ValueError as exc:
            raise serializers.ValidationError({"detail": str(exc)}) from exc
        return attrs


class CriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Criterion
        fields = ("id", "name", "description", "weight", "reverse_direction", "active", "created_at")
        read_only_fields = ("id", "created_at")

    def validate_weight(self, value):
        if value < Decimal("0") or value > Decimal("1"):
            raise serializers.ValidationError("Вес должен находиться в диапазоне 0..1")
        return value


class AssessmentSerializer(serializers.ModelSerializer):
    expert_username = serializers.CharField(source="expert.username", read_only=True)
    criterion_name = serializers.CharField(source="criterion.name", read_only=True)

    class Meta:
        model = ExpertAssessment
        fields = ("id", "project", "expert", "expert_username", "criterion", "criterion_name", "score", "comment", "assessed_at")
        read_only_fields = ("id", "expert", "expert_username", "assessed_at", "criterion_name")

    def validate_score(self, value):
        try:
            return validate_score(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc


class ScenarioRequestSerializer(serializers.ModelSerializer):
    project = ProjectRequestSerializer(read_only=True)
    project_id = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = ScenarioRequest
        fields = ("id", "project", "project_id", "included", "rank_order")
        read_only_fields = ("id", "rank_order")


class PortfolioScenarioSerializer(serializers.ModelSerializer):
    created_by = UserPublicSerializer(read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = PortfolioScenario
        fields = ("id", "name", "budget_limit", "status", "status_label", "created_by", "created_at", "submitted_at", "finalized_at")
        read_only_fields = ("id", "status", "created_by", "created_at", "submitted_at", "finalized_at")

    def validate_budget_limit(self, value):
        if not Decimal("0") < value <= Decimal("100000"):
            raise serializers.ValidationError("Лимит бюджета должен быть в диапазоне (0; 100000.00]")
        return value


class VoteSerializer(serializers.ModelSerializer):
    member_username = serializers.CharField(source="member.username", read_only=True)

    class Meta:
        model = CommitteeVote
        fields = ("id", "scenario", "project", "member", "member_username", "decision", "voted_at")
        read_only_fields = ("id", "member", "member_username", "voted_at")


class AuditLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True, allow_null=True)

    class Meta:
        model = AuditAndIntegrationLog
        fields = ("id", "created_at", "username", "user", "ip_address", "event_type", "object_type", "object_id", "before_data", "after_data", "status", "message")
        read_only_fields = fields
