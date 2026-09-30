"""RBAC: role -> scope mapping, deny-by-default. Scopes are enforced server-side
in every route handler — never trusted from the client."""
import json

ROLE_SCOPES = {
    "user": {"openid", "profile", "email", "devices:manage"},
    "support": {"support:read", "support:write"},
    "admin": {"admin:users", "admin:audit", "admin:keys"},
}


def scopes_for_roles(roles: list) -> set:
    out = set()
    for r in roles:
        out |= ROLE_SCOPES.get(r, set())
    return out


def roles_of(user_row: dict) -> list:
    try:
        return json.loads(user_row.get("roles") or "[]")
    except Exception:
        return []


def has_scope(token_scopes: set, required: str) -> bool:
    return required in token_scopes
