from rest_framework.permissions import BasePermission


class IsWorker(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == "worker"


class IsEmployer(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == "employer"
