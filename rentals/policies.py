"""Role -> action permission map. Empty stub for M0; wired up properly in M1 (rentals/api still uses
membership(request, org_id, write=(...)) tuples directly until then)."""

ROLE_ACTIONS: dict[str,set[str]] = {}

def can(role:str,action:str) -> bool:
    return action in ROLE_ACTIONS.get(role,set())
