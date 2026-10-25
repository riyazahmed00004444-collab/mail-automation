"""
accounts/rbac.py - Role-Based Access Control helpers.
Roles:
  - admin / staff (is_staff=True)  -> full access
  - automation_handler (group)     -> ops access, no credentials/settings
"""
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def is_automation_handler(user):
    return user.groups.filter(name="automation_handler").exists()


def is_admin(user):
    return user.is_staff or user.is_superuser


def has_ops_access(user):
    """Can operate campaigns, process queue, view everything."""
    return is_admin(user) or is_automation_handler(user)


def ops_required(view_func):
    """Decorator: allows admin + automation_handler."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("/accounts/login/")
        if not has_ops_access(request.user):
            messages.error(request, "Access denied.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_required(view_func):
    """Decorator: staff/admin only."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("/accounts/login/")
        if not is_admin(request.user):
            messages.error(request, "Admin access required.")
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)
    return wrapper
