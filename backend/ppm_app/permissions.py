from rest_framework.permissions import BasePermission


class HasRole(BasePermission):
    """Set `required_roles` on a view or pass a single role string."""
    def has_permission(self, request, view):
        required = getattr(view, "required_roles", None)
        if not required:
            return bool(request.user and request.user.is_authenticated)
        if isinstance(required, str):
            required = {required}
        return bool(request.user and request.user.is_authenticated and request.user.role in required)
