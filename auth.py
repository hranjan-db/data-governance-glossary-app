"""Identity + role resolution.

Databricks Apps injects the signed-in user's email via the ``X-Forwarded-Email``
proxy header. Roles come from the ``data_stewards`` table (owner/steward emails)
plus the ``governance_leads`` / ``step2_approvers`` config lists. In demo mode
every authenticated user gets all roles.
"""

import config
import repository


def current_email(headers) -> str:
    for key in ("X-Forwarded-Email", "x-forwarded-email", "X-Forwarded-User", "x-forwarded-user"):
        value = headers.get(key)
        if value:
            return value
    return "anonymous@local"


def resolve_user(email: str) -> dict:
    email_l = (email or "anonymous").lower()
    is_owner = is_steward = False
    for s in repository.list_stewards():
        if (s.get("data_owner_email") or "").lower() == email_l:
            is_owner = True
        if (s.get("data_steward_email") or "").lower() == email_l:
            is_steward = True

    is_gov_lead = email_l in config.GOVERNANCE_LEADS
    is_de_ops = email_l in config.STEP2_APPROVERS
    demo = config.DEMO_MODE

    can_propose = demo or is_gov_lead or is_steward or is_owner
    can_step1 = demo or is_gov_lead or is_owner
    can_step2 = demo or is_gov_lead or is_de_ops
    is_approver = can_step1 or can_step2
    can_edit_stewards = demo or is_gov_lead

    if demo:
        role_label = "Demo (all roles)"
    elif is_gov_lead:
        role_label = "Governance Lead"
    elif is_de_ops:
        role_label = "Data Engineering"
    elif is_owner:
        role_label = "Data Owner"
    elif is_steward:
        role_label = "Data Steward"
    else:
        role_label = "Viewer"

    tabs = {
        "definitions": True,
        "update": can_propose,
        "review": is_approver,
        "domains": True,
        "coverage": True,
        "my_updates": can_propose or is_approver,
    }
    return {
        "email": email,
        "role_label": role_label,
        "can_propose": can_propose,
        "can_step1": can_step1,
        "can_step2": can_step2,
        "is_approver": is_approver,
        "can_edit_stewards": can_edit_stewards,
        "tabs": tabs,
    }
