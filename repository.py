"""Data access layer: cross-catalog metadata browsing, coverage, the two-step
proposal workflow, Unity Catalog write-back, and the stewardship model.

The app governs whatever the service principal can see. Current definitions are
read live from each catalog's ``information_schema`` (column COMMENT = definition,
column TAG = sensitivity). The only app-owned state is the proposals and stewards
tables, which live in the configured workflow catalog/schema.
"""

import json
import uuid
from datetime import datetime, timezone

import config
from db import cache_clear, cache_get, cache_set, execute, logger, sql_literal

SCHEMA_MARKER = "__SCHEMA__"
TABLE_MARKER = "__TABLE__"


def _ic(catalog: str, view: str) -> str:
    return f"{config.bt(catalog)}.information_schema.{view}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_col(rows: list) -> list:
    return [list(r.values())[0] for r in rows if r]


# --- Discovery: catalog -> schema -> table -----------------------------------
def list_catalogs() -> list:
    cached = cache_get("catalogs")
    if cached is not None:
        return cached
    try:
        names = _first_col(execute("SHOW CATALOGS"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("SHOW CATALOGS failed: %s", exc)
        names = []
    allow = set(config.ALLOWED_CATALOGS)
    result = sorted(
        n for n in names
        if n not in config.HIDDEN_CATALOGS and (not allow or n in allow)
    )
    cache_set("catalogs", result)
    return result


def list_schemas(catalog: str) -> list:
    if not catalog:
        return []
    key = f"schemas_{catalog}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    exclude = {"information_schema"}
    if catalog == config.WORKFLOW_CATALOG:
        exclude.add(config.WORKFLOW_SCHEMA)  # app plumbing, not something to govern
    try:
        rows = execute(f"SELECT schema_name FROM {_ic(catalog, 'schemata')} ORDER BY schema_name")
        result = [r["schema_name"] for r in rows if r["schema_name"] not in exclude]
    except Exception as exc:  # noqa: BLE001
        logger.warning("list schemas failed for %s: %s", catalog, exc)
        result = []
    cache_set(key, result)
    return result


def list_tables(catalog: str, schema: str) -> list:
    if not (catalog and schema):
        return []
    key = f"tables_{catalog}_{schema}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    try:
        rows = execute(
            f"SELECT table_name, table_type FROM {_ic(catalog, 'tables')} "
            f"WHERE table_schema = {sql_literal(schema)} AND left(table_name, 2) <> '__' "
            f"ORDER BY table_name"
        )
        result = [{"table": r["table_name"], "type": r["table_type"]} for r in rows]
        if catalog == config.WORKFLOW_CATALOG and schema == config.WORKFLOW_SCHEMA:
            app_tables = {config.PROPOSALS_TABLE_NAME, config.STEWARDS_TABLE_NAME}
            result = [t for t in result if t["table"] not in app_tables]
    except Exception as exc:  # noqa: BLE001
        logger.warning("list tables failed for %s.%s: %s", catalog, schema, exc)
        result = []
    cache_set(key, result)
    return result


def get_columns(catalog: str, schema: str, table: str) -> list:
    key = f"cols_{catalog}_{schema}_{table}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    cols = execute(
        f"SELECT column_name, data_type, comment FROM {_ic(catalog, 'columns')} "
        f"WHERE table_schema = {sql_literal(schema)} AND table_name = {sql_literal(table)} "
        f"ORDER BY ordinal_position"
    )
    tags = {}
    try:
        for r in execute(
            f"SELECT column_name, tag_name, tag_value FROM {_ic(catalog, 'column_tags')} "
            f"WHERE schema_name = {sql_literal(schema)} AND table_name = {sql_literal(table)}"
        ):
            tags.setdefault(r["column_name"], {})[r["tag_name"]] = r["tag_value"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("column_tags read failed for %s.%s.%s: %s", catalog, schema, table, exc)
    for c in cols:
        c["tags"] = tags.get(c["column_name"], {})
        c["sensitivity"] = c["tags"].get("sensitivity", "")
    cache_set(key, cols)
    return cols


def get_coverage(catalog: str, schema: str) -> list:
    tables = list_tables(catalog, schema)
    if not tables:
        return []
    key = f"cov_{catalog}_{schema}"
    cached = cache_get(key)
    if cached is not None:
        return cached
    unions = []
    for t in tables:
        unions.append(
            f"SELECT {sql_literal(t['table'])} AS table_name, COUNT(*) AS total, "
            f"COUNT(CASE WHEN comment IS NOT NULL AND comment != '' THEN 1 END) AS documented "
            f"FROM {_ic(catalog, 'columns')} WHERE table_schema = {sql_literal(schema)} "
            f"AND table_name = {sql_literal(t['table'])}"
        )
    rows = execute(" UNION ALL ".join(unions))
    coverage = []
    for r in rows:
        total = int(r["total"] or 0)
        documented = int(r["documented"] or 0)
        coverage.append(
            {
                "table": r["table_name"],
                "total": total,
                "documented": documented,
                "percentage": round(documented / total * 100, 1) if total else 0.0,
            }
        )
    coverage.sort(key=lambda c: c["table"])
    cache_set(key, coverage)
    return coverage


def get_upstream_sources(catalog: str, schema: str, table: str) -> list:
    """Best-effort upstream tables via system lineage (needs system.access)."""
    sql = (
        "SELECT DISTINCT source_table_full_name FROM system.access.table_lineage "
        f"WHERE target_table_catalog = {sql_literal(catalog)} "
        f"AND target_table_schema = {sql_literal(schema)} AND target_table_name = {sql_literal(table)} "
        "AND source_table_full_name IS NOT NULL LIMIT 25"
    )
    try:
        return [r["source_table_full_name"] for r in execute(sql)]
    except Exception as exc:  # noqa: BLE001
        logger.warning("table_lineage unavailable: %s", exc)
        return []


# --- Proposal workflow -------------------------------------------------------
_PROPOSAL_COLS = (
    "id, catalog_name, schema_name, table_name, column_name, current_comment, proposed_comment, "
    "proposed_tags, proposer, status, reviewer, review_note, created_at, reviewed_at, "
    "step1_reviewer, step1_reviewed_at"
)


def list_proposals(status=None, proposer=None) -> list:
    sql = f"SELECT * FROM {config.PROPOSALS_TABLE}"
    clauses = []
    if status:
        clauses.append(f"status = {sql_literal(status)}")
    if proposer:
        clauses.append(f"proposer = {sql_literal(proposer)}")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC"
    try:
        return execute(sql)
    except Exception as exc:  # noqa: BLE001
        logger.warning("proposals query failed (run sql/01_setup.sql?): %s", exc)
        return []


def get_proposal(proposal_id: str):
    rows = execute(f"SELECT * FROM {config.PROPOSALS_TABLE} WHERE id = {sql_literal(proposal_id)}")
    return rows[0] if rows else None


def create_proposal(catalog, schema, table, column, current, proposed, sensitivity, synonyms, reason, proposer) -> str:
    proposal_id = str(uuid.uuid4())
    tags = json.dumps({"sensitivity": sensitivity or "", "synonyms": synonyms or "", "reason": reason or ""})
    vals = ", ".join(
        [
            sql_literal(proposal_id), sql_literal(catalog), sql_literal(schema), sql_literal(table),
            sql_literal(column), sql_literal(current or ""), sql_literal(proposed), sql_literal(tags),
            sql_literal(proposer), sql_literal("pending"), "NULL", "NULL", sql_literal(_now()),
            "NULL", "NULL", "NULL",
        ]
    )
    execute(f"INSERT INTO {config.PROPOSALS_TABLE} ({_PROPOSAL_COLS}) VALUES ({vals})", fetch=False)
    cache_clear("proposals")
    return proposal_id


def step1_approve(proposal_id, reviewer, edited_definition=None):
    sets = ["status = 'step1_approved'", f"step1_reviewer = {sql_literal(reviewer)}", f"step1_reviewed_at = {sql_literal(_now())}"]
    if edited_definition is not None:
        sets.append(f"proposed_comment = {sql_literal(edited_definition)}")
    execute(f"UPDATE {config.PROPOSALS_TABLE} SET {', '.join(sets)} WHERE id = {sql_literal(proposal_id)}", fetch=False)
    cache_clear("proposals")


def step2_approve(proposal_id, reviewer):
    proposal = get_proposal(proposal_id)
    if not proposal:
        raise RuntimeError("Proposal not found")
    result = writeback_to_uc(proposal)
    execute(
        f"UPDATE {config.PROPOSALS_TABLE} SET status = 'approved', reviewer = {sql_literal(reviewer)}, "
        f"reviewed_at = {sql_literal(_now())} WHERE id = {sql_literal(proposal_id)}",
        fetch=False,
    )
    cache_clear()  # definitions + coverage changed
    return result


def return_proposal(proposal_id, reviewer, note, step):
    column = "step1_reviewer" if step == 1 else "reviewer"
    execute(
        f"UPDATE {config.PROPOSALS_TABLE} SET status = 'returned', {column} = {sql_literal(reviewer)}, "
        f"review_note = {sql_literal(note or '')}, reviewed_at = {sql_literal(_now())} "
        f"WHERE id = {sql_literal(proposal_id)}",
        fetch=False,
    )
    cache_clear("proposals")


def writeback_to_uc(proposal: dict) -> dict:
    """Apply an approved proposal to Unity Catalog: column COMMENT + sensitivity TAG."""
    if not config.UC_WRITEBACK.get("enabled"):
        return {"applied": False, "reason": "uc_writeback disabled"}
    catalog = proposal.get("catalog_name")
    schema = proposal.get("schema_name")
    table = proposal.get("table_name")
    column = proposal.get("column_name")
    definition = (proposal.get("proposed_comment") or "").strip()
    try:
        tags = json.loads(proposal.get("proposed_tags") or "{}")
    except (TypeError, ValueError):
        tags = {}
    target = config.fq(catalog, schema, table)
    applied = []
    try:
        if column == SCHEMA_MARKER:
            execute(f"COMMENT ON SCHEMA {config.bt(catalog)}.{config.bt(schema)} IS {sql_literal(definition)}", fetch=False)
            applied.append("schema comment")
        elif column == TABLE_MARKER:
            execute(f"COMMENT ON TABLE {target} IS {sql_literal(definition)}", fetch=False)
            applied.append("table comment")
        else:
            if config.UC_WRITEBACK.get("write_comment", True) and definition:
                execute(f"ALTER TABLE {target} ALTER COLUMN {config.bt(column)} COMMENT {sql_literal(definition)}", fetch=False)
                applied.append("column comment")
            sensitivity = tags.get("sensitivity")
            if config.UC_WRITEBACK.get("write_sensitivity_tag", True) and sensitivity:
                execute(
                    f"ALTER TABLE {target} ALTER COLUMN {config.bt(column)} SET TAGS ('sensitivity' = {sql_literal(sensitivity)})",
                    fetch=False,
                )
                applied.append("sensitivity tag")
        return {"applied": True, "writes": applied}
    except Exception as exc:  # noqa: BLE001 - approval still records even if write-back can't run
        logger.warning("UC write-back skipped for %s: %s", target, exc)
        return {"applied": False, "reason": str(exc), "writes": applied}


# --- Stewardship model -------------------------------------------------------
def list_stewards() -> list:
    try:
        return execute(
            f"SELECT data_domain, data_steward_name, data_steward_email, data_owner_name, "
            f"data_owner_email, is_active FROM {config.STEWARDS_TABLE} ORDER BY data_domain"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stewards query failed (run sql/01_setup.sql?): %s", exc)
        return []


def upsert_steward(row: dict):
    domain = row.get("data_domain")
    if not domain:
        return
    existing = execute(f"SELECT data_domain FROM {config.STEWARDS_TABLE} WHERE data_domain = {sql_literal(domain)}")
    fields = {
        "data_steward_name": row.get("data_steward_name") or "",
        "data_steward_email": row.get("data_steward_email") or "",
        "data_owner_name": row.get("data_owner_name") or "",
        "data_owner_email": row.get("data_owner_email") or "",
    }
    if existing:
        sets = ", ".join(f"{k} = {sql_literal(v)}" for k, v in fields.items())
        sets += f", updated_at = {sql_literal(_now())}"
        execute(f"UPDATE {config.STEWARDS_TABLE} SET {sets} WHERE data_domain = {sql_literal(domain)}", fetch=False)
    else:
        execute(
            f"INSERT INTO {config.STEWARDS_TABLE} "
            f"(data_domain, data_steward_name, data_steward_email, data_owner_name, data_owner_email, is_active) "
            f"VALUES ({sql_literal(domain)}, {sql_literal(fields['data_steward_name'])}, "
            f"{sql_literal(fields['data_steward_email'])}, {sql_literal(fields['data_owner_name'])}, "
            f"{sql_literal(fields['data_owner_email'])}, TRUE)",
            fetch=False,
        )


def save_stewards(rows: list):
    for row in rows:
        upsert_steward(row)
    cache_clear("stewards")
