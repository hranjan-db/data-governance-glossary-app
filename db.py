"""Databricks SQL execution + a small in-memory TTL cache.

Uses the Databricks SDK Statement Execution API with the app service principal
credentials auto-detected by ``Config()``. The warehouse id comes from the
``DATABRICKS_WAREHOUSE_ID`` env var (injected via an app resource) and falls
back to auto-discovering a running warehouse — the same pattern the reference
app uses in production.
"""

import logging
import os
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("glossary")

_w = None
_WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")


def workspace_client() -> WorkspaceClient:
    """Lazily create the client so importing the app needs no credentials."""
    global _w
    if _w is None:
        _w = WorkspaceClient()
    return _w

_cache = {}
_CACHE_TTL = 60


def cache_get(key):
    if key in _cache:
        value, ts = _cache[key]
        if time.time() - ts < _CACHE_TTL:
            return value
    return None


def cache_set(key, value):
    _cache[key] = (value, time.time())


def cache_clear(prefix: str = ""):
    keys = [k for k in _cache if k.startswith(prefix)] if prefix else list(_cache.keys())
    for k in keys:
        _cache.pop(k, None)


def get_warehouse_id() -> str:
    global _WAREHOUSE_ID
    if _WAREHOUSE_ID:
        return _WAREHOUSE_ID
    warehouses = list(workspace_client().warehouses.list())
    for wh in warehouses:
        if wh.state and wh.state.value == "RUNNING":
            _WAREHOUSE_ID = wh.id
            break
    if not _WAREHOUSE_ID and warehouses:
        _WAREHOUSE_ID = warehouses[0].id
    return _WAREHOUSE_ID


def sql_literal(value) -> str:
    """Escape a Python value for safe inlining into a SQL string literal."""
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def execute(sql: str, fetch: bool = True):
    """Run a SQL statement. Returns a list of dict rows when ``fetch`` is True."""
    wh_id = get_warehouse_id()
    if not wh_id:
        raise RuntimeError("No SQL warehouse is available to this app.")
    logger.info("SQL: %s", sql[:200])
    resp = workspace_client().statement_execution.execute_statement(
        warehouse_id=wh_id, statement=sql, wait_timeout="50s"
    )
    if resp.status.state == StatementState.FAILED:
        msg = resp.status.error.message if resp.status.error else "unknown error"
        raise RuntimeError(f"SQL error: {msg}")
    if not fetch:
        return []
    if resp.manifest and resp.result and resp.result.data_array:
        columns = [c.name for c in resp.manifest.schema.columns]
        return [dict(zip(columns, row)) for row in resp.result.data_array]
    return []
