"""AI-suggested business definitions via the Databricks Foundation Model API.

Calls the serving endpoint directly through the SDK's REST client. We avoid
``serving_endpoints.query()`` (dict/object quirks in some SDK versions) and
``ai_query()`` (resolves model aliases to dated versions that may not exist).
"""

import logging

import config
from db import workspace_client

logger = logging.getLogger("glossary")

_PROMPT = """You are a data governance expert writing a business definition for a column in {context}.

Table: {table}
Column: {column}
Data type: {data_type}
Current definition: {current}

Write ONE concise business definition (1-2 sentences, 50 words max) that explains what the attribute
represents, its business meaning, and how it should be interpreted. It must be useful both to business
users and to AI agents reading Unity Catalog column comments. Return only the definition text."""


def suggest_definition(table_name: str, column_name: str, data_type: str, current_definition: str = "") -> dict:
    prompt = _PROMPT.format(
        context=config.DOMAIN_CONTEXT,
        table=table_name,
        column=column_name,
        data_type=data_type or "unknown",
        current=current_definition or "None",
    )
    try:
        resp = workspace_client().api_client.do(
            "POST",
            f"/serving-endpoints/{config.AI_MODEL_ENDPOINT}/invocations",
            body={
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 150,
                "temperature": 0.3,
            },
        )
        suggestion = resp["choices"][0]["message"]["content"].strip()
        return {"suggestion": suggestion}
    except Exception as exc:  # noqa: BLE001 - surface the error to the UI
        logger.error("AI suggestion failed: %s", exc)
        return {"suggestion": "", "error": str(exc)}
