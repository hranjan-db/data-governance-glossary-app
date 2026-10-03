"""Data Governance - Business Glossary.

A generic, Unity Catalog-native governance app on Databricks Apps. Users browse
any catalog -> schema -> table the app service principal can see, read current
column definitions + sensitivity, and propose changes. A two-step approval
(business accuracy -> data engineering) commits approved definitions back to
Unity Catalog as column COMMENTs and TAGs. Fully config-driven, customer-agnostic.
"""

import json
import os

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, callback, callback_context, dash_table, dcc, html, no_update
from flask import request as flask_request

import ai
import auth
import config
import repository

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.FONT_AWESOME],
    title=config.APP_TITLE,
    suppress_callback_exceptions=True,
    update_title=None,
)
server = app.server

TAB_LABELS = {
    "definitions": "Definitions",
    "update": "Propose Change",
    "review": "Review / Approve",
    "domains": "Domain Owners",
    "coverage": "Coverage & Metrics",
    "my_updates": "My Updates",
}
TAB_ORDER = ["definitions", "update", "review", "domains", "coverage", "my_updates"]

STATUS_BADGE = {
    "pending": ("Pending Step 1", "warning"),
    "step1_approved": ("Pending Step 2", "info"),
    "approved": ("Approved", "success"),
    "returned": ("Returned", "danger"),
}


# -------------------------------------------------------- shared helpers -----
def catalog_options():
    return [{"label": c, "value": c} for c in repository.list_catalogs()]


def schema_options(catalog):
    return [{"label": s, "value": s} for s in repository.list_schemas(catalog)]


def table_options(catalog, schema):
    return [{"label": t["table"], "value": t["table"]} for t in repository.list_tables(catalog, schema)]


def _catalog_schema_picker(prefix, with_table=False):
    cols = [
        dbc.Col([dbc.Label("Catalog"), dcc.Dropdown(id=f"{prefix}-catalog", options=catalog_options(), value=config.DEFAULT_CATALOG or None, placeholder="Select catalog")], md=4),
        dbc.Col([dbc.Label("Schema"), dcc.Dropdown(id=f"{prefix}-schema", placeholder="Select schema")], md=4),
    ]
    if with_table:
        cols.append(dbc.Col([dbc.Label("Table"), dcc.Dropdown(id=f"{prefix}-table", placeholder="Select table")], md=4))
    return dbc.Row(cols, className="mb-3")


# ------------------------------------------------------------------ layout ---
def serve_layout():
    try:
        email = auth.current_email(flask_request.headers)
    except Exception:  # noqa: BLE001 - no request context (startup validation)
        email = "anonymous@local"
    user = auth.resolve_user(email)
    visible_tabs = [dbc.Tab(label=TAB_LABELS[k], tab_id=k) for k in TAB_ORDER if user["tabs"].get(k)]

    navbar = dbc.Navbar(
        dbc.Container(
            [
                html.Span([html.I(className="fa-solid fa-book-bookmark me-2"), config.APP_TITLE], className="navbar-brand mb-0 h1"),
                html.Div(
                    [
                        dbc.Badge(config.ENVIRONMENT, color="secondary", className="me-2"),
                        dbc.Badge(user["role_label"], color="primary", className="me-2"),
                        html.Span(user["email"], className="text-muted small me-3"),
                        dbc.Button(html.I(className="fa-solid fa-circle-half-stroke"), id="theme-toggle", color="light", size="sm", outline=True),
                    ],
                    className="d-flex align-items-center",
                ),
            ],
            fluid=True,
            className="d-flex justify-content-between",
        ),
        color="dark",
        dark=True,
    )
    return html.Div(
        id="app-root",
        children=[
            dcc.Store(id="user", data=user),
            dcc.Store(id="theme", data="light", storage_type="local"),
            navbar,
            dbc.Container(
                [
                    dbc.Tabs(visible_tabs, id="tabs", active_tab="definitions", className="mt-3"),
                    html.Div(id="tab-content", className="mt-3"),
                ],
                fluid=True,
                className="pb-5",
            ),
        ],
    )


app.layout = serve_layout


# -------------------------------------------------------------- tab router ---
@callback(Output("tab-content", "children"), Input("tabs", "active_tab"), State("user", "data"))
def render_tab(active_tab, user):
    user = user or {}
    return {
        "definitions": render_definitions,
        "update": lambda: render_update(user),
        "review": lambda: render_review(user),
        "domains": lambda: render_domains(user),
        "coverage": render_coverage,
        "my_updates": lambda: render_my_updates(user),
    }.get(active_tab, lambda: html.Div())()


# ------------------------------------------------------------ definitions ----
def render_definitions():
    return html.Div(
        [
            html.H4("Business Definitions"),
            html.P("Pick a catalog, schema and table to review the current Unity Catalog definition and sensitivity for each column.", className="text-muted"),
            _catalog_schema_picker("def", with_table=True),
            dash_table.DataTable(
                id="def-grid",
                columns=[{"name": c, "id": c} for c in ["Column", "Type", "Definition", "Sensitivity"]],
                data=[],
                page_size=20,
                sort_action="native",
                filter_action="native",
                style_cell={"textAlign": "left", "padding": "8px", "whiteSpace": "normal", "height": "auto", "fontFamily": "inherit"},
                style_header={"fontWeight": "600"},
                style_data_conditional=[{"if": {"filter_query": '{Definition} = "— No definition —"'}, "color": "#c62828"}],
            ),
        ]
    )


@callback(Output("def-schema", "options"), Output("def-schema", "value"), Input("def-catalog", "value"))
def _def_schemas(catalog):
    return schema_options(catalog), None


@callback(Output("def-table", "options"), Output("def-table", "value"), Input("def-schema", "value"), State("def-catalog", "value"))
def _def_tables(schema, catalog):
    return (table_options(catalog, schema) if (catalog and schema) else []), None


@callback(Output("def-grid", "data"), Input("def-table", "value"), State("def-catalog", "value"), State("def-schema", "value"))
def _def_rows(table, catalog, schema):
    if not (catalog and schema and table):
        return []
    return [
        {
            "Column": c["column_name"],
            "Type": c["data_type"],
            "Definition": c.get("comment") or "— No definition —",
            "Sensitivity": c.get("sensitivity") or "",
        }
        for c in repository.get_columns(catalog, schema, table)
    ]


# ------------------------------------------------------------ propose tab ----
def render_update(user):
    if not user.get("can_propose"):
        return dbc.Alert("You do not have permission to propose changes.", color="secondary")
    return html.Div(
        [
            html.H4("Propose a Definition Change"),
            html.P("Pick a column, write (or AI-suggest) a definition and sensitivity, and submit it for review.", className="text-muted"),
            _catalog_schema_picker("up", with_table=True),
            dbc.Label("Column"),
            dcc.Dropdown(id="up-column", placeholder="Select a column", className="mb-3"),
            dbc.Label("Current Definition"),
            dbc.Textarea(id="up-current", disabled=True, className="mb-3"),
            dbc.Label(["Proposed Definition", html.Span(" *", className="text-danger")]),
            dbc.Textarea(id="up-proposed", placeholder="A clear, consistent description of what this attribute represents.", className="mb-1"),
            dbc.Button([html.I(className="fa-solid fa-wand-magic-sparkles me-1"), "Suggest with AI"], id="up-ai", color="link", size="sm", className="mb-3 p-0"),
            dbc.Row(
                [
                    dbc.Col([dbc.Label("Synonyms"), dbc.Input(id="up-synonyms", placeholder="comma-separated alternative terms / abbreviations")], md=6),
                    dbc.Col([dbc.Label(["Sensitivity", html.Span(" *", className="text-danger")]), dcc.Dropdown(id="up-sensitivity", options=[{"label": v, "value": v} for v in config.SENSITIVITY_VALUES], placeholder="Select")], md=6),
                ],
                className="mb-3",
            ),
            dbc.Label(["Reason for Change", html.Span(" *", className="text-danger")]),
            dbc.Textarea(id="up-reason", placeholder="Why is this change needed?", className="mb-3"),
            dbc.Button("Submit Proposal", id="up-submit", color="primary"),
            html.Div(id="up-alert", className="mt-3"),
        ]
    )


@callback(Output("up-schema", "options"), Output("up-schema", "value"), Input("up-catalog", "value"))
def _up_schemas(catalog):
    return schema_options(catalog), None


@callback(Output("up-table", "options"), Output("up-table", "value"), Input("up-schema", "value"), State("up-catalog", "value"))
def _up_tables(schema, catalog):
    return (table_options(catalog, schema) if (catalog and schema) else []), None


@callback(Output("up-column", "options"), Output("up-column", "value"), Input("up-table", "value"), State("up-catalog", "value"), State("up-schema", "value"))
def _up_columns(table, catalog, schema):
    if not (catalog and schema and table):
        return [], None
    opts = [{"label": f"{c['column_name']} ({c['data_type']})", "value": c["column_name"]} for c in repository.get_columns(catalog, schema, table)]
    return opts, None


@callback(Output("up-current", "value"), Input("up-column", "value"), State("up-catalog", "value"), State("up-schema", "value"), State("up-table", "value"))
def _fill_current(column, catalog, schema, table):
    if not (column and catalog and schema and table):
        return ""
    for c in repository.get_columns(catalog, schema, table):
        if c["column_name"] == column:
            return c.get("comment") or ""
    return ""


@callback(
    Output("up-proposed", "value"),
    Input("up-ai", "n_clicks"),
    State("up-catalog", "value"), State("up-schema", "value"), State("up-table", "value"),
    State("up-column", "value"), State("up-current", "value"),
    prevent_initial_call=True,
)
def _ai_suggest(_, catalog, schema, table, column, current):
    if not (catalog and schema and table and column):
        return no_update
    data_type = next((c["data_type"] for c in repository.get_columns(catalog, schema, table) if c["column_name"] == column), "")
    result = ai.suggest_definition(f"{schema}.{table}", column, data_type, current or "")
    return result.get("suggestion") or f"[AI unavailable: {result.get('error', 'unknown error')}]"


@callback(
    Output("up-alert", "children"),
    Input("up-submit", "n_clicks"),
    State("up-catalog", "value"), State("up-schema", "value"), State("up-table", "value"), State("up-column", "value"),
    State("up-current", "value"), State("up-proposed", "value"), State("up-synonyms", "value"),
    State("up-sensitivity", "value"), State("up-reason", "value"), State("user", "data"),
    prevent_initial_call=True,
)
def _submit_proposal(_, catalog, schema, table, column, current, proposed, synonyms, sensitivity, reason, user):
    if not (catalog and schema and table and column):
        return dbc.Alert("Select catalog, schema, table and column first.", color="warning")
    if not (proposed and sensitivity and reason):
        return dbc.Alert("Proposed Definition, Sensitivity and Reason for Change are required.", color="warning")
    try:
        pid = repository.create_proposal(catalog, schema, table, column, current, proposed, sensitivity, synonyms, reason, (user or {}).get("email", "anonymous"))
        return dbc.Alert(f"Proposal submitted (id {pid[:8]}). It now awaits Step 1 review.", color="success")
    except Exception as exc:  # noqa: BLE001
        return dbc.Alert(f"Submit failed: {exc}", color="danger")


# ------------------------------------------------------------ review tab -----
def _target_label(p):
    return f"{p.get('catalog_name', '?')}.{p['schema_name']}.{p['table_name']}.{p['column_name']}"


def _review_card(p, user):
    step = 1 if p.get("status") == "pending" else 2
    can_act = user.get("can_step1") if step == 1 else user.get("can_step2")
    pid = p["id"]
    try:
        tags = json.loads(p.get("proposed_tags") or "{}")
    except (TypeError, ValueError):
        tags = {}
    body = [
        html.Div([html.Strong(_target_label(p)), dbc.Badge(f"Step {step}", color="info", className="ms-2")]),
        html.Div([html.Span("Current: ", className="text-muted"), html.Span(p.get("current_comment") or "—")], className="small mt-2"),
        dbc.Label("Proposed Definition (editable)", className="mt-2"),
        dbc.Textarea(id={"type": "edit-def", "index": pid}, value=p.get("proposed_comment") or ""),
        html.Div(
            [
                html.Span(f"Sensitivity: {tags.get('sensitivity', '—')}", className="me-3 small"),
                html.Span(f"Synonyms: {tags.get('synonyms') or '—'}", className="me-3 small"),
                html.Span(f"Proposer: {p.get('proposer', '—')}", className="small text-muted"),
            ],
            className="mt-1",
        ),
        html.Div([html.Span("Reason: ", className="text-muted"), html.Span(tags.get("reason") or "—")], className="small mt-1"),
    ]
    if can_act:
        body += [
            dbc.Input(id={"type": "review-note", "index": pid}, placeholder="Review note (required to return)", size="sm", className="mt-2"),
            html.Div(
                [
                    dbc.Button("Approve", id={"type": "approve-btn", "index": pid}, color="success", size="sm", className="me-2"),
                    dbc.Button("Return with feedback", id={"type": "return-btn", "index": pid}, color="danger", size="sm", outline=True),
                ],
                className="mt-2",
            ),
        ]
    else:
        body.append(dbc.Alert("View only — you are not an approver for this step.", color="secondary", className="mt-2 mb-0 py-1"))
    return dbc.Card(dbc.CardBody(body), className="mb-3")


def render_review(user):
    if not user.get("is_approver"):
        return dbc.Alert("You are not an approver.", color="secondary")
    pending = repository.list_proposals(status="pending")
    step2 = repository.list_proposals(status="step1_approved")
    return html.Div(
        [
            html.H4("Review / Approve"),
            html.P("Step 1 reviews business accuracy; Step 2 confirms data-engineering fit before writing to Unity Catalog.", className="text-muted"),
            html.H6(f"Step 1 — Business accuracy ({len(pending)})", className="mt-3"),
            html.Div([_review_card(p, user) for p in pending] or [html.P("Nothing awaiting Step 1.", className="text-muted")]),
            html.Hr(),
            html.H6(f"Step 2 — Data engineering ({len(step2)})"),
            html.Div([_review_card(p, user) for p in step2] or [html.P("Nothing awaiting Step 2.", className="text-muted")]),
        ]
    )


@callback(
    Output("tab-content", "children", allow_duplicate=True),
    Input({"type": "approve-btn", "index": ALL}, "n_clicks"),
    Input({"type": "return-btn", "index": ALL}, "n_clicks"),
    State({"type": "edit-def", "index": ALL}, "value"),
    State({"type": "review-note", "index": ALL}, "value"),
    State({"type": "edit-def", "index": ALL}, "id"),
    State("user", "data"),
    prevent_initial_call=True,
)
def handle_review_action(approve_clicks, return_clicks, edit_values, notes, edit_ids, user):
    triggered = callback_context.triggered_id
    if not triggered or not isinstance(triggered, dict):
        return no_update
    if not any(c for c in (approve_clicks or []) + (return_clicks or [])):
        return no_update  # ALL inputs fire with None on render; wait for a real click
    pid = triggered["index"]
    index_map = {d["index"]: i for i, d in enumerate(edit_ids or [])}
    idx = index_map.get(pid)
    edited = edit_values[idx] if idx is not None and edit_values else None
    note = notes[idx] if idx is not None and notes else ""

    proposal = repository.get_proposal(pid)
    if not proposal:
        return render_review(user)
    step = 1 if proposal.get("status") == "pending" else 2
    reviewer = (user or {}).get("email", "anonymous")
    try:
        if triggered["type"] == "approve-btn":
            repository.step1_approve(pid, reviewer, edited) if step == 1 else repository.step2_approve(pid, reviewer)
        else:
            repository.return_proposal(pid, reviewer, note or "Returned for revision", step)
    except Exception as exc:  # noqa: BLE001
        return html.Div([dbc.Alert(f"Action failed: {exc}", color="danger"), render_review(user)])
    return render_review(user)


# ------------------------------------------------------------ domains tab ----
def render_domains(user):
    editable = user.get("can_edit_stewards")
    cols = [
        {"name": "Data Domain", "id": "data_domain", "editable": True},
        {"name": "Data Steward", "id": "data_steward_name", "editable": editable},
        {"name": "Steward Email", "id": "data_steward_email", "editable": editable},
        {"name": "Data Owner", "id": "data_owner_name", "editable": editable},
        {"name": "Owner Email", "id": "data_owner_email", "editable": editable},
    ]
    matrix = [
        ("Secret", "Highly restricted.", "Passwords, encryption keys, acquisition plans"),
        ("Confidential", "Limited audiences.", "Financial forecasts, employee data, customer contracts"),
        ("Internal Use Only", "Employees and trusted partners.", "Internal reports, operational metrics"),
        ("Public (Non-Sensitive)", "Approved for external sharing.", "Public website content, press releases"),
        ("Not Defined", "Classification not yet assigned.", "Newly created datasets pending review"),
    ]
    children = [
        html.H4("Domain Owners & Stewards"),
        html.P("Who owns and stewards each data domain." + (" Governance leads can edit and add rows." if editable else " Read-only."), className="text-muted"),
        dash_table.DataTable(
            id="stewards-table",
            columns=cols,
            data=repository.list_stewards(),
            editable=editable,
            row_deletable=editable,
            style_cell={"textAlign": "left", "padding": "8px", "whiteSpace": "normal", "height": "auto"},
            style_header={"fontWeight": "600"},
        ),
    ]
    if editable:
        children += [
            html.Div(
                [
                    dbc.Button("Add Row", id="stewards-add", color="secondary", size="sm", outline=True, className="mt-2 me-2"),
                    dbc.Button("Save Changes", id="stewards-save", color="primary", size="sm", className="mt-2"),
                ]
            ),
            html.Div(id="stewards-alert", className="mt-2"),
        ]
    children += [
        html.Hr(className="mt-4"),
        html.H5("Sensitivity Reference"),
        dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in ["Level", "Meaning", "Examples"]],
            data=[{"Level": lvl, "Meaning": m, "Examples": ex} for lvl, m, ex in matrix],
            style_cell={"textAlign": "left", "padding": "8px", "whiteSpace": "normal", "height": "auto"},
            style_header={"fontWeight": "600"},
        ),
    ]
    return html.Div(children)


@callback(
    Output("stewards-table", "data"),
    Input("stewards-add", "n_clicks"),
    State("stewards-table", "data"),
    prevent_initial_call=True,
)
def _add_steward_row(_, data):
    data = list(data or [])
    data.append({"data_domain": "", "data_steward_name": "", "data_steward_email": "", "data_owner_name": "", "data_owner_email": ""})
    return data


@callback(Output("stewards-alert", "children"), Input("stewards-save", "n_clicks"), State("stewards-table", "data"), State("user", "data"), prevent_initial_call=True)
def _save_stewards(_, data, user):
    if not (user or {}).get("can_edit_stewards"):
        return dbc.Alert("Not authorized.", color="danger")
    try:
        repository.save_stewards(data or [])
        return dbc.Alert("Stewardship model saved.", color="success")
    except Exception as exc:  # noqa: BLE001
        return dbc.Alert(f"Save failed: {exc}", color="danger")


# ---------------------------------------------------- coverage & metrics -----
def render_coverage():
    return html.Div(
        [
            html.H4("Coverage & Metrics"),
            html.P("Documentation coverage for a schema: how many columns carry a definition.", className="text-muted"),
            _catalog_schema_picker("cov"),
            dcc.Loading(html.Div(id="cov-content"), type="default"),
        ]
    )


@callback(Output("cov-schema", "options"), Output("cov-schema", "value"), Input("cov-catalog", "value"))
def _cov_schemas(catalog):
    return schema_options(catalog), None


@callback(Output("cov-content", "children"), Input("cov-schema", "value"), State("cov-catalog", "value"))
def _cov_render(schema, catalog):
    if not (catalog and schema):
        return html.P("Select a catalog and schema.", className="text-muted")
    coverage = repository.get_coverage(catalog, schema)
    if not coverage:
        return html.P("No tables found in this schema.", className="text-muted")
    total = sum(c["total"] for c in coverage)
    documented = sum(c["documented"] for c in coverage)
    missing = total - documented
    pct = round(documented / total * 100, 1) if total else 0

    donut = go.Figure(data=[go.Pie(labels=["Documented", "Missing"], values=[documented, missing], hole=0.55, marker={"colors": ["#2e7d32", "#c62828"]})])
    donut.update_layout(margin={"t": 10, "b": 10, "l": 10, "r": 10}, height=300)
    bar = go.Figure(
        data=[
            go.Bar(name="Documented", x=[c["table"] for c in coverage], y=[c["documented"] for c in coverage], marker_color="#2e7d32"),
            go.Bar(name="Missing", x=[c["table"] for c in coverage], y=[c["total"] - c["documented"] for c in coverage], marker_color="#c62828"),
        ]
    )
    bar.update_layout(barmode="stack", margin={"t": 10, "b": 90, "l": 10, "r": 10}, height=340)

    bars = [
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div([html.Strong(c["table"]), html.Span(f"  {c['documented']}/{c['total']}", className="text-muted small ms-2")]),
                    dbc.Progress(value=c["percentage"], label=f"{c['percentage']}%", color="success" if c["percentage"] >= 80 else "warning", className="mt-1"),
                ]
            ),
            className="mb-2",
        )
        for c in coverage
    ]
    return html.Div(
        [
            dbc.Row(
                [
                    dbc.Col(dbc.Card(dbc.CardBody([html.H2(f"{pct}%"), html.Div("Columns documented", className="text-muted")])), md=4),
                    dbc.Col(dbc.Card(dbc.CardBody([html.H2(str(total)), html.Div("Columns in schema", className="text-muted")])), md=4),
                    dbc.Col(dbc.Card(dbc.CardBody([html.H2(str(missing)), html.Div("Missing definitions", className="text-muted")])), md=4),
                ],
                className="mb-3",
            ),
            dbc.Row(
                [
                    dbc.Col([html.H6("Overall"), dcc.Graph(figure=donut, config={"displayModeBar": False})], md=4),
                    dbc.Col([html.H6("By table"), dcc.Graph(figure=bar, config={"displayModeBar": False})], md=8),
                ],
                className="mb-3",
            ),
            html.H6("Per-table coverage"),
            *bars,
        ]
    )


# --------------------------------------------------------- my updates tab ----
def _status_tracker(p):
    status = p.get("status", "pending")
    label, color = STATUS_BADGE.get(status, ("Unknown", "secondary"))
    step1_state = "success" if status in ("step1_approved", "approved") else ("danger" if status == "returned" else "warning")
    step2_state = "success" if status == "approved" else ("danger" if status == "returned" else ("warning" if status == "step1_approved" else "secondary"))
    footer = {
        "pending": "Next: Step 1 — business accuracy review",
        "step1_approved": f"Step 1 approved by {p.get('step1_reviewer') or '—'}. Next: Step 2 — data engineering.",
        "approved": f"Fully approved by {p.get('reviewer') or '—'} — written back to Unity Catalog.",
        "returned": f"Returned: {p.get('review_note') or 'see reviewer'}",
    }.get(status, "")
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div([html.Strong(_target_label(p)), dbc.Badge(label, color=color, className="ms-2")]),
                html.Div(
                    [dbc.Badge("Step 1", color=step1_state, className="me-1"), html.Span("———", className="text-muted mx-1"), dbc.Badge("Step 2", color=step2_state, className="ms-1")],
                    className="mt-2",
                ),
                html.Div(footer, className="small text-muted mt-2"),
            ]
        ),
        className="mb-2",
    )


def render_my_updates(user):
    lead_view = user.get("can_edit_stewards")
    proposals = repository.list_proposals() if lead_view else repository.list_proposals(proposer=user.get("email"))
    title = "All Definition Updates" if lead_view else "My Definition Updates"
    cards = [_status_tracker(p) for p in proposals] or [html.P("No proposals yet.", className="text-muted")]
    return html.Div([html.H4(title), html.P("Track each proposal through the two-step approval.", className="text-muted"), *cards])


# ---------------------------------------------------------------- theme ------
app.clientside_callback(
    "function(n, current) { return (current === 'dark') ? 'light' : 'dark'; }",
    Output("theme", "data"),
    Input("theme-toggle", "n_clicks"),
    State("theme", "data"),
    prevent_initial_call=True,
)
app.clientside_callback(
    """
    function(theme) {
        document.documentElement.setAttribute('data-bs-theme', theme || 'light');
        return (theme === 'dark') ? 'Switch to light mode' : 'Switch to dark mode';
    }
    """,
    Output("theme-toggle", "title"),
    Input("theme", "data"),
)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("DATABRICKS_APP_PORT", 8000)), debug=False)
