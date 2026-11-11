from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate
from django.core import signing
from django.db import connection, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import audit_event
from .integrations import ldap_bind_valid, notify_users
from .models import (
    AuditAndIntegrationLog, CommitteeVote, Criterion, ExpertAssessment,
    PortfolioScenario, ProjectRequest, RequestStatus, Role, ScenarioRequest,
    ScenarioStatus, User, VoteDecision,
)
from .serializers import (
    AssessmentSerializer, AuditLogSerializer, CriterionSerializer,
    PortfolioScenarioSerializer, ProjectRequestSerializer, UserPublicSerializer,
    VoteSerializer,
)
from .services.committee import tally
from .services.exporter import export_approved
from .services.modeling import auto_select, rank, scenario_totals
from .services.rating import weighted_rating
from .services.state_machine import InvalidTransition, PermissionDenied as StatePermissionDenied, RequestStateMachine
from .services.validators import validate_weights


def require_role(request, *roles):
    if request.user.role not in roles:
        raise PermissionDenied("Недостаточно прав для выполнения операции")


def request_snapshot(obj):
    return {
        "id": obj.pk, "code": obj.code, "title": obj.title, "status": obj.status,
        "budget": str(obj.budget), "expected_benefit": str(obj.expected_benefit),
        "rating": str(obj.rating) if obj.rating is not None else None,
    }


def scenario_summary(scenario):
    rows = []
    for item in scenario.items.select_related("project").all():
        p = item.project
        rows.append({
            "id": p.pk, "code": p.code, "title": p.title,
            "budget": str(p.budget), "expected_benefit": str(p.expected_benefit),
            "rating": str(p.rating) if p.rating is not None else None,
            "status": p.status, "included": item.included, "rank_order": item.rank_order,
        })
    summary = scenario_totals(rows, scenario.budget_limit)
    return {
        "scenario": PortfolioScenarioSerializer(scenario).data,
        "items": rows,
        "totals": {k: str(v) if isinstance(v, Decimal) else v for k, v in summary.items()},
    }


def _send_status_email(project, actor, subject):
    if not project.created_by.email:
        return
    notify_users(
        recipients=[project.created_by.email], subject=subject,
        body=f"Заявка {project.code or project.pk} «{project.title}»: новый статус — {project.get_status_display()}.",
        actor=actor, object_type="ProjectRequest", object_id=project.pk,
    )


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response({"status": "ok", "service": "ppm-api"})


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        password = str(request.data.get("password", ""))
        if not username or not password:
            return Response({"error": "Укажите логин и пароль"}, status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.filter(username=username, is_active=True).first()
        if user and user.locked_until and user.locked_until > timezone.now():
            return Response({"error": "Учётная запись временно заблокирована на 15 минут"}, status=status.HTTP_423_LOCKED)
        if user and user.locked_until and user.locked_until <= timezone.now():
            user.locked_until = None
            user.failed_login_count = 0
            user.save(update_fields=["locked_until", "failed_login_count"])

        valid = False
        if user:
            if settings.LDAP_ENABLED:
                valid = ldap_bind_valid(username, password)
            else:
                valid = user.check_password(password)

        if not valid or not user:
            if user:
                user.failed_login_count += 1
                fields = ["failed_login_count"]
                if user.failed_login_count >= settings.LOGIN_MAX_FAILURES:
                    user.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCK_MINUTES)
                    fields.append("locked_until")
                user.save(update_fields=fields)
                audit_event(actor=user, event_type="LOGIN_FAILED", object_type="User", object_id=user.pk, status="FAILED", request=request)
            return Response({"error": "Неверный логин или пароль"}, status=status.HTTP_401_UNAUTHORIZED)

        user.failed_login_count = 0
        user.locked_until = None
        if settings.LDAP_ENABLED:
            user.ldap_dn = settings.LDAP_DN_TEMPLATE.format(username=username)
        user.save(update_fields=["failed_login_count", "locked_until", "ldap_dn"])
        token = signing.dumps({"user_id": user.pk}, salt="ppm-api-token-v1")
        audit_event(actor=user, event_type="LOGIN_SUCCESS", object_type="User", object_id=user.pk, request=request)
        return Response({"token": token, "user": UserPublicSerializer(user).data})


class MeView(APIView):
    def get(self, request):
        return Response(UserPublicSerializer(request.user).data)


class UserListView(APIView):
    required_roles = {Role.ADMIN, Role.PORTFOLIO_MANAGER}

    def get(self, request):
        require_role(request, Role.ADMIN, Role.PORTFOLIO_MANAGER)
        users = User.objects.filter(is_active=True).order_by("role", "username")
        role = request.query_params.get("role")
        if role:
            users = users.filter(role=role)
        return Response(UserPublicSerializer(users, many=True).data)

    def post(self, request):
        require_role(request, Role.ADMIN)
        data = request.data
        username = str(data.get("username", "")).strip()
        role = data.get("role")
        password = str(data.get("password", ""))
        if role not in Role.values or not username or len(password) < 10:
            raise ValidationError("Нужны логин, допустимая роль и пароль не короче 10 символов")
        if User.objects.filter(username=username).exists():
            raise ValidationError("Пользователь с таким логином уже существует")
        user = User(username=username, role=role, email=str(data.get("email", "")), first_name=str(data.get("first_name", "")), last_name=str(data.get("last_name", "")))
        user.set_password(password)
        user.save()
        audit_event(actor=request.user, event_type="USER_CREATED", object_type="User", object_id=user.pk, after={"username": username, "role": role}, request=request)
        return Response(UserPublicSerializer(user).data, status=status.HTTP_201_CREATED)


class RequestListCreateView(APIView):
    def get(self, request):
        role = request.user.role
        qs = ProjectRequest.objects.select_related("created_by").all()
        if role == Role.INITIATOR:
            qs = qs.filter(created_by=request.user)
        elif role == Role.EXPERT:
            # JSONField filtering varies by database; filtering this bounded MVP list in Python is portable.
            ids = [p.pk for p in qs if request.user.username in (p.assigned_experts or [])]
            qs = qs.filter(pk__in=ids)
        elif role == Role.COMMITTEE_MEMBER:
            qs = qs.filter(status=RequestStatus.COMMITTEE_REVIEW)
        elif role not in {Role.PORTFOLIO_MANAGER, Role.ADMIN}:
            raise PermissionDenied("Недостаточно прав")
        status_filter = request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response(ProjectRequestSerializer(qs, many=True).data)

    def post(self, request):
        require_role(request, Role.INITIATOR)
        serializer = ProjectRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save(created_by=request.user)
        audit_event(actor=request.user, event_type="REQUEST_CREATED", object_type="ProjectRequest", object_id=project.pk, after=request_snapshot(project), request=request)
        return Response(ProjectRequestSerializer(project).data, status=status.HTTP_201_CREATED)


class RequestDetailView(APIView):
    def get(self, request, pk):
        project = get_object_or_404(ProjectRequest.objects.select_related("created_by"), pk=pk)
        if request.user.role == Role.INITIATOR and project.created_by_id != request.user.pk:
            raise PermissionDenied("Можно просматривать только собственные заявки")
        if request.user.role == Role.EXPERT and request.user.username not in project.assigned_experts:
            raise PermissionDenied("Заявка не назначена этому эксперту")
        if request.user.role == Role.COMMITTEE_MEMBER and project.status != RequestStatus.COMMITTEE_REVIEW:
            raise PermissionDenied("Заявка не передана комитету")
        return Response(ProjectRequestSerializer(project).data)

    def patch(self, request, pk):
        project = get_object_or_404(ProjectRequest, pk=pk)
        require_role(request, Role.INITIATOR)
        if project.created_by_id != request.user.pk:
            raise PermissionDenied("Можно изменять только собственные заявки")
        if not RequestStateMachine.can_edit(project.status):
            raise PermissionDenied("Редактирование заблокировано после начала оценки")  # FR-3.1.5 / TC-REL-05 require HTTP 403
        before = request_snapshot(project)
        serializer = ProjectRequestSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        project = serializer.save()
        audit_event(actor=request.user, event_type="REQUEST_UPDATED", object_type="ProjectRequest", object_id=project.pk, before=before, after=request_snapshot(project), request=request)
        return Response(ProjectRequestSerializer(project).data)


class RequestSubmitView(APIView):
    def post(self, request, pk):
        require_role(request, Role.INITIATOR)
        with transaction.atomic():
            project = get_object_or_404(ProjectRequest.objects.select_for_update(), pk=pk)
            if project.created_by_id != request.user.pk:
                raise PermissionDenied("Можно отправлять только собственные заявки")
            try:
                RequestStateMachine.transition(project.status, RequestStatus.REGISTERED, request.user.role)
            except (InvalidTransition, StatePermissionDenied) as exc:
                raise ValidationError(str(exc)) from exc
            before = request_snapshot(project)
            year = timezone.now().year
            prefix = f"PRJ-{year}-"
            if connection.vendor == "postgresql":
                # Serialize code generation per year: without this, parallel submissions compute the same
                # number and the UNIQUE constraint turns the loser into an HTTP 500.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", [int(year)])
            existing_codes = ProjectRequest.objects.filter(code__startswith=prefix).values_list("code", flat=True)
            sequence = max([int(code.rsplit("-", 1)[1]) for code in existing_codes if code.rsplit("-", 1)[-1].isdigit()] or [0]) + 1
            project.code = f"{prefix}{sequence:04d}"
            project.status = RequestStatus.REGISTERED
            project.submitted_at = timezone.now()
            project.save(update_fields=["code", "status", "submitted_at", "updated_at"])
            audit_event(actor=request.user, event_type="REQUEST_SUBMITTED", object_type="ProjectRequest", object_id=project.pk, before=before, after=request_snapshot(project), request=request)
            transaction.on_commit(lambda: _send_status_email(project, request.user, "Заявка зарегистрирована"))
        return Response(ProjectRequestSerializer(project).data)


class RequestRejectView(APIView):
    def post(self, request, pk):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        reason = str(request.data.get("reason", "")).strip()
        if len(reason) < 10:
            raise ValidationError({"reason": "Причина отклонения должна содержать не менее 10 символов"})
        with transaction.atomic():
            project = get_object_or_404(ProjectRequest.objects.select_for_update(), pk=pk)
            if project.status != RequestStatus.REGISTERED:
                raise ValidationError("Отклонить можно только зарегистрированную заявку")
            before = request_snapshot(project)
            project.status = RequestStatus.REJECTED
            project.rejection_reason = reason
            project.save(update_fields=["status", "rejection_reason", "updated_at"])
            audit_event(actor=request.user, event_type="REQUEST_REJECTED", object_type="ProjectRequest", object_id=project.pk, before=before, after=request_snapshot(project), request=request)
            transaction.on_commit(lambda: _send_status_email(project, request.user, "Заявка отклонена"))
        return Response(ProjectRequestSerializer(project).data)


class CriteriaView(APIView):
    def get(self, request):
        if request.user.role not in {Role.EXPERT, Role.PORTFOLIO_MANAGER, Role.ADMIN}:
            raise PermissionDenied("Недостаточно прав")
        return Response(CriterionSerializer(Criterion.objects.filter(active=True), many=True).data)

    def post(self, request):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        payload = request.data.get("criteria")
        if not isinstance(payload, list) or not payload:
            raise ValidationError("Передайте непустой массив criteria")
        names = [str(row.get("name", "")).strip() for row in payload if isinstance(row, dict)]
        if len(names) != len(payload) or any(not n for n in names) or len(set(names)) != len(names):
            raise ValidationError("У каждого критерия должно быть уникальное непустое название")
        try:
            validate_weights([row.get("weight") for row in payload])
        except (ValueError, TypeError) as exc:
            raise ValidationError({"criteria": str(exc)}) from exc
        serializer = CriterionSerializer(data=payload, many=True)
        serializer.is_valid(raise_exception=True)
        before = list(Criterion.objects.filter(active=True).values("name", "weight", "reverse_direction"))
        with transaction.atomic():
            names_set = set(names)
            Criterion.objects.filter(active=True).exclude(name__in=names_set).update(active=False)
            result = []
            for row in serializer.validated_data:
                criterion, _ = Criterion.objects.update_or_create(
                    name=row["name"],
                    defaults={"description": row.get("description", ""), "weight": row["weight"], "reverse_direction": row.get("reverse_direction", False), "active": True},
                )
                result.append(criterion)
            audit_event(actor=request.user, event_type="CRITERIA_TEMPLATE_UPDATED", object_type="Criterion", object_id="active-template", before=before, after=CriterionSerializer(result, many=True).data, request=request)
        return Response(CriterionSerializer(result, many=True).data)


class AssignExpertsView(APIView):
    def post(self, request, pk):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        usernames = request.data.get("experts")
        if not isinstance(usernames, list) or not 1 <= len(usernames) <= 10 or len(set(usernames)) != len(usernames):
            raise ValidationError("Нужно назначить от 1 до 10 уникальных экспертов")
        project = get_object_or_404(ProjectRequest, pk=pk)
        if project.status != RequestStatus.REGISTERED:
            raise ValidationError("Назначать экспертов можно только зарегистрированной заявке")
        criteria = list(Criterion.objects.filter(active=True))
        try:
            validate_weights([c.weight for c in criteria])
        except ValueError as exc:
            raise ValidationError({"criteria": str(exc)}) from exc
        experts = list(User.objects.filter(username__in=usernames, role=Role.EXPERT, is_active=True))
        if {u.username for u in experts} != set(usernames):
            raise ValidationError("Все логины должны существовать и иметь роль EXPERT")
        before = request_snapshot(project)
        try:
            project.status = RequestStateMachine.transition(project.status, RequestStatus.UNDER_EVALUATION, request.user.role)
        except (InvalidTransition, StatePermissionDenied) as exc:
            raise ValidationError(str(exc)) from exc
        project.assigned_experts = usernames
        project.save(update_fields=["status", "assigned_experts", "updated_at"])
        audit_event(actor=request.user, event_type="EXPERTS_ASSIGNED", object_type="ProjectRequest", object_id=project.pk, before=before, after={**request_snapshot(project), "experts": usernames}, request=request)
        recipients = [u.email for u in [project.created_by, *experts] if u.email]
        if recipients:
            transaction.on_commit(lambda: notify_users(recipients=recipients, subject="Назначена экспертная оценка", body=f"Для заявки {project.code} назначена экспертная оценка.", actor=request.user, object_type="ProjectRequest", object_id=project.pk))
        return Response(ProjectRequestSerializer(project).data)


class AssessmentListCreateView(APIView):
    def get(self, request):
        qs = ExpertAssessment.objects.select_related("project", "expert", "criterion").all()
        project_id = request.query_params.get("project")
        if project_id:
            qs = qs.filter(project_id=project_id)
        if request.user.role == Role.EXPERT:
            qs = qs.filter(expert=request.user)
        elif request.user.role not in {Role.ADMIN, Role.PORTFOLIO_MANAGER}:
            raise PermissionDenied("Недостаточно прав")
        return Response(AssessmentSerializer(qs, many=True).data)

    def post(self, request):
        require_role(request, Role.EXPERT)
        serializer = AssessmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        project = data["project"]
        criterion = data["criterion"]
        score = data["score"]
        if project.status != RequestStatus.UNDER_EVALUATION:
            raise ValidationError("Заявка не находится на этапе оценки")
        if request.user.username not in project.assigned_experts:
            raise PermissionDenied("Эксперт не назначен на эту заявку")
        if not criterion.active:
            raise ValidationError("Критерий не входит в активный шаблон")
        before_obj = ExpertAssessment.objects.filter(project=project, expert=request.user, criterion=criterion).first()
        before = {"score": before_obj.score, "comment": before_obj.comment} if before_obj else None
        assessment, _ = ExpertAssessment.objects.update_or_create(
            project=project, expert=request.user, criterion=criterion,
            defaults={"score": score, "comment": data.get("comment", "")},
        )
        active_criteria = list(Criterion.objects.filter(active=True))
        active_experts = list(User.objects.filter(username__in=project.assigned_experts, role=Role.EXPERT, is_active=True))
        assessments = list(ExpertAssessment.objects.filter(project=project, criterion__in=active_criteria, expert__in=active_experts).values("expert_id", "criterion_id", "score"))
        expected_count = len(active_criteria) * len(active_experts)
        if expected_count and len(assessments) == expected_count:
            rating = weighted_rating(
                [{"id": c.pk, "weight": c.weight, "reverse_direction": c.reverse_direction, "name": c.name} for c in active_criteria],
                [{"expert_id": a["expert_id"], "criterion_id": a["criterion_id"], "score": a["score"]} for a in assessments],
                expert_ids=[e.pk for e in active_experts],
            )
            project.rating = rating
            project.save(update_fields=["rating", "updated_at"])
        audit_event(actor=request.user, event_type="ASSESSMENT_SAVED", object_type="ExpertAssessment", object_id=assessment.pk, before=before, after={"project_id": project.pk, "criterion_id": criterion.pk, "score": assessment.score, "rating": str(project.rating) if project.rating is not None else None}, request=request)
        return Response(AssessmentSerializer(assessment).data, status=status.HTTP_201_CREATED)


class ScenarioListCreateView(APIView):
    def get(self, request):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN, Role.COMMITTEE_MEMBER)
        qs = PortfolioScenario.objects.prefetch_related("items__project").all()
        if request.user.role == Role.COMMITTEE_MEMBER:
            qs = qs.filter(status=ScenarioStatus.SUBMITTED)
        return Response([scenario_summary(s) for s in qs])

    def post(self, request):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        serializer = PortfolioScenarioSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scenario = serializer.save(created_by=request.user)
        audit_event(actor=request.user, event_type="SCENARIO_CREATED", object_type="PortfolioScenario", object_id=scenario.pk, after={"name": scenario.name, "budget_limit": str(scenario.budget_limit)}, request=request)
        return Response(scenario_summary(scenario), status=status.HTTP_201_CREATED)


class ScenarioAutoSelectView(APIView):
    def post(self, request, pk):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        with transaction.atomic():
            scenario = get_object_or_404(PortfolioScenario.objects.select_for_update(), pk=pk)
            if scenario.status != ScenarioStatus.DRAFT:
                raise ValidationError("Автоподбор доступен только для черновика сценария")
            projects = list(ProjectRequest.objects.filter(status=RequestStatus.UNDER_EVALUATION, rating__isnull=False).order_by("created_at", "id"))
            payload = [{"id": p.pk, "code": p.code, "title": p.title, "budget": p.budget, "expected_benefit": p.expected_benefit, "rating": p.rating, "created_at": p.created_at.isoformat(), "submitted_at": p.submitted_at.isoformat() if p.submitted_at else ""} for p in projects]
            result = auto_select(payload, scenario.budget_limit)
            selected_ids = set(result["selected_ids"])
            ranked_ids = {row["id"]: row["rank_order"] for row in result["ranked"]}
            for project in projects:
                ScenarioRequest.objects.update_or_create(
                    scenario=scenario, project=project,
                    defaults={"included": project.pk in selected_ids, "rank_order": ranked_ids[project.pk]},
                )
                project.rank_order = ranked_ids[project.pk]
                project.save(update_fields=["rank_order", "updated_at"])
            audit_event(actor=request.user, event_type="SCENARIO_AUTO_SELECTED", object_type="PortfolioScenario", object_id=scenario.pk, after={"selected_ids": sorted(selected_ids), "used": str(result["used"]), "remainder": str(result["remainder"])}, request=request)
        return Response({**scenario_summary(scenario), "optimizer": {k: str(v) if isinstance(v, Decimal) else v for k, v in result.items() if k not in {"ranked"}}})


class ScenarioToggleView(APIView):
    def post(self, request, pk):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        try:
            project_id = int(request.data.get("project_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"project_id": "Укажите числовой идентификатор заявки"}) from exc
        included = request.data.get("included")
        if not isinstance(included, bool):
            raise ValidationError({"included": "Ожидается true или false"})
        scenario = get_object_or_404(PortfolioScenario, pk=pk)
        if scenario.status != ScenarioStatus.DRAFT:
            raise ValidationError("Изменять состав можно только в черновике")
        item = get_object_or_404(ScenarioRequest, scenario=scenario, project_id=project_id)
        item.included = included
        item.save(update_fields=["included"])
        audit_event(actor=request.user, event_type="SCENARIO_ITEM_TOGGLED", object_type="ScenarioRequest", object_id=item.pk, after={"scenario_id": scenario.pk, "project_id": project_id, "included": included}, request=request)
        return Response(scenario_summary(scenario))


class ScenarioSubmitView(APIView):
    def post(self, request, pk):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        with transaction.atomic():
            scenario = get_object_or_404(PortfolioScenario.objects.select_for_update(), pk=pk)
            if scenario.status != ScenarioStatus.DRAFT:
                raise ValidationError("Сценарий уже передан или закрыт")
            summary = scenario_summary(scenario)
            totals = summary["totals"]
            if not totals["within_budget"]:
                raise ValidationError("Превышен лимит бюджета: сохранение и передача запрещены")
            selected = list(scenario.items.filter(included=True).select_related("project"))
            if not selected:
                raise ValidationError("В сценарии должен быть включён хотя бы один проект")
            if any(item.project.status != RequestStatus.UNDER_EVALUATION or item.project.rating is None for item in selected):
                raise ValidationError("Все включённые проекты должны быть полностью оценены")
            for item in selected:
                item.project.status = RequestStatus.COMMITTEE_REVIEW
                item.project.save(update_fields=["status", "updated_at"])
            scenario.status = ScenarioStatus.SUBMITTED
            scenario.submitted_at = timezone.now()
            scenario.save(update_fields=["status", "submitted_at"])
            audit_event(actor=request.user, event_type="SCENARIO_SUBMITTED", object_type="PortfolioScenario", object_id=scenario.pk, after={"selected_ids": [i.project_id for i in selected]}, request=request)
            recipients = list(User.objects.filter(role=Role.COMMITTEE_MEMBER, is_active=True).exclude(email="").values_list("email", flat=True))
            if recipients:
                transaction.on_commit(lambda: notify_users(recipients=recipients, subject="Сценарий передан инвестиционному комитету", body=f"Сценарий «{scenario.name}» готов к голосованию.", actor=request.user, object_type="PortfolioScenario", object_id=scenario.pk))
                for item in selected:
                    transaction.on_commit(lambda item=item: _send_status_email(item.project, request.user, "Заявка передана комитету"))
        return Response(scenario_summary(scenario))


class VoteView(APIView):
    def post(self, request):
        require_role(request, Role.COMMITTEE_MEMBER)
        try:
            scenario_id = int(request.data.get("scenario_id"))
            project_id = int(request.data.get("project_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Нужны числовые scenario_id и project_id") from exc
        decision = request.data.get("decision")
        if decision not in VoteDecision.values:
            raise ValidationError({"decision": f"Допустимые значения: {', '.join(VoteDecision.values)}"})
        scenario = get_object_or_404(PortfolioScenario, pk=scenario_id, status=ScenarioStatus.SUBMITTED)
        item = get_object_or_404(ScenarioRequest, scenario=scenario, project_id=project_id, included=True)
        vote, created = CommitteeVote.objects.update_or_create(
            scenario=scenario, project=item.project, member=request.user,
            defaults={"decision": decision},
        )
        audit_event(actor=request.user, event_type="COMMITTEE_VOTE_CAST", object_type="CommitteeVote", object_id=vote.pk, after={"scenario_id": scenario.pk, "project_id": project_id, "decision": decision}, request=request)
        return Response(VoteSerializer(vote).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class CommitteeFinalizeView(APIView):
    def post(self, request):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        try:
            scenario_id = int(request.data.get("scenario_id"))
        except (TypeError, ValueError) as exc:
            raise ValidationError({"scenario_id": "Укажите числовой идентификатор сценария"}) from exc
        with transaction.atomic():
            scenario = get_object_or_404(PortfolioScenario.objects.select_for_update(), pk=scenario_id, status=ScenarioStatus.SUBMITTED)
            total_members = User.objects.filter(role=Role.COMMITTEE_MEMBER, is_active=True).count()
            items = list(scenario.items.filter(included=True).select_related("project"))
            outcomes = []
            for item in items:
                decisions = list(CommitteeVote.objects.filter(scenario=scenario, project=item.project).values_list("decision", flat=True))
                result = tally(decisions, total_members)
                if not result["quorum_reached"]:
                    raise ValidationError(f"Не достигнут кворум для заявки {item.project.code}: {result['voted']}/{result['total_members']}, требуется {result['quorum_required']}")
                outcomes.append((item, result))
            if not outcomes:
                raise ValidationError("Нет включённых проектов для фиксации решения")
            for item, result in outcomes:
                before = request_snapshot(item.project)
                item.project.status = RequestStatus.APPROVED if result["approved"] else RequestStatus.REJECTED
                item.project.save(update_fields=["status", "updated_at"])
                audit_event(actor=request.user, event_type="COMMITTEE_DECISION_FINALIZED", object_type="ProjectRequest", object_id=item.project.pk, before=before, after={**request_snapshot(item.project), "vote_result": result}, request=request)
                transaction.on_commit(lambda project=item.project: _send_status_email(project, request.user, "Решение инвестиционного комитета"))
            scenario.status = ScenarioStatus.FINALIZED
            scenario.finalized_at = timezone.now()
            scenario.save(update_fields=["status", "finalized_at"])
            protocol = [{"project_id": item.project_id, "code": item.project.code, "title": item.project.title, **result} for item, result in outcomes]
            audit_event(actor=request.user, event_type="PROTOCOL_FINALIZED", object_type="PortfolioScenario", object_id=scenario.pk, after={"scenario": scenario.name, "protocol": protocol}, request=request)
        return Response({"scenario_id": scenario.pk, "scenario_status": scenario.status, "protocol": protocol})


class ProtocolListView(APIView):
    def get(self, request):
        require_role(request, Role.COMMITTEE_MEMBER, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        result = []
        scenarios = PortfolioScenario.objects.filter(status=ScenarioStatus.FINALIZED).prefetch_related("items__project", "votes__member")
        for scenario in scenarios:
            projects = []
            for item in scenario.items.filter(included=True).select_related("project"):
                votes = list(scenario.votes.filter(project=item.project).values_list("decision", flat=True))
                outcome = tally(votes, User.objects.filter(role=Role.COMMITTEE_MEMBER, is_active=True).count())
                projects.append({"id": item.project_id, "code": item.project.code, "title": item.project.title, "status": item.project.status, **outcome})
            result.append({"scenario": PortfolioScenarioSerializer(scenario).data, "projects": projects})
        return Response(result)


class ExportPortfolioView(APIView):
    def get(self, request):
        require_role(request, Role.PORTFOLIO_MANAGER, Role.ADMIN)
        approved_projects = list(
            ProjectRequest.objects.filter(status=RequestStatus.APPROVED)
            .select_related("created_by")
            .values(
                "id", "code", "title", "department", "expected_benefit", "budget",
                "effort", "rating", "rank_order", "status", "created_by__email",
            )
        )
        rows = []
        for project in approved_projects:
            # The project model intentionally has no separate protocol entity: the
            # finalized scenario is the stored voting protocol for this MVP.
            finalized_item = (
                ScenarioRequest.objects.filter(
                    project_id=project["id"], scenario__status=ScenarioStatus.FINALIZED
                )
                .select_related("scenario")
                .order_by("-scenario__finalized_at", "-scenario_id")
                .first()
            )
            scenario = finalized_item.scenario if finalized_item else None
            rows.append({
                "project_code": project["code"] or "",
                "title": project["title"],
                "department": project["department"],
                "initiator_email": project["created_by__email"] or "",
                "approved_budget_mln": project["budget"],
                "effort_person_months": project["effort"],
                "expected_benefits_mln": project["expected_benefit"],
                "final_score": project["rating"] if project["rating"] is not None else "",
                "approval_date": scenario.finalized_at.isoformat() if scenario and scenario.finalized_at else "",
                "protocol_id": f"PPM-{scenario.pk}" if scenario else "",
                "rank_order": project["rank_order"],
                "status": project["status"],
            })

        payload = export_approved(rows)
        audit_event(
            actor=request.user, event_type="PORTFOLIO_EXPORTED",
            object_type="ProjectRequest", object_id="approved-portfolio",
            after={"rows": len(rows), "format": "csv;utf-8-sig"}, request=request,
        )
        response = HttpResponse(payload, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="portfolio.csv"'
        response["X-Content-Type-Options"] = "nosniff"
        return response


class AuditLogView(APIView):
    required_roles = {Role.ADMIN}

    def get(self, request):
        require_role(request, Role.ADMIN)
        qs = AuditAndIntegrationLog.objects.select_related("user").all()
        if request.query_params.get("username"):
            qs = qs.filter(user__username=request.query_params["username"])
        if request.query_params.get("event_type"):
            qs = qs.filter(event_type__icontains=request.query_params["event_type"])
        if request.query_params.get("date_from"):
            qs = qs.filter(created_at__date__gte=request.query_params["date_from"])
        if request.query_params.get("date_to"):
            qs = qs.filter(created_at__date__lte=request.query_params["date_to"])
        return Response(AuditLogSerializer(qs[:500], many=True).data)
