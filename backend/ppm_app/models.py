from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Role(models.TextChoices):
    INITIATOR = "INITIATOR", "Инициатор"
    EXPERT = "EXPERT", "Эксперт"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER", "Менеджер портфеля"
    COMMITTEE_MEMBER = "COMMITTEE_MEMBER", "Член комитета"
    ADMIN = "ADMIN", "Администратор"


class User(AbstractUser):
    role = models.CharField(max_length=32, choices=Role.choices, default=Role.INITIATOR, db_index=True)
    failed_login_count = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    ldap_dn = models.CharField(max_length=255, blank=True)

    def delete(self, *args, **kwargs):
        # Accounts are deactivated, never physically deleted (DR-4.4.2).
        raise ValidationError("Физическое удаление пользователей запрещено; используйте is_active=False.")

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class RequestStatus(models.TextChoices):
    DRAFT = "DRAFT", "Черновик"
    REGISTERED = "REGISTERED", "Зарегистрирована"
    UNDER_EVALUATION = "UNDER_EVALUATION", "На оценке"
    COMMITTEE_REVIEW = "COMMITTEE_REVIEW", "На рассмотрении комитета"
    APPROVED = "APPROVED", "Утверждена"
    REJECTED = "REJECTED", "Отклонена"


class ProjectRequest(models.Model):
    code = models.CharField(max_length=20, unique=True, null=True, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField()
    department = models.CharField(max_length=160)
    strategic_goal = models.CharField(max_length=250)
    expected_benefit = models.DecimalField(max_digits=12, decimal_places=2)
    budget = models.DecimalField(max_digits=12, decimal_places=2)
    effort = models.DecimalField(max_digits=8, decimal_places=1)
    status = models.CharField(max_length=24, choices=RequestStatus.choices, default=RequestStatus.DRAFT, db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="project_requests")
    assigned_experts = models.JSONField(default=list, blank=True)
    rating = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    rank_order = models.PositiveIntegerField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(expected_benefit__gt=0) & Q(expected_benefit__lte=100000), name="ppm_benefit_range"),
            models.CheckConstraint(condition=Q(budget__gt=0) & Q(budget__lte=100000), name="ppm_budget_range"),
            models.CheckConstraint(condition=Q(effort__gte=0.5) & Q(effort__lte=10000), name="ppm_effort_range"),
        ]

    def __str__(self):
        return f"{self.code or 'DRAFT'} — {self.title}"


class Criterion(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=400, blank=True)
    weight = models.DecimalField(max_digits=6, decimal_places=4)
    reverse_direction = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["id"]
        constraints = [models.CheckConstraint(condition=Q(weight__gte=0) & Q(weight__lte=1), name="ppm_criterion_weight_range")]

    def __str__(self):
        return f"{self.name} ({self.weight})"


class ExpertAssessment(models.Model):
    project = models.ForeignKey(ProjectRequest, on_delete=models.PROTECT, related_name="assessments")
    expert = models.ForeignKey(User, on_delete=models.PROTECT, related_name="expert_assessments")
    criterion = models.ForeignKey(Criterion, on_delete=models.PROTECT, related_name="assessments")
    score = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=1000, blank=True)
    assessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "expert", "criterion"], name="ppm_unique_expert_assessment"),
            models.CheckConstraint(condition=Q(score__gte=1) & Q(score__lte=5), name="ppm_score_range"),
        ]
        ordering = ["project_id", "expert_id", "criterion_id"]


class ScenarioStatus(models.TextChoices):
    DRAFT = "DRAFT", "Черновик"
    SUBMITTED = "SUBMITTED", "Передан комитету"
    FINALIZED = "FINALIZED", "Решение зафиксировано"


class PortfolioScenario(models.Model):
    name = models.CharField(max_length=160)
    budget_limit = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=16, choices=ScenarioStatus.choices, default=ScenarioStatus.DRAFT)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="portfolio_scenarios")
    created_at = models.DateTimeField(default=timezone.now)
    submitted_at = models.DateTimeField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [models.CheckConstraint(condition=Q(budget_limit__gt=0) & Q(budget_limit__lte=100000), name="ppm_scenario_budget_range")]

    def __str__(self):
        return self.name


class ScenarioRequest(models.Model):
    scenario = models.ForeignKey(PortfolioScenario, on_delete=models.PROTECT, related_name="items")
    project = models.ForeignKey(ProjectRequest, on_delete=models.PROTECT, related_name="scenario_items")
    included = models.BooleanField(default=False)
    rank_order = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scenario", "project"], name="ppm_unique_scenario_project")]
        ordering = ["rank_order", "project_id"]


class VoteDecision(models.TextChoices):
    FOR = "FOR", "За"
    AGAINST = "AGAINST", "Против"
    ABSTAIN = "ABSTAIN", "Воздержался"


class CommitteeVote(models.Model):
    scenario = models.ForeignKey(PortfolioScenario, on_delete=models.PROTECT, related_name="votes")
    project = models.ForeignKey(ProjectRequest, on_delete=models.PROTECT, related_name="committee_votes")
    member = models.ForeignKey(User, on_delete=models.PROTECT, related_name="committee_votes")
    decision = models.CharField(max_length=8, choices=VoteDecision.choices)
    voted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["scenario", "project", "member"], name="ppm_unique_committee_vote")]


class AuditAndIntegrationLog(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(User, on_delete=models.PROTECT, null=True, blank=True, related_name="audit_events")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    event_type = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    before_data = models.JSONField(null=True, blank=True)
    after_data = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=12, default="SUCCESS")
    message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Журнал аудита неизменяем: допускается только INSERT.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Удаление записей журнала аудита запрещено.")

    def __str__(self):
        return f"{self.created_at.isoformat()} {self.event_type} {self.object_type}:{self.object_id}"
