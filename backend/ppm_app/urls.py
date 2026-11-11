from django.urls import path
from .views import (
    AssessmentListCreateView, AssignExpertsView, AuditLogView, CommitteeFinalizeView,
    CriteriaView, ExportPortfolioView, LoginView, MeView, ProtocolListView,
    RequestDetailView, RequestListCreateView, RequestRejectView, RequestSubmitView,
    ScenarioAutoSelectView, ScenarioListCreateView, ScenarioSubmitView,
    ScenarioToggleView, UserListView, VoteView,
)

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("users/", UserListView.as_view(), name="users"),
    path("requests/", RequestListCreateView.as_view(), name="requests"),
    path("requests/<int:pk>/", RequestDetailView.as_view(), name="request-detail"),
    path("requests/<int:pk>/submit/", RequestSubmitView.as_view(), name="request-submit"),
    path("requests/<int:pk>/reject/", RequestRejectView.as_view(), name="request-reject"),
    path("requests/<int:pk>/assign/", AssignExpertsView.as_view(), name="request-assign"),
    path("criteria/", CriteriaView.as_view(), name="criteria"),
    path("assessments/", AssessmentListCreateView.as_view(), name="assessments"),
    path("scenarios/", ScenarioListCreateView.as_view(), name="scenarios"),
    path("scenarios/<int:pk>/auto-select/", ScenarioAutoSelectView.as_view(), name="scenario-auto-select"),
    path("scenarios/<int:pk>/toggle/", ScenarioToggleView.as_view(), name="scenario-toggle"),
    path("scenarios/<int:pk>/submit/", ScenarioSubmitView.as_view(), name="scenario-submit"),
    path("committee/vote/", VoteView.as_view(), name="committee-vote"),
    path("committee/finalize/", CommitteeFinalizeView.as_view(), name="committee-finalize"),
    path("protocols/", ProtocolListView.as_view(), name="protocols"),
    path("export/portfolio.csv", ExportPortfolioView.as_view(), name="export-portfolio"),
    path("admin/logs/", AuditLogView.as_view(), name="audit-logs"),
]
