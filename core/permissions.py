from rest_framework.permissions import BasePermission


class PoCRolePermission(BasePermission):
    def has_permission(self, request, view):
        if getattr(view, 'allow_anonymous', False):
            return True

        user = getattr(request, 'poc_user', None)
        if user is None:
            return False

        allowed_roles = getattr(view, 'allowed_roles', None)
        if allowed_roles is None:
            return True
        return user.role in allowed_roles
