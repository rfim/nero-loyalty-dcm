"""Platform Governance — combined Streamlit-in-Snowflake app, replacing the
three separate Pipeline Health, Security & Horizon Governance Report, and
(newly added) Data Quality dashboards with one app, three tabs. Same
pattern as streamlit_apps/dashboards/cost_reports/cost_reports.py's
Platform/Chatbot Cost consolidation: one place to land, same PDF/Excel
exports per tab.

Tab 1 (Pipeline Health): end-to-end orchestration status -- staging
landing -> dbt transform -> what's live in each layer. Updated for the
Staging/Bronze/Silver rename (see analytics/README.md) and PROCESS_BATCH's
retirement.

Tab 2 (Security & Governance): Trust Center findings, ACCOUNTADMIN
holders, role grants, login activity. Unchanged from the standalone
security_governance_report app -- reads NERO_GOVERNANCE.SECURITY, which
this migration never touched.

Tab 3 (Data Quality): NEW. dbt's own test results, actually persisted --
before this, `dbt build`'s 74 tests reported pass/fail only to whoever ran
the CLI. See analytics/macros/log_dbt_test_results.sql (the on-run-end
hook that logs every test) and account_setup/dbt_quality_log.sql (the
table + grants). No dashboard like this existed before; this is a real
gap being closed, not just a rename.
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

st.set_page_config(page_title="Platform Governance", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


# ==================================================================
# Tab 1: Pipeline Health
# ==================================================================

INGEST_SCHEDULE = "06:00 UTC daily"
DBT_REFRESH_SCHEDULE = "06:15 UTC daily"
TASK_STATE_KIND = {"SUCCEEDED": "good", "SCHEDULED": "warn", "EXECUTING": "warn",
                    "FAILED": "crit", "CANCELLED": "crit", "SKIPPED": "warn"}


@st.cache_data(ttl=300)
def load_pipeline_data():
    last_batch = rows("""
        SELECT BATCH_ID, SOURCE_SYSTEM, CAPTURED_AT
        FROM NERO_DB."00_STAGING".STAGING_BATCH_MANIFESTS
        ORDER BY CAPTURED_AT DESC LIMIT 1
    """)
    last_batch = last_batch[0] if last_batch else None

    staging_count = sum(
        one(f'SELECT COUNT(*) AS N FROM NERO_DB."00_STAGING".STAGING_{ds}')["N"]
        for ds in ("STORES", "TRANSACTIONS", "LOYALTY_EVENTS", "LOYALTY_CUSTOMERS")
    )
    layers = [
        {"layer": "Raw landing (Staging)", "object": "STAGING_<DATASET> (4 tables)",
         "count": staging_count},
        {"layer": "Contract-filtered (Bronze)", "object": "BRONZE_STORES",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_BRONZE".BRONZE_STORES')["N"]},
        {"layer": "Contract-filtered (Bronze)", "object": "BRONZE_LOYALTY_CUSTOMERS",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_CUSTOMERS')["N"]},
        {"layer": "Contract-filtered (Bronze)", "object": "BRONZE_TRANSACTIONS",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_BRONZE".BRONZE_TRANSACTIONS')["N"]},
        {"layer": "Contract-filtered (Bronze)", "object": "BRONZE_LOYALTY_EVENTS",
         "count": one('SELECT COUNT(*) AS N FROM NERO_DB."01_BRONZE".BRONZE_LOYALTY_EVENTS')["N"]},
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
        FROM NERO_DB."00_STAGING".STAGING_BATCH_MANIFESTS
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

    stage_generate = {
        "name": "1. Generate & land", "detail": f"Staging landing (3 adapters) · {INGEST_SCHEDULE}",
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
                        "detail": f"dbt build: bronze -> silver -> gold/marts · {DBT_REFRESH_SCHEDULE}"}
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


def build_pipeline_pdf(d: dict) -> bytes:
    stage_rows = [[s["name"], s["status_text"], s["detail"]] for s in d["stages"]]
    layer_rows = [[l["layer"], l["object"], f"{l['count']:,}"] for l in d["layers"]]
    batch_rows_ = [[b["batch_id"], b["source_system"], b["captured_at"]] for b in d["recent_batches"]] or \
        [["—", "No batches landed yet", ""]]
    task_rows_ = [[t["name"], t["state"], t["scheduled_at"] or "", t["completed_at"] or ""] for t in d["task_runs"]] or \
        [["—", "No task runs recorded yet (account_usage lags new tasks up to ~2h)", "", ""]]

    sections = [
        ("Executive Summary", [
            rc.body(f"Last batch landed: <b>{d['last_batch_id']}</b> at {d['last_batch_captured_at']}."),
            rc.caption("Sources: NERO_DB.\"00_STAGING\".STAGING_BATCH_MANIFESTS, SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY."),
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


def build_pipeline_excel(d: dict) -> bytes:
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


# ==================================================================
# Tab 2: Security & Governance
# ==================================================================

@st.cache_data(ttl=300)
def load_security_data():
    open_findings_rows = rows(f"SELECT * FROM {GOV}.SECURITY.OPEN_FINDINGS")
    open_findings = [
        {"scanner_name": r["SCANNER_NAME"], "severity": r["SEVERITY"],
         "risk_description": r["RISK_DESCRIPTION"], "created_on": str(r["CREATED_ON"])}
        for r in open_findings_rows
    ]
    open_by_severity = {}
    for f in open_findings:
        open_by_severity[f["severity"]] = open_by_severity.get(f["severity"], 0) + 1
    open_critical = open_by_severity.get("CRITICAL", 0) + open_by_severity.get("HIGH", 0)

    summary_rows = rows(f"SELECT * FROM {GOV}.SECURITY.FINDINGS_SUMMARY")
    hist = {}
    for r in summary_rows:
        sev = r["SEVERITY"]
        hist.setdefault(sev, {"severity": sev, "open": 0, "resolved": 0})
        hist[sev]["open" if r["STATE"] == "Open" else "resolved"] += r["FINDING_COUNT"]
    findings_history = list(hist.values())

    admin_rows = rows(f"SELECT * FROM {GOV}.SECURITY.ACCOUNTADMIN_HOLDERS")
    accountadmin_holders = [
        {"user_name": r["USER_NAME"], "granted_role": r["GRANTED_ROLE"], "created_on": str(r["CREATED_ON"])}
        for r in admin_rows
    ]

    failed = one(f"""
        SELECT COALESCE(SUM(FAILED_ATTEMPTS), 0) AS N, COUNT(DISTINCT USER_NAME) AS U
        FROM {GOV}.SECURITY.FAILED_LOGINS_DAILY
    """)

    login_rows = rows(f"SELECT * FROM {GOV}.SECURITY.LOGIN_ACTIVITY_DAILY ORDER BY LOGIN_DATE DESC")
    login_daily = [
        {"date": str(r["LOGIN_DATE"]), "success": int(r["SUCCESS_COUNT"]), "failed": int(r["FAILED_COUNT"])}
        for r in login_rows
    ]

    grants_rows = rows(f"""
        SELECT ROLE_NAME, PRIVILEGE, GRANTED_ON, COUNT(*) AS N
        FROM {GOV}.SECURITY.ROLE_GRANTS_INVENTORY
        GROUP BY ROLE_NAME, PRIVILEGE, GRANTED_ON
        ORDER BY ROLE_NAME, N DESC
    """)
    grants_by_role = [
        {"role": r["ROLE_NAME"], "privilege": r["PRIVILEGE"], "granted_on": r["GRANTED_ON"], "count": int(r["N"])}
        for r in grants_rows
    ]

    net_policy = session.sql("SHOW PARAMETERS LIKE 'NETWORK_POLICY' IN ACCOUNT").collect()
    network_policy_set = bool(net_policy and net_policy[0]["value"])

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_start": None,
        "period_end": None,
        "security": {
            "open_findings": open_findings,
            "open_count": len(open_findings),
            "open_by_severity": open_by_severity,
            "open_critical": open_critical,
            "findings_history": findings_history,
            "accountadmin_holders": accountadmin_holders,
            "accountadmin_count": len(accountadmin_holders),
            "failed_logins_30d": int(failed["N"]),
            "failed_login_users": int(failed["U"]),
            "login_daily": login_daily,
            "grants_by_role": grants_by_role,
            "network_policy_set": network_policy_set,
        },
    }
    if data["security"]["login_daily"]:
        data["period_start"] = min(r["date"] for r in data["security"]["login_daily"])
        data["period_end"] = max(r["date"] for r in data["security"]["login_daily"])
    return data


def build_security_pdf(d: dict) -> bytes:
    s = d["security"]
    finding_rows = [
        [f["scanner_name"], f["severity"], (f["risk_description"] or "").strip()[:180], f["created_on"][:10]]
        for f in s["open_findings"]
    ] or [["—", "—", "No open findings.", ""]]
    admin_rows = [[a["user_name"], a["granted_role"], a["created_on"][:10]] for a in s["accountadmin_holders"]]
    grants_rows = [[g["role"], g["privilege"], g["granted_on"], f"{g['count']:,}"] for g in s["grants_by_role"][:40]]
    login_rows = [[r["date"], f"{r['success']:,}", f"{r['failed']:,}"] for r in s["login_daily"]] or [["—", "—", "—"]]

    sections = [
        ("Executive Summary", [
            rc.body(
                f"<b>{s['open_count']}</b> open Trust Center finding(s), of which <b>{s['open_critical']}</b> "
                f"are critical or high severity. <b>{s['accountadmin_count']}</b> user(s) currently hold "
                f"ACCOUNTADMIN. Account-level network policy is "
                f"<b>{'configured' if s['network_policy_set'] else 'NOT configured'}</b>."
            ),
            rc.body(
                f"{s['failed_logins_30d']} failed login attempt(s) across {s['failed_login_users']} "
                f"distinct user(s) in the last 30 days."
            ),
            rc.caption("Sources: SNOWFLAKE.TRUST_CENTER.FINDINGS, SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY / GRANTS_TO_ROLES / GRANTS_TO_USERS."),
        ]),
        ("1. Open Horizon / Trust Center Findings", [
            rc.styled_table(["Scanner", "Severity", "Risk", "Opened"], finding_rows,
                             col_widths=[110, 55, 260, 55]),
        ]),
        ("2. Least-Privilege Posture", [
            rc.styled_table(["User", "Role", "Granted"], admin_rows or [["—", "None", ""]], col_widths=[150, 150, 90]),
            rc.caption(
                "Single-identity risk: this user runs ingestion, CI/CD and dbt under ACCOUNTADMIN. "
                "Splitting into per-workload least-privilege service roles is the standing recommendation."
                if len(admin_rows) <= 1 else ""
            ),
        ]),
        ("3. Role Grant Inventory (top 40 by count)", [
            rc.styled_table(["Role", "Privilege", "Object Type", "Count"], grants_rows, col_widths=[110, 110, 110, 60]),
        ]),
        ("4. Login Activity, Last 30 Days", [
            rc.styled_table(["Date", "Successful", "Failed"], login_rows, col_widths=[110, 110, 110]),
        ]),
    ]
    return rc.build_pdf(
        report_title="Security & Horizon Governance Report",
        report_subtitle="Nero Platform — Trust Center findings, grants and login posture",
        prepared_for="Platform Engineering",
        sections=sections,
    )


def build_security_excel(d: dict) -> bytes:
    s = d["security"]
    sheets = {
        "Open Findings": pd.DataFrame(s["open_findings"] or [{"scanner_name": "", "severity": "", "risk_description": "", "created_on": ""}])
            .rename(columns={"scanner_name": "Scanner", "severity": "Severity", "risk_description": "Risk", "created_on": "Opened"}),
        "ACCOUNTADMIN Holders": pd.DataFrame(s["accountadmin_holders"] or [{"user_name": "", "granted_role": "", "created_on": ""}])
            .rename(columns={"user_name": "User", "granted_role": "Role", "created_on": "Granted"}),
        "Role Grants": pd.DataFrame(s["grants_by_role"] or [{"role": "", "privilege": "", "granted_on": "", "count": 0}])
            .rename(columns={"role": "Role", "privilege": "Privilege", "granted_on": "Object Type", "count": "Count"}),
        "Login Activity": pd.DataFrame(s["login_daily"] or [{"date": "", "success": 0, "failed": 0}])
            .rename(columns={"date": "Date", "success": "Successful", "failed": "Failed"}),
    }
    return rc.write_excel_workbook(
        title="Security & Horizon Governance Report",
        subtitle="Nero Platform — Trust Center findings, grants and login posture",
        sheets=sheets,
    )


# ==================================================================
# Tab 3: Data Quality (new)
# ==================================================================

@st.cache_data(ttl=300)
def load_quality_data():
    QUAL = 'NERO_ANALYTICS."05_QUALITY".DBT_TEST_RESULTS'

    latest_run = rows(f"""
        SELECT INVOCATION_ID, RUN_STARTED_AT, COUNT(*) AS TEST_COUNT,
               COUNT_IF(STATUS = 'pass') AS PASS_COUNT
        FROM {QUAL}
        GROUP BY INVOCATION_ID, RUN_STARTED_AT
        ORDER BY RUN_STARTED_AT DESC LIMIT 1
    """)
    latest_run = latest_run[0] if latest_run else None

    # Latest result per test, across all logged runs (a test not in the
    # most recent invocation -- e.g. removed or renamed -- still shows its
    # last known status here, which is exactly what "all tests we track"
    # should mean).
    all_tests_rows = rows(f"""
        SELECT TEST_NAME, STATUS, FAILURES, EXECUTION_TIME, RUN_STARTED_AT
        FROM {QUAL}
        QUALIFY ROW_NUMBER() OVER (PARTITION BY TEST_NAME ORDER BY RUN_STARTED_AT DESC) = 1
        ORDER BY (STATUS != 'pass') DESC, TEST_NAME
    """)
    all_tests = [
        {"test_name": r["TEST_NAME"], "status": r["STATUS"], "failures": r["FAILURES"],
         "execution_time": r["EXECUTION_TIME"], "run_started_at": r["RUN_STARTED_AT"].isoformat()}
        for r in all_tests_rows
    ]
    currently_failing = [t for t in all_tests if t["status"] != "pass"]
    for t in currently_failing:
        msg_row = one(f"""
            SELECT "MESSAGE" AS MSG FROM {QUAL}
            WHERE TEST_NAME = '{t["test_name"].replace("'", "''")}'
            ORDER BY RUN_STARTED_AT DESC LIMIT 1
        """)
        t["message"] = msg_row["MSG"]

    runs_rows = rows(f"""
        SELECT INVOCATION_ID, RUN_STARTED_AT, COUNT(*) AS TEST_COUNT,
               COUNT_IF(STATUS = 'pass') AS PASS_COUNT,
               COUNT_IF(STATUS != 'pass') AS FAIL_COUNT
        FROM {QUAL}
        GROUP BY INVOCATION_ID, RUN_STARTED_AT
        ORDER BY RUN_STARTED_AT DESC LIMIT 20
    """)
    recent_runs = [
        {"run_started_at": r["RUN_STARTED_AT"].isoformat(), "test_count": r["TEST_COUNT"],
         "pass_count": r["PASS_COUNT"], "fail_count": r["FAIL_COUNT"]}
        for r in runs_rows
    ]

    total_runs = one(f"SELECT COUNT(DISTINCT INVOCATION_ID) AS N FROM {QUAL}")["N"]
    distinct_test_count = one(f"SELECT COUNT(DISTINCT TEST_NAME) AS N FROM {QUAL}")["N"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "last_run_at": latest_run["RUN_STARTED_AT"].isoformat() if latest_run else None,
        "last_run_test_count": latest_run["TEST_COUNT"] if latest_run else 0,
        "last_run_pass_count": latest_run["PASS_COUNT"] if latest_run else 0,
        "all_tests": all_tests,
        "currently_failing": currently_failing,
        "recent_runs": recent_runs,
        "total_runs": total_runs,
        "distinct_test_count": distinct_test_count,
        "logo_b64": branding.LOGO_B64,
    }


def build_quality_pdf(d: dict) -> bytes:
    all_rows = [[t["test_name"], t["status"], fmt_n(t["failures"]), t["run_started_at"][:16]] for t in d["all_tests"]] or \
        [["—", "No test results logged yet.", "", ""]]
    failing_rows = [[t["test_name"], t["status"], fmt_n(t["failures"]), (t.get("message") or "")[:150]] for t in d["currently_failing"]] or \
        [["—", "None currently failing.", "", ""]]
    run_rows = [[r["run_started_at"][:16], fmt_n(r["test_count"]), fmt_n(r["pass_count"]), fmt_n(r["fail_count"])] for r in d["recent_runs"]] or \
        [["—", "", "", ""]]

    sections = [
        ("Executive Summary", [
            rc.body(
                f"<b>{d['distinct_test_count']}</b> distinct tests tracked across <b>{d['total_runs']}</b> "
                f"logged `dbt build` run(s). Last run: <b>{d['last_run_pass_count']}</b> of "
                f"<b>{d['last_run_test_count']}</b> passed, <b>{len(d['currently_failing'])}</b> currently failing."
            ),
            rc.caption("Source: NERO_ANALYTICS.\"05_QUALITY\".DBT_TEST_RESULTS, logged by the log_dbt_test_results on-run-end hook."),
        ]),
        ("1. Currently Failing", [
            rc.styled_table(["Test", "Status", "Failures", "Message"], failing_rows, col_widths=[200, 60, 60, 260]),
        ]),
        ("2. All Tests, Latest Result", [
            rc.styled_table(["Test", "Status", "Failures", "Last Run"], all_rows, col_widths=[220, 60, 60, 140]),
        ]),
        ("3. Recent Runs", [
            rc.styled_table(["Run Started", "Tests", "Passed", "Failed"], run_rows, col_widths=[140, 90, 90, 90]),
        ]),
    ]
    return rc.build_pdf(
        report_title="Data Quality",
        report_subtitle="Nero Platform — dbt test results, logged per run",
        prepared_for="Platform Engineering",
        sections=sections,
    )


def build_quality_excel(d: dict) -> bytes:
    sheets = {
        "All Tests": pd.DataFrame(d["all_tests"] or [{"test_name": "", "status": "", "failures": 0, "run_started_at": ""}])
            [["test_name", "status", "failures", "run_started_at"]]
            .rename(columns={"test_name": "Test", "status": "Status", "failures": "Failures", "run_started_at": "Last Run"}),
        "Currently Failing": pd.DataFrame(d["currently_failing"] or [{"test_name": "", "status": "", "failures": 0, "message": ""}])
            [["test_name", "status", "failures", "message"]]
            .rename(columns={"test_name": "Test", "status": "Status", "failures": "Failures", "message": "Message"}),
        "Recent Runs": pd.DataFrame(d["recent_runs"] or [{"run_started_at": "", "test_count": 0, "pass_count": 0, "fail_count": 0}])
            .rename(columns={"run_started_at": "Run Started", "test_count": "Tests", "pass_count": "Passed", "fail_count": "Failed"}),
    }
    return rc.write_excel_workbook(
        title="Data Quality",
        subtitle="Nero Platform — dbt test results, logged per run",
        sheets=sheets,
    )


def fmt_n(v):
    return "—" if v is None else f"{v:,}"


# ==================================================================
# Render: three tabs
# ==================================================================

pipeline_data = load_pipeline_data()
security_data = load_security_data()
security_data["logo_b64"] = branding.LOGO_B64
quality_data = load_quality_data()

tab_pipeline, tab_security, tab_quality = st.tabs(["Pipeline Health", "Security & Governance", "Data Quality"])

with tab_pipeline:
    template_path = Path(__file__).parent / "pipeline_health_template.html"
    html = template_path.read_text().replace("__DATA_JSON__", json.dumps(pipeline_data))
    components.html(html, height=1900, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
    today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    with col1:
        st.download_button(
            "Download PDF report", data=build_pipeline_pdf(pipeline_data),
            file_name=f"nero_pipeline_health_{today_str}.pdf", mime="application/pdf",
            key="pipeline_pdf",
        )
    with col2:
        st.download_button(
            "Download Excel workbook", data=build_pipeline_excel(pipeline_data),
            file_name=f"nero_pipeline_health_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="pipeline_xlsx",
        )

with tab_security:
    template_path = Path(__file__).parent / "security_dashboard_template.html"
    html2 = template_path.read_text().replace("__DATA_JSON__", json.dumps(security_data))
    components.html(html2, height=2400, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download PDF report", data=build_security_pdf(security_data),
            file_name=f"nero_security_governance_report_{today_str}.pdf", mime="application/pdf",
            key="security_pdf",
        )
    with col2:
        st.download_button(
            "Download Excel workbook", data=build_security_excel(security_data),
            file_name=f"nero_security_governance_report_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="security_xlsx",
        )

with tab_quality:
    template_path = Path(__file__).parent / "quality_dashboard_template.html"
    html3 = template_path.read_text().replace("__DATA_JSON__", json.dumps(quality_data))
    components.html(html3, height=1700, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download PDF report", data=build_quality_pdf(quality_data),
            file_name=f"nero_data_quality_{today_str}.pdf", mime="application/pdf",
            key="quality_pdf",
        )
    with col2:
        st.download_button(
            "Download Excel workbook", data=build_quality_excel(quality_data),
            file_name=f"nero_data_quality_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="quality_xlsx",
        )
