"""Pipeline Health — end-to-end orchestration status, one screen.

Snowsight has pieces of this (Task History, dbt Project run history, table
Lineage) but nothing that shows "did today's whole run work" in one place.
This pulls the three stages together: bronze landing -> dbt transform
(validate/publish into silver + staging/gold/marts, all one `dbt build`
now that PROCESS_BATCH is retired) -> what's actually live in each layer --
so a non-Snowflake person can tell at a glance whether the daily pipeline
is healthy without knowing what a Task or a dbt model is.

Every number here is read live (SELECT, no caching beyond the 5-minute
Streamlit cache) -- row counts per layer, BRONZE_BATCH_MANIFESTS as a
landing log (audit trail only, no status), and SNOWFLAKE.ACCOUNT_USAGE.
TASK_HISTORY for both task states.
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

TASK_STATE_KIND = {"SUCCEEDED": "good", "SCHEDULED": "warn", "EXECUTING": "warn",
                    "FAILED": "crit", "CANCELLED": "crit", "SKIPPED": "warn"}


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


@st.cache_data(ttl=300)
def load_pipeline_data():
    last_batch = rows("""
        SELECT BATCH_ID, SOURCE_SYSTEM, CAPTURED_AT
        FROM NERO_DB."00_BRONZE".BRONZE_BATCH_MANIFESTS
        ORDER BY CAPTURED_AT DESC LIMIT 1
    """)
    last_batch = last_batch[0] if last_batch else None

    bronze_count = sum(
        one(f'SELECT COUNT(*) AS N FROM NERO_DB."00_BRONZE".BRONZE_{ds}')["N"]
        for ds in ("STORES", "TRANSACTIONS", "LOYALTY_EVENTS", "LOYALTY_CUSTOMERS")
    )
    layers = [
        {"layer": "Raw landing (Bronze)", "object": "BRONZE_<DATASET> (4 tables)",
         "count": bronze_count},
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
        SELECT BATCH_ID, SOURCE_SYSTEM, DATASETS, CAPTURED_AT
        FROM NERO_DB."00_BRONZE".BRONZE_BATCH_MANIFESTS
        ORDER BY CAPTURED_AT DESC LIMIT 20
    """)
    recent_batches = [
        {"batch_id": r["BATCH_ID"], "source_system": r["SOURCE_SYSTEM"],
         "datasets": json.loads(r["DATASETS"]) if isinstance(r["DATASETS"], str) else r["DATASETS"],
         "captured_at": r["CAPTURED_AT"].isoformat()}
        for r in batch_rows
    ]

    task_rows = rows("""
        SELECT NAME, STATE, SCHEDULED_TIME, QUERY_START_TIME, COMPLETED_TIME, ERROR_MESSAGE
        FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY
        WHERE NAME IN ('SYNTHETIC_DAILY_INGEST_TASK', 'GOOGLE_SHEETS_INGEST_TASK', 'DBT_DAILY_REFRESH_TASK')
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
    latest_ingest_task = next((t for t in task_runs if t["name"] in
                               ("SYNTHETIC_DAILY_INGEST_TASK", "GOOGLE_SHEETS_INGEST_TASK")), None)
    latest_dbt_task = next((t for t in task_runs if t["name"] == "DBT_DAILY_REFRESH_TASK"), None)

    # 3-stage flow now, not 4 -- PROCESS_BATCH's old "validate & publish"
    # step is folded into dbt build (analytics/models/silver/), so there's
    # no separate stage for it anymore. Falls back to the landing log (no
    # ACCOUNT_USAGE latency) when TASK_HISTORY hasn't caught up yet, since
    # a batch actually landing is stronger evidence than an empty
    # latency-lagged view.
    stage_generate = {
        "name": "1. Generate & land", "detail": f"Bronze landing (3 adapters) · {INGEST_SCHEDULE}",
    }
    if latest_ingest_task:
        stage_generate["kind"] = latest_ingest_task["state_kind"]
        stage_generate["status_text"] = latest_ingest_task["state"]
        stage_generate["when"] = latest_ingest_task["completed_at"] or latest_ingest_task["scheduled_at"]
    elif last_batch:
        stage_generate["kind"] = "good"
        stage_generate["status_text"] = "ran (task history not caught up yet)"
        stage_generate["when"] = last_batch["CAPTURED_AT"].isoformat()
    else:
        stage_generate["kind"] = "unknown"
        stage_generate["status_text"] = "no run yet"
        stage_generate["when"] = None

    stage_transform = {"name": "2. Validate, publish & transform",
                        "detail": f"dbt build: silver -> staging -> gold/marts · {DBT_REFRESH_SCHEDULE}"}
    if latest_dbt_task:
        stage_transform["kind"] = latest_dbt_task["state_kind"]
        stage_transform["status_text"] = latest_dbt_task["state"]
        stage_transform["when"] = latest_dbt_task["completed_at"] or latest_dbt_task["scheduled_at"]
    else:
        stage_transform["kind"] = "unknown"
        stage_transform["status_text"] = "no run recorded yet (account_usage lags new tasks)"
        stage_transform["when"] = None

    stage_serve = {
        "name": "3. Serve", "detail": "Dashboards & chatbots read gold/marts live",
        "kind": "good", "status_text": "always live", "when": None,
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_batch_id": last_batch["BATCH_ID"] if last_batch else None,
        "last_batch_captured_at": last_batch["CAPTURED_AT"].isoformat() if last_batch else None,
        "layers": layers,
        "recent_batches": recent_batches,
        "task_runs": task_runs,
        "stages": [stage_generate, stage_transform, stage_serve],
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
    batch_rows_ = [[b["batch_id"], b["source_system"], b["captured_at"]] for b in d["recent_batches"]] or \
        [["—", "No batches landed yet", ""]]
    task_rows_ = [[t["name"], t["state"], t["scheduled_at"] or "", t["completed_at"] or ""] for t in d["task_runs"]] or \
        [["—", "No task runs recorded yet (account_usage lags new tasks up to ~2h)", "", ""]]

    sections = [
        ("Executive Summary", [
            rc.body(f"Last batch landed: <b>{d['last_batch_id']}</b> at {d['last_batch_captured_at']}."),
            rc.caption("Sources: NERO_DB.\"00_BRONZE\".BRONZE_BATCH_MANIFESTS, SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY."),
        ]),
        ("1. Pipeline Stages", [
            rc.styled_table(["Stage", "Last Status", "What It Does"], stage_rows, col_widths=[110, 130, 220]),
        ]),
        ("2. Data Volume by Layer", [
            rc.styled_table(["Layer", "Object", "Row Count"], layer_rows, col_widths=[130, 200, 100]),
        ]),
        ("3. Recent Batches Landed", [
            rc.styled_table(["Batch ID", "Source", "Captured At"], batch_rows_, col_widths=[180, 100, 160]),
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
        "Recent Batches": pd.DataFrame(d["recent_batches"] or [{"batch_id": "", "source_system": "", "captured_at": ""}])
            [["batch_id", "source_system", "captured_at"]]
            .rename(columns={"batch_id": "Batch ID", "source_system": "Source", "captured_at": "Captured At"}),
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
