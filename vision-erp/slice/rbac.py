"""RBAC: deny-by-default grants. Server-side enforcement only — the UI is cosmetic."""
from db import ROLES

# Canonical capabilities. Each maps to the set of roles granted.
# Anything not listed here is denied.
GRANTS = {
    # admin console
    "user.create": {"admin"},
    "user.update": {"admin"},
    "user.read_full": {"admin"},
    "user.read_limited": {"admin", "support"},  # support: name/dept/designation only
    "audit.read": {"admin"},
    # HRMS
    "employee.create": {"admin"},
    "employee.read_all": {"admin", "manager"},
    "employee.read_own": {"admin", "manager", "employee"},
    "leave.approve": {"admin", "manager"},
    "leave.apply": {"admin", "manager", "employee"},
    "leave.read_all": {"admin", "manager"},
    "leave.read_own": {"admin", "manager", "employee"},
    "attendance.read_all": {"admin", "manager"},
    "attendance.self": {"admin", "manager", "employee"},
    # CRM
    "contact.create": {"admin", "manager", "employee"},
    "contact.read_all": {"admin", "manager"},
    "contact.read_own": {"admin", "manager", "employee"},
    "contact.delete": {"admin"},
    "deal.create": {"admin", "manager", "employee"},
    "deal.read_all": {"admin", "manager"},
    "deal.read_own": {"admin", "manager", "employee"},
    "deal.update": {"admin", "manager", "employee"},  # scoped by ownership below
    "deal.delete": {"admin"},
    "activity.create": {"admin", "manager", "employee"},
    # invoicing
    "invoice.create": {"admin", "manager"},
    "invoice.status": {"admin", "manager"},
    "invoice.read_all": {"admin", "manager"},
    "invoice.read_scoped": {"admin", "manager", "employee", "partner"},
    # support
    "ticket.triage": {"admin", "support"},
    "ticket.open": {"admin", "manager", "employee", "support"},
    "ticket.read_own": {"admin", "manager", "employee", "support"},
}


def can(role: str, capability: str) -> bool:
    """Deny by default: unknown role or unknown capability -> False."""
    if role not in ROLES:
        return False
    return role in GRANTS.get(capability, set())


def require(role: str, capability: str):
    if not can(role, capability):
        raise Forbidden(f"role '{role}' lacks capability '{capability}'")


class Forbidden(Exception):
    pass
