# Data Governance — Business Glossary

A generic, steward-facing **business glossary / metadata governance** app for Unity Catalog, built on **Databricks Apps** with **Dash**.

Users browse **any catalog → schema → table** the app's service principal can see, review the current column **definitions** and **sensitivity**, and propose changes. A **two-step approval** (business accuracy → data engineering) commits approved definitions back to Unity Catalog as column `COMMENT`s and `TAG`s — no external metadata store.

It is fully **config-driven and customer-agnostic**: deploy it, point it at a workspace, and it governs whatever UC the service principal can read. Nothing in the code is specific to any one organization.

---

## Why

- Stewards and business owners usually have **no workspace access** but need to author definitions. This app gives them a governed UI behind Databricks Apps SSO.
- Definitions live **in Unity Catalog** (column comments + tags), so they flow to Catalog Explorer, Genie, and downstream AI agents — one source of truth.
- A lightweight **propose → review → approve** workflow keeps authoring open while keeping writes controlled.
- Because browsing is **cross-catalog**, the same deployment serves many domains/estates — useful for federated data-governance setups.

---

## Architecture

```
 Browser (SSO: X-Forwarded-Email)
        │
        ▼
 Databricks App  ──  Dash (dash-bootstrap-components)
        │                 app.py        UI + callbacks (role-gated tabs, catalog/schema/table pickers)
        │                 repository.py metadata browse, proposal workflow, UC write-back, stewardship
        │                 auth.py       identity + role resolution
        │                 ai.py         AI-suggested definitions (Foundation Model API)
        │                 db.py         SQL execution + TTL cache
        ▼
 Unity Catalog (app service principal, via SQL warehouse)
   • <any catalog>.information_schema → live definitions (COMMENT) + sensitivity (TAG)
   • workflow catalog/schema:
       glossary_proposals → workflow + audit trail (app-owned)
       data_stewards      → domain → owner/steward (role resolution, app-owned)
```

---

## The 6 tabs (role-gated)

| Tab | Who sees it | Purpose |
| --- | --- | --- |
| Definitions | Everyone | Pick catalog + schema; browse every column with its current UC definition + sensitivity |
| Propose Change | Stewards / Owners / Leads | Pick catalog → schema → table → column; propose a definition (optionally AI-suggested), set sensitivity + reason |
| Review / Approve | Approvers | Step 1 (business accuracy) and Step 2 (data-engineering fit); edit, approve, or return |
| Domain Owners | Everyone (edit: Leads) | Stewardship model (add/edit rows) + a sensitivity reference matrix |
| Coverage & Metrics | Everyone | Pick catalog + schema; documentation coverage %, donut, per-table breakdown |
| My Updates | Proposers / Approvers | Two-step status tracker per proposal (leads see all) |

Roles resolve from the `data_stewards` table (owner/steward email) plus the `governance_leads` / `step2_approvers` lists in `config.json`. With `roles.demo_mode: true`, any authenticated user gets all roles so a single viewer can exercise the full workflow.

---

## Configuration (`config.json`)

| Key | Meaning |
| --- | --- |
| `allowed_catalogs` | Optional allowlist of catalogs users may browse. Empty = all the SP can see (minus `hidden_catalogs`) |
| `default_catalog` | Optional catalog pre-selected on load |
| `hidden_catalogs` | Catalogs to hide from the picker (e.g. `system`, `samples`) |
| `workflow_catalog` / `workflow_schema` | Where the app's own `glossary_proposals` + `data_stewards` tables live |
| `ai_model_endpoint` | Foundation Model serving endpoint for AI suggestions |
| `domain_context` | One phrase describing the data (used in the AI prompt), e.g. "an enterprise data warehouse" |
| `sensitivity_values` | The controlled sensitivity dropdown |
| `uc_writeback.enabled` | Write approved definitions back to UC (COMMENT + sensitivity TAG) |
| `roles` | `governance_leads`, `step2_approvers`, `demo_mode` |

Env overrides: `GLOSSARY_ENVIRONMENT`, `GLOSSARY_DEFAULT_CATALOG`, `AI_MODEL_ENDPOINT`, `GLOSSARY_CONFIG_PATH`.

---

## Deploy

```bash
# 0. One-time: fill placeholders in sql/01_setup.sql ({{WF_CATALOG}}, {{WF_SCHEMA}}, {{APP_SP}})
#    and run it. Optionally run 02_seed_demo.sql.

# 1. Edit config.json: workflow_catalog/schema, allowed_catalogs (optional), roles.

# 2. Create the app (once) and bind a SQL warehouse resource.
databricks apps create data-gov-glossary --profile <PROFILE>

# 3. Sync the source into the workspace and deploy.
databricks sync . /Workspace/Users/<you>/data-gov-glossary --profile <PROFILE>
databricks apps deploy data-gov-glossary \
  --source-code-path /Workspace/Users/<you>/data-gov-glossary --profile <PROFILE>
```

Service principal needs: `CAN USE` on a SQL warehouse, `CAN QUERY` on the model endpoint, `SELECT`/`MODIFY` on the workflow schema, and `SELECT` (+ `MODIFY` + `APPLY TAG` for write-back) on each governed catalog. See `sql/01_setup.sql`.

---

## UC write-back

On full (Step 2) approval, if `uc_writeback.enabled`, the app runs:

- `ALTER TABLE <t> ALTER COLUMN <c> COMMENT '<definition>'`
- `ALTER TABLE <t> ALTER COLUMN <c> SET TAGS ('sensitivity' = '<value>')`

Columns that don't resolve are skipped safely (logged, not fatal). Only `DROP TABLE` wipes comments/tags — ordinary upserts preserve them.

---

## Local notes

- Identity comes from the `X-Forwarded-Email` SSO header, present only when deployed. Locally it falls back to `anonymous@local` (demo mode shows all tabs; UC calls degrade gracefully to empty).
- Dash 2.18.x: never mix pattern-matching (`MATCH`/`ALL`) outputs with regular outputs in the same callback — it silently breaks the client renderer.
