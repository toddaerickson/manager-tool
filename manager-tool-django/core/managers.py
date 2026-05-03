from django.db import models


class TenantManager(models.Manager):
    """Default manager for tenant-scoped models. Forces explicit
    `for_manager(manager_id)` access so the audit C1 cross-tenant
    isolation guarantee can't be regressed by accident.

    Bare `.objects.all()` still works (Django requires it for admin,
    test fixtures, and migrations) but per-request views and services
    must call `for_manager(request.user.id)` first.
    """

    def for_manager(self, manager_id: int):
        if manager_id is None:
            raise ValueError("for_manager requires a manager_id (got None)")
        return self.get_queryset().filter(manager_id=manager_id)
