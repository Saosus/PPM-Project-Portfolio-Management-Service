from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from ppm_app.models import Criterion, ExpertAssessment, ProjectRequest, RequestStatus, Role, User
from ppm_app.services.rating import weighted_rating


USERS = [
    ("initiator", "INITIATOR", "initiator123", "Иван", "Инициатор"),
    ("expert1", "EXPERT", "expert12345", "Анна", "Эксперт 1"),
    ("expert2", "EXPERT", "expert23456", "Олег", "Эксперт 2"),
    ("manager", "PORTFOLIO_MANAGER", "manager123", "Мария", "Менеджер"),
    ("committee1", "COMMITTEE_MEMBER", "committee123", "Сергей", "Комитет 1"),
    ("committee2", "COMMITTEE_MEMBER", "committee234", "Елена", "Комитет 2"),
    ("committee3", "COMMITTEE_MEMBER", "committee345", "Дмитрий", "Комитет 3"),
    ("admin", "ADMIN", "admin12345", "Администратор", "Системный"),
]

CRITERIA = [
    ("Соответствие стратегии", "Насколько проект поддерживает стратегические цели", "0.3000", False),
    ("Ожидаемая ценность", "Ожидаемая ценность и польза для компании", "0.3000", False),
    ("Реализуемость", "Реализуемость в рамках имеющихся ресурсов", "0.2500", False),
    ("Управляемость риска", "Качество мер по выявлению и снижению рисков", "0.1500", False),
]

PROJECTS = [
    # code, title, budget, benefit, effort, expert1 scores, expert2 scores
    ("PRJ-2026-0001", "Модернизация производственной сети", "12.00", "30.00", "24.0", [4, 5, 3, 4], [4, 4, 4, 3]),
    ("PRJ-2026-0002", "Единая платформа мониторинга", "10.00", "25.00", "18.0", [5, 4, 4, 4], [5, 5, 4, 5]),
    ("PRJ-2026-0003", "Обновление системы отчётности", "20.00", "40.00", "30.0", [3, 3, 5, 3], [4, 3, 4, 4]),
    ("PRJ-2026-0004", "Автоматизация резервного копирования", "8.00", "20.00", "12.0", [4, 5, 2, 4], [3, 5, 3, 3]),
]


class Command(BaseCommand):
    help = "Создаёт демо-учётные записи, критерии и тестовые заявки (идемпотентно)."

    def handle(self, *args, **options):
        users = {}
        for username, role, password, first_name, last_name in USERS:
            user, created = User.objects.get_or_create(username=username, defaults={
                "role": role, "first_name": first_name, "last_name": last_name,
                "email": f"{username}@example.org", "is_active": True,
                "is_staff": role == Role.ADMIN,
            })
            changed = user.role != role or not user.is_active
            user.role = role
            user.is_active = True
            user.is_staff = role == Role.ADMIN
            user.first_name = first_name
            user.last_name = last_name
            user.email = f"{username}@example.org"
            user.ldap_dn = f"uid={username},ou=people,dc=example,dc=org"
            if created or not user.has_usable_password():
                user.set_password(password)
                changed = True
            if changed:
                user.save()
            users[username] = user

        criteria = []
        for name, description, weight, reverse in CRITERIA:
            obj, _ = Criterion.objects.update_or_create(name=name, defaults={
                "description": description, "weight": Decimal(weight),
                "reverse_direction": reverse, "active": True,
            })
            criteria.append(obj)

        for code, title, budget, benefit, effort, scores1, scores2 in PROJECTS:
            project, created = ProjectRequest.objects.get_or_create(code=code, defaults={
                "title": title,
                "description": f"Демонстрационная заявка на реализацию проекта: {title}. Данные используются только для учебной проверки макета.",
                "department": "ИТ-служба", "strategic_goal": "Повышение надёжности цифровых сервисов",
                "expected_benefit": Decimal(benefit), "budget": Decimal(budget), "effort": Decimal(effort),
                "status": RequestStatus.UNDER_EVALUATION, "created_by": users["initiator"],
                "assigned_experts": ["expert1", "expert2"], "submitted_at": timezone.now(),
            })
            if not created:
                # Avoid overwriting changes a student makes after initial setup.
                continue
            for expert_name, scores in (("expert1", scores1), ("expert2", scores2)):
                for criterion, score in zip(criteria, scores, strict=True):
                    ExpertAssessment.objects.create(project=project, expert=users[expert_name], criterion=criterion, score=score, comment="Демо-оценка для тестирования")
            assessments = list(project.assessments.values("expert_id", "criterion_id", "score"))
            project.rating = weighted_rating(
                [{"id": c.pk, "name": c.name, "weight": c.weight, "reverse_direction": c.reverse_direction} for c in criteria],
                [{"expert_id": a["expert_id"], "criterion_id": a["criterion_id"], "score": a["score"]} for a in assessments],
                expert_ids=[users["expert1"].pk, users["expert2"].pk],
            )
            project.save(update_fields=["rating", "updated_at"])

        self.stdout.write(self.style.SUCCESS("Демо-данные готовы. Для повторного запуска сохранённые заявки не перезаписываются."))
        self.stdout.write("Локальный вход: LDAP_ENABLED=0; в Compose LDAP_ENABLED=1.")
        self.stdout.write("Логины: initiator, expert1, expert2, manager, committee1..3, admin. Пароли перечислены в README.")
