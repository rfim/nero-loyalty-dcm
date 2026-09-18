"""Pipeline Health — end-to-end orchestration status, one screen.

Snowsight has pieces of this (Task History, dbt Project run history, table
Lineage) but nothing that shows "did today's whole run work" in one place.
This pulls the four stages together: synthetic data generation ->
validation/publish (PROCESS_BATCH) -> dbt transform -> what's actually
live in each layer -- so a non-Snowflake person can tell at a glance
whether the daily pipeline is healthy without knowing what a Task or a
dbt model is.

Every number here is read live (SELECT, no caching beyond the 5-minute
Streamlit cache) from the same control/audit tables the pipeline itself
already writes to -- account_setup/synthetic_daily_ingest.sql,
sources/definitions/ingestion/engine.sql.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from snowflake.snowpark.context import get_active_session

import sys as _sys
from pathlib import Path as _Path
_p = _Path(__file__).resolve().parent
while not (_p / "shared").is_dir() and _p != _p.parent:
    _p = _p.parent
_sys.path.insert(0, str(_p / "shared"))

import branding
import report_common as rc

st.set_page_config(page_title="Pipeline Health", layout="wide")

session = get_active_session()

# Statically known -- the schedule is configuration, not something worth a
# grant just to read back (SHOW TASKS needs MONITOR on each task).
INGEST_SCHEDULE = "06:00 UTC daily"
DBT_REFRESH_SCHEDULE = "06:15 UTC daily"

BATCH_STATUS_KIND = {
    "PUBLISHED": "good", "ALREADY_PUBLISHED": "good",
    "INCOMPLETE": "warn", "SUPERSEDED": "warn",
    "REJECTED": "crit", "ERROR": "crit",
}
TASK_STATE_KIND = {"SUCCEEDED": "good", "SCHEDULED": "warn", "EXECUTING": "warn",
                    "FAILED": "crit", "CANCELLED": "crit", "SKIPPED": "warn"}


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


@st.cache_data(ttl=300)
def load_pipeline_data():
    pointer = one('SELECT CURRENT_BATCH_ID, CURRENT_RELEASE_AT FROM NERO_DB."02_CONTROL".CONTROL_RELEASE_POINTER')

    layers = [
        {"layer": "Raw landing (Bronze)", "object": "RAW_ENVELOPES",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."00_BRONZE".RAW_ENVELOPES')["N"]},
        {"layer": "Validated (Silver)", "object": "VALIDATED_STORES",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_SILVER".VALIDATED_STORES')["N"]},
        {"layer": "Validated (Silver)", "object": "VALIDATED_LOYALTY_CUSTOMERS",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_SILVER".VALIDATED_LOYALTY_CUSTOMERS')["N"]},
        {"layer": "Validated (Silver)", "object": "VALIDATED_TRANSACTIONS",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_SILVER".VALIDATED_TRANSACTIONS')["N"]},
        {"layer": "Validated (Silver)", "object": "VALIDATED_LOYALTY_EVENTS",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_SILVER".VALIDATED_LOYALTY_EVENTS')["N"]},
        {"layer": "Transformed (Gold)", "object": "DIM_STORE",
         "count": one('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."02_GOLD".DIM_STORE')["N"]},
        {"layer": "Transformed (Gold)", "object": "DIM_CUSTOMER_SCD",
         "count": one('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."02_GOLD".DIM_CUSTOMER_SCD')["N"]},
        {"layer": "Transformed (Gold)", "object": "FACT_SALES_TRANSACTION",
         "count": one('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."02_GOLD".FACT_SALES_TRANSACTION')["N"]},
        {"layer": "Transformed (Gold)", "object": "FACT_LOYALTY_EVENT",
         "count": one('SELECT COUNT(*) AS N FROM NERO_ANALYTICS."02_GOLD".FACT_LOYALTY_EVENT')["N"]},
    ]

    batch_rows = rows("""
        SELECT BATCH_ID, STATUS, DETAILS, RECORDED_AT
        FROM NERO_DB."02_CONTROL".CONTROL_RUN_AUDIT
        ORDER BY RECORDED_AT DESC LIMIT 20
    """)
    recent_batches = [
        {"batch_id": r["BATCH_ID"], "status": r["STATUS"],
         "status_kind": BATCH_STATUS_KIND.get(r["STATUS"], "warn"),
         "details": json.loads(r["DETAILS"]) if isinstance(r["DETAILS"], str) else r["DETAILS"],
         "recorded_at": r["RECORDED_AT"].isoformat()}
        for r in batch_rows
    ]
    latest_synthetic = next((b for b in recent_batches if b["batch_id"].startswith("synthetic_")), None)

    task_rows = rows("""
        SELECT NAME, STATE, SCHEDULED_TIME, QUERY_START_TIME, COMPLETED_TIME, ERROR_MESSAGE
        FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY
        WHERE NAME IN ('SYNTHETIC_DAILY_INGEST_TASK', 'DBT_DAILY_REFRESH_TASK')
        ORDER BY SCHEDULED_TIME DESC LIMIT 30
    """)
    task_runs = [
        {"name": r["NAME"], "state": r["STATE"], "state_kind": TASK_STATE_KIND.get(r["STATE"], "warn"),
         "scheduled_at": r["SCHEDULED_TIME"].isoformat() if r["SCHEDULED_TIME"] else None,
         "started_at": r["QUERY_START_TIME"].isoformat() if r["QUERY_START_TIME"] else None,
         "completed_at": r["COMPLETED_TIME"].isoformat() if r["COMPLETED_TIME"] else None,
         "error": r["ERROR_MESSAGE"]}
        for r in task_rows
    ]
    latest_ingest_task = next((t for t in task_runs if t["name"] == "SYNTHETIC_DAILY_INGEST_TASK"), None)
    latest_dbt_task = next((t for t in task_runs if t["name"] == "DBT_DAILY_REFRESH_TASK"), None)

    # The 4-stage flow banner -- falls back to the audit trail (no
    # ACCOUNT_USAGE latency) when TASK_HISTORY hasn't caught up yet, since
    # a batch actually publishing is stronger evidence than an empty
    # latency-lagged view.
    stage_generate = {
        "name": "1. Generate", "detail": f"Synthetic daily data · {INGEST_SCHEDULE}",
    }
    if latest_ingest_task:
        stage_generate["kind"] = latest_ingest_task["state_kind"]
        stage_generate["status_text"] = latest_ingest_task["state"]
        stage_generate["when"] = latest_ingest_task["completed_at"] or latest_ingest_task["scheduled_at"]
    elif latest_synthetic:
        stage_generate["kind"] = "good"
        stage_generate["status_text"] = "ran (task history not caught up yet)"
        stage_generate["when"] = latest_synthetic["recorded_at"]
    else:
        stage_generate["kind"] = "unknown"
        stage_generate["status_text"] = "no run yet"
        stage_generate["when"] = None

    stage_validate = {"name": "2. Validate & publish", "detail": "PROCESS_BATCH against the data contract"}
    if latest_synthetic:
        stage_validate["kind"] = latest_synthetic["status_kind"]
        stage_validate["status_text"] = latest_synthetic["status"]
        stage_validate["when"] = latest_synthetic["recorded_at"]
    else:
        stage_validate["kind"] = "unknown"
        stage_validate["status_text"] = "no batch yet"
        stage_validate["when"] = None

    stage_transform = {"name": "3. Transform", "detail": f"dbt snapshot + gold/marts · {DBT_REFRESH_SCHEDULE}"}
    if latest_dbt_task:
        stage_transform["kind"] = latest_dbt_task["state_kind"]
        stage_transform["status_text"] = latest_dbt_task["state"]
        stage_transform["when"] = latest_dbt_task["completed_at"] or latest_dbt_task["scheduled_at"]
    else:
        stage_transform["kind"] = "unknown"
        stage_transform["status_text"] = "no run recorded yet (account_usage lags new tasks)"
        stage_transform["when"] = None

    stage_serve = {
        "name": "4. Serve", "detail": "Dashboards & chatbots read gold/marts live",
        "kind": "good", "status_text": "always live", "when": None,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_batch_id": pointer["CURRENT_BATCH_ID"],
        "current_release_at": pointer["CURRENT_RELEASE_AT"].isoformat() if pointer["CURRENT_RELEASE_AT"] else None,
        "layers": layers,
        "recent_batches": recent_batches,
        "task_runs": task_runs,
        "stages": [stage_generate, stage_validate, stage_transform, stage_serve],
        "logo_b64": branding.LOGO_B64,
    }


data = load_pipeline_data()

template_path = Path(__file__).parent / "pipeline_health_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))
components.html(html, height=1900, scrolling=True)


# ---------------- exports ----------------

def build_pdf(d: dict) -> bytes:
    stage_rows = [[s["name"], s["status_text"], s["detail"]] for s in d["stages"]]
    layer_rows = [[l["layer"], l["object"], f"{l['count']:,}"] for l in d["layers"]]
    batch_rows_ = [[b["batch_id"], b["status"], b["recorded_at"]] for b in d["recent_batches"]] or \
        [["—", "No batches recorded yet", ""]]
    task_rows_ = [[t["name"], t["state"], t["scheduled_at"] or "", t["completed_at"] or ""] for t in d["task_runs"]] or \
        [["—", "No task runs recorded yet (account_usage lags new tasks up to ~2h)", "", ""]]

    sections = [
        ("Executive Summary", [
            rc.body(f"Currently live batch: <b>{d['current_batch_id']}</b>, published {d['current_release_at']}."),
            rc.caption("Sources: NERO_DB.\"02_CONTROL\".CONTROL_RUN_AUDIT / CONTROL_RELEASE_POINTER, SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY."),
        ]),
        ("1. Pipeline Stages", [
            rc.styled_table(["Stage", "Last Status", "What It Does"], stage_rows, col_widths=[110, 130, 220]),
        ]),
        ("2. Data Volume by Layer", [
            rc.styled_table(["Layer", "Object", "Row Count"], layer_rows, col_widths=[130, 200, 100]),
        ]),
        ("3. Recent Batch Publishes", [
            rc.styled_table(["Batch ID", "Status", "Recorded At"], batch_rows_, col_widths=[180, 100, 160]),
        ]),
        ("4. Recent Task Runs", [
            rc.styled_table(["Task", "State", "Scheduled", "Completed"], task_rows_, col_widths=[160, 90, 130, 130]),
        ]),
    ]
    return rc.build_pdf(
        report_title="Pipeline Health",
        report_subtitle="Nero Platform — end-to-end orchestration status",
        prepared_for="Platform Engineering",
        sections=sections,
    )


def build_excel(d: dict) -> bytes:
    sheets = {
        "Pipeline Stages": pd.DataFrame(d["stages"])[["name", "status_text", "when", "detail"]]
            .rename(columns={"name": "Stage", "status_text": "Status", "when": "When", "detail": "Detail"}),
        "Data Volume": pd.DataFrame(d["layers"])
            .rename(columns={"layer": "Layer", "object": "Object", "count": "Row Count"}),
        "Recent Batches": pd.DataFrame(d["recent_batches"] or [{"batch_id": "", "status": "", "recorded_at": ""}])
            [["batch_id", "status", "recorded_at"]]
            .rename(columns={"batch_id": "Batch ID", "status": "Status", "recorded_at": "Recorded At"}),
        "Recent Task Runs": pd.DataFrame(
            d["task_runs"] or [{"name": "", "state": "", "scheduled_at": "", "completed_at": ""}]
        )[["name", "state", "scheduled_at", "completed_at"]]
            .rename(columns={"name": "Task", "state": "State", "scheduled_at": "Scheduled", "completed_at": "Completed"}),
    }
    return rc.write_excel_workbook(
        title="Pipeline Health",
        subtitle="Nero Platform — end-to-end orchestration status",
        sheets=sheets,
    )


st.divider()
st.subheader("Export")
col1, col2 = st.columns(2)
today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
with col1:
    st.download_button(
        "Download PDF report", data=build_pdf(data),
        file_name=f"nero_pipeline_health_{today_str}.pdf", mime="application/pdf",
    )
with col2:
    st.download_button(
        "Download Excel workbook", data=build_excel(data),
        file_name=f"nero_pipeline_health_{today_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
