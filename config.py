"""Configuration loader for the Data Governance Business Glossary app.

All environment-specific settings live in ``config.json`` (or a file pointed to
by ``GLOSSARY_CONFIG_PATH``). A few keys can be overridden by environment
variables so the same image promotes across workspaces without edits. Nothing
here is customer-specific — the app browses whatever Unity Catalog the service
principal can see.
"""

import json
import os
from pathlib import Path

_DEFAULTS = {
    "app_title": "Data Governance - Business Glossary",
    "environment": "DEMO",
    "allowed_catalogs": [],
    "default_catalog": "",
    "hidden_catalogs": ["system", "__databricks_internal", "samples"],
    "workflow_catalog": "main",
    "workflow_schema": "data_governance_app",
    "proposals_table_name": "glossary_proposals",
    "stewards_table_name": "data_stewards",
    "ai_model_endpoint": "databricks-claude-sonnet-4-6",
    "domain_context": "an enterprise data warehouse",
    "sensitivity_values": [
        "Public (Non-Sensitive)",
        "Internal Use Only",
        "Confidential",
        "Secret",
        "Not Defined",
    ],
    "uc_writeback": {"enabled": True, "write_comment": True, "write_sensitivity_tag": True},
    "roles": {"governance_leads": [], "step2_approvers": [], "demo_mode": True},
}

# Scalar env overrides -> top-level config key.
_ENV_SCALARS = {
    "GLOSSARY_ENVIRONMENT": "environment",
    "GLOSSARY_DEFAULT_CATALOG": "default_catalog",
    "GLOSSARY_WORKFLOW_CATALOG": "workflow_catalog",
    "GLOSSARY_WORKFLOW_SCHEMA": "workflow_schema",
    "AI_MODEL_ENDPOINT": "ai_model_endpoint",
}
# Comma-separated list env overrides.
_ENV_LISTS = {"GLOSSARY_ALLOWED_CATALOGS": "allowed_catalogs"}
# Comma-separated list overrides nested under roles.
_ENV_ROLE_LISTS = {"GLOSSARY_GOVERNANCE_LEADS": "governance_leads", "GLOSSARY_STEP2_APPROVERS": "step2_approvers"}


def _csv(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def _load():
    cfg = json.loads(json.dumps(_DEFAULTS))  # deep copy
    path = os.environ.get("GLOSSARY_CONFIG_PATH", str(Path(__file__).resolve().parent / "config.json"))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for key, value in json.load(fh).items():
                if not key.startswith("_"):
                    cfg[key] = value
    except FileNotFoundError:
        pass
    for env_name, key in _ENV_SCALARS.items():
        if os.environ.get(env_name):
            cfg[key] = os.environ[env_name]
    for env_name, key in _ENV_LISTS.items():
        if os.environ.get(env_name):
            cfg[key] = _csv(os.environ[env_name])
    for env_name, key in _ENV_ROLE_LISTS.items():
        if os.environ.get(env_name):
            cfg.setdefault("roles", {})[key] = _csv(os.environ[env_name])
    if os.environ.get("GLOSSARY_DEMO_MODE"):
        cfg.setdefault("roles", {})["demo_mode"] = os.environ["GLOSSARY_DEMO_MODE"].lower() in ("1", "true", "yes")
    return cfg


_CFG = _load()


def bt(identifier: str) -> str:
    """Backtick-quote one identifier part (handles hyphens, reserved words)."""
    return "`" + str(identifier).replace("`", "``") + "`"


def fq(catalog: str, schema: str, table: str) -> str:
    """Fully qualified, backtick-quoted catalog.schema.table."""
    return f"{bt(catalog)}.{bt(schema)}.{bt(table)}"


# --- Flat accessors -----------------------------------------------------------
APP_TITLE = _CFG["app_title"]
ENVIRONMENT = _CFG["environment"]
ALLOWED_CATALOGS = _CFG["allowed_catalogs"]
DEFAULT_CATALOG = _CFG["default_catalog"]
HIDDEN_CATALOGS = set(_CFG["hidden_catalogs"])
WORKFLOW_CATALOG = _CFG["workflow_catalog"]
WORKFLOW_SCHEMA = _CFG["workflow_schema"]
PROPOSALS_TABLE_NAME = _CFG["proposals_table_name"]
STEWARDS_TABLE_NAME = _CFG["stewards_table_name"]
PROPOSALS_TABLE = fq(WORKFLOW_CATALOG, WORKFLOW_SCHEMA, PROPOSALS_TABLE_NAME)
STEWARDS_TABLE = fq(WORKFLOW_CATALOG, WORKFLOW_SCHEMA, STEWARDS_TABLE_NAME)
AI_MODEL_ENDPOINT = _CFG["ai_model_endpoint"]
DOMAIN_CONTEXT = _CFG["domain_context"]
SENSITIVITY_VALUES = _CFG["sensitivity_values"]
UC_WRITEBACK = _CFG["uc_writeback"]
GOVERNANCE_LEADS = [e.lower() for e in _CFG["roles"].get("governance_leads", [])]
STEP2_APPROVERS = [e.lower() for e in _CFG["roles"].get("step2_approvers", [])]
DEMO_MODE = bool(_CFG["roles"].get("demo_mode", False))
