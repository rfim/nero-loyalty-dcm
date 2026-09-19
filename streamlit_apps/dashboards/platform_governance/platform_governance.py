"""Platform Governance — the one combined Streamlit-in-Snowflake app.
Replaces five previously separate apps (Pipeline Health, Security &
Horizon Governance Report, Chatbot Security Governance Report, and the
already-combined Cost Reports app, which itself used to be two) with a
single app, six tabs -- one dashboard, not several dashboards that each
happen to have tabs.

Tab 1 (Pipeline Health): end-to-end orchestration status -- staging
landing -> dbt transform -> what's live in each layer. Updated for the
Staging/Bronze/Silver rename (see analytics/README.md) and PROCESS_BATCH's
retirement.

Tab 2 (Security & Governance): Trust Center findings, ACCOUNTADMIN
holders, role grants, login activity. Reads NERO_GOVERNANCE.SECURITY,
untouched by any of this session's schema migrations.

Tab 3 (Data Quality): NEW. dbt's own test results, actually persisted --
before this, `dbt build`'s 74 tests reported pass/fail only to whoever ran
the CLI. See analytics/macros/log_dbt_test_results.sql (the on-run-end
hook that logs every test) and account_setup/dbt_quality_log.sql (the
table + grants). No dashboard like this existed before; this is a real
gap being closed, not just a consolidation.

Tab 4 (Platform Cost) / Tab 5 (Chatbot Cost): warehouse + Cortex compute
spend against budget, and what the Nero Assistant chatbot itself costs by
caller. Reads NERO_GOVERNANCE.COST, untouched by this session's
migrations. Chatbot cost keys off USER_NAME rather than AGENT_NAME:
nero_assistant.py calls the Cortex Agent lite-run API with inline tools
rather than referencing NERO_PLATFORM_AGENT by name, so
CORTEX_AGENT_USAGE_HISTORY's AGENT_NAME column is always null for its
traffic.

Tab 6 (Chatbot Security): folded in from the standalone
chatbot_security_report.py app -- who can reach the Nero Assistant's
tools (MCP server, agent, search service, semantic view), who has
actually called them, and whether the MCP server is registered and live.
Distinct from Tab 2's account-wide Security & Governance view: this one
is scoped to the chatbot's own attack surface.
"""
import calendar
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
# Tab 4: Platform Cost
# ==================================================================

@st.cache_data(ttl=300)
def load_cost_data():
    budget_rows = rows(f"SELECT * FROM {GOV}.COST.BUDGET_VS_ACTUAL_MONTH_TO_DATE")
    warehouses = [
        {"name": r["WAREHOUSE_NAME"], "workload": r["WORKLOAD"],
         "quota": float(r["CREDIT_QUOTA"]), "used": float(r["CREDITS_USED_MTD"]),
         "pct": float(r["PCT_OF_BUDGET_USED"])}
        for r in budget_rows
    ]
    max_pct_warehouse = max(warehouses, key=lambda w: w["pct"]) if warehouses else None

    lifetime = one(f"SELECT SUM(CREDITS_USED) AS N FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY")
    total_credits_lifetime = float(lifetime["N"] or 0)

    rate_row = session.sql("""
        SELECT EFFECTIVE_RATE FROM SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY
        WHERE USAGE_TYPE = 'compute' ORDER BY DATE DESC LIMIT 1
    """).collect()
    credit_rate = float(rate_row[0]["EFFECTIVE_RATE"]) if rate_row else 0.0

    mtd = one(f"""
        SELECT COALESCE(SUM(CREDITS_USED), 0) AS N FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY
        WHERE USAGE_DATE >= DATE_TRUNC('month', CURRENT_DATE())
    """)
    mtd_credits = float(mtd["N"])

    today = datetime.now(timezone.utc)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    forecast_credits = (mtd_credits / days_elapsed * days_in_month) if days_elapsed else mtd_credits

    daily_rows = rows(f"""
        SELECT USAGE_DATE, WAREHOUSE_NAME, CREDITS_USED
        FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY
        WHERE USAGE_DATE >= DATEADD('day', -7, CURRENT_DATE())
        ORDER BY USAGE_DATE
    """)
    by_date = {}
    for r in daily_rows:
        d = str(r["USAGE_DATE"])
        by_date.setdefault(d, {})[r["WAREHOUSE_NAME"]] = float(r["CREDITS_USED"])
    daily_by_warehouse = [{"date": d, "credits": c} for d, c in sorted(by_date.items())]

    role_rows = rows(f"""
        SELECT WAREHOUSE_NAME, ROLE_NAME, SUM(QUERY_COUNT) AS Q, SUM(EXECUTION_HOURS) AS H, SUM(CLOUD_SERVICES_CREDITS) AS C
        FROM {GOV}.COST.QUERY_COST_BY_ROLE_DAILY
        WHERE QUERY_DATE >= DATEADD('day', -30, CURRENT_DATE())
        GROUP BY WAREHOUSE_NAME, ROLE_NAME
        ORDER BY H DESC
    """)
    role_attribution = [
        {"warehouse": r["WAREHOUSE_NAME"], "role": r["ROLE_NAME"], "query_count": int(r["Q"] or 0),
         "execution_hours": float(r["H"] or 0), "cloud_services_credits": float(r["C"] or 0)}
        for r in role_rows
    ]

    cortex_rows = rows(f"""
        SELECT SOURCE, SUM(CREDITS) AS C, SUM(TOKENS) AS T, SUM(REQUEST_COUNT) AS R
        FROM {GOV}.COST.CORTEX_CREDITS_DAILY GROUP BY SOURCE ORDER BY C DESC
    """)
    cortex_by_source = [
        {"source": r["SOURCE"], "credits": float(r["C"] or 0), "tokens": int(r["T"] or 0), "requests": int(r["R"] or 0)}
        for r in cortex_rows
    ]
    cortex_credits_lifetime = sum(c["credits"] for c in cortex_by_source)

    warehouse_user_rows = rows(f"""
        SELECT WAREHOUSE_NAME, USER_NAME, SUM(QUERY_COUNT) AS Q, SUM(CREDITS_ATTRIBUTED) AS C
        FROM {GOV}.COST.WAREHOUSE_USER_CREDITS_DAILY
        WHERE USAGE_DATE >= DATEADD('day', -30, CURRENT_DATE())
        GROUP BY WAREHOUSE_NAME, USER_NAME
        ORDER BY C DESC
    """)
    warehouse_user_costs = [
        {"warehouse": r["WAREHOUSE_NAME"], "user": r["USER_NAME"], "query_count": int(r["Q"] or 0),
         "credits": float(r["C"] or 0)}
        for r in warehouse_user_rows
    ]

    period_start = one(f"SELECT MIN(USAGE_DATE) AS D FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY")["D"]
    period_end = one(f"SELECT MAX(USAGE_DATE) AS D FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY")["D"]

    monthly_rows = rows(f"""
        SELECT DATE_TRUNC('month', USAGE_DATE) AS MONTH, SUM(CREDITS) AS C
        FROM {GOV}.COST.ALL_COMPUTE_CREDITS_DAILY
        GROUP BY MONTH ORDER BY MONTH
    """)
    current_month_key = today.strftime("%Y-%m")
    monthly_breakdown = []
    for r in monthly_rows:
        m = r["MONTH"]
        actual_credits = float(r["C"] or 0)
        month_key = m.strftime("%Y-%m")
        is_current = month_key == current_month_key
        estimated_credits = (
            (actual_credits / days_elapsed * days_in_month) if is_current and days_elapsed else actual_credits
        )
        monthly_breakdown.append({
            "month": month_key,
            "month_label": m.strftime("%B %Y"),
            "is_current": is_current,
            "actual_credits": actual_credits,
            "actual_usd": actual_credits * credit_rate,
            "estimated_credits": estimated_credits,
            "estimated_usd": estimated_credits * credit_rate,
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_start": str(period_start) if period_start else None,
        "period_end": str(period_end) if period_end else None,
        "cost": {
            "warehouses": warehouses,
            "max_pct_warehouse": max_pct_warehouse,
            "total_credits_lifetime": total_credits_lifetime,
            "total_usd_lifetime": total_credits_lifetime * credit_rate,
            "credit_rate": credit_rate,
            "mtd_credits": mtd_credits,
            "mtd_usd": mtd_credits * credit_rate,
            "forecast_usd": forecast_credits * credit_rate,
            "daily_by_warehouse": daily_by_warehouse,
            "role_attribution": role_attribution,
            "cortex_by_source": cortex_by_source,
            "cortex_credits_lifetime": cortex_credits_lifetime,
            "cortex_usd_lifetime": cortex_credits_lifetime * credit_rate,
            "warehouse_user_costs": warehouse_user_costs,
            "monthly_breakdown": monthly_breakdown,
        },
    }


def build_cost_pdf(d: dict) -> bytes:
    c = d["cost"]
    budget_rows = [[w["name"], w["workload"], f"{w['quota']:.2f}", f"{w['used']:.3f}", f"{w['pct']:.1f}%"] for w in c["warehouses"]]
    role_rows = [[r["warehouse"], r["role"], f"{r['query_count']:,}", f"{r['execution_hours']:.2f}", f"{r['cloud_services_credits']:.3f}"] for r in c["role_attribution"]] or [["—", "No query activity in the last 30 days", "", "", ""]]
    cortex_rows = [[r["source"], f"{r['credits']:.3f}", f"{r['tokens']:,}", f"{r['requests']:,}"] for r in c["cortex_by_source"]] or [["—", "No Cortex usage recorded", "", ""]]
    wu_rows = [[r["warehouse"], r["user"], f"{r['query_count']:,}", f"{r['credits']:.3f}"] for r in c["warehouse_user_costs"]] or [["—", "No data yet — QUERY_ATTRIBUTION_HISTORY lags up to ~24h", "", ""]]
    monthly_rows = [
        [m["month_label"], f"{m['actual_credits']:.3f}", f"${m['actual_usd']:.2f}",
         f"${m['estimated_usd']:.2f}" if m["is_current"] else "—",
         "In progress" if m["is_current"] else "Complete"]
        for m in c["monthly_breakdown"]
    ] or [["—", "No usage history yet", "", "", ""]]

    sections = [
        ("Executive Summary", [
            rc.body(
                f"Lifetime warehouse spend to date is <b>${c['total_usd_lifetime']:.2f}</b> "
                f"({c['total_credits_lifetime']:.3f} credits at ${c['credit_rate']:.2f}/credit). "
                f"Month-to-date spend is <b>${c['mtd_usd']:.2f}</b>, projecting to "
                f"<b>${c['forecast_usd']:.2f}</b> by month end on current pace."
            ),
            rc.body(
                f"Highest budget utilization: <b>{c['max_pct_warehouse']['name']}</b> at "
                f"{c['max_pct_warehouse']['pct']:.1f}% of its monthly quota." if c["max_pct_warehouse"] else "No warehouse usage recorded this month."
            ),
            rc.caption("All figures from SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY, subject to replication latency of up to a few hours."),
        ]),
        ("1. Budget vs. Actual, Month to Date", [
            rc.styled_table(["Warehouse", "Workload", "Quota (cr)", "Used MTD (cr)", "% Used"], budget_rows,
                             col_widths=[85, 110, 65, 80, 60]),
            rc.caption("Each warehouse is bound to its own resource monitor; see account_setup/warehouses_and_monitors.sql."),
        ]),
        ("2. Cost Attribution by Role, Last 30 Days", [
            rc.styled_table(["Warehouse", "Role", "Queries", "Exec. Hours", "Cloud Svc (cr)"], role_rows,
                             col_widths=[85, 110, 65, 75, 75]),
        ]),
        ("3. Serverless (Cortex) Usage", [
            rc.styled_table(["Source", "Credits", "Tokens", "Requests"], cortex_rows,
                             col_widths=[160, 90, 90, 90]),
        ]),
        ("4. Cost per Warehouse per User, Last 30 Days", [
            rc.styled_table(["Warehouse", "User", "Queries", "Credits Attributed"], wu_rows,
                             col_widths=[100, 130, 80, 110]),
            rc.caption(
                "Real per-query credit attribution from SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY "
                "(CREDITS_ATTRIBUTED_COMPUTE) — not an execution-time proxy. This view characteristically "
                "lags up to ~24 hours, longer than other ACCOUNT_USAGE views; each of the four workloads "
                "(NERO_INGEST_USER, NERO_CI_USER, NERO_BI_USER, NERO_DBT_USER) now has its own login, so "
                "once the lag clears this table attributes spend to an actual identity per warehouse."
            ),
        ]),
        ("5. Monthly Spend & Estimate", [
            rc.styled_table(["Month", "Credits", "Actual USD", "Projected Month-End USD", "Status"], monthly_rows,
                             col_widths=[100, 80, 90, 130, 90]),
            rc.caption(
                "Whole-account compute (warehouses + Cortex) grouped by calendar month, from "
                "NERO_GOVERNANCE.COST.ALL_COMPUTE_CREDITS_DAILY. Completed months show final actuals; "
                "the current month's 'Projected Month-End' is a linear estimate from days elapsed "
                "so far this month, same method as the month-to-date forecast in the Executive Summary."
            ),
        ]),
    ]
    return rc.build_pdf(
        report_title="Cost Governance Report",
        report_subtitle="Nero Platform — warehouse and Cortex compute spend against budget",
        prepared_for="Platform Engineering",
        sections=sections,
    )


def build_cost_excel(d: dict) -> bytes:
    c = d["cost"]
    sheets = {
        "Budget vs Actual": pd.DataFrame(c["warehouses"])[["name", "workload", "quota", "used", "pct"]]
            .rename(columns={"name": "Warehouse", "workload": "Workload", "quota": "Quota (cr)", "used": "Used MTD (cr)", "pct": "% Used"}),
        "Cost by Role": pd.DataFrame(c["role_attribution"] or [{"warehouse": "", "role": "", "query_count": 0, "execution_hours": 0, "cloud_services_credits": 0}])
            .rename(columns={"warehouse": "Warehouse", "role": "Role", "query_count": "Queries", "execution_hours": "Exec Hours", "cloud_services_credits": "Cloud Services (cr)"}),
        "Cortex Usage": pd.DataFrame(c["cortex_by_source"] or [{"source": "", "credits": 0, "tokens": 0, "requests": 0}])
            .rename(columns={"source": "Source", "credits": "Credits", "tokens": "Tokens", "requests": "Requests"}),
        "Daily Credits": pd.DataFrame([
            {"Date": row["date"], **row["credits"]} for row in c["daily_by_warehouse"]
        ]) if c["daily_by_warehouse"] else pd.DataFrame([{"Date": None}]),
        "Cost by Warehouse & User": pd.DataFrame(c["warehouse_user_costs"] or [{"warehouse": "", "user": "", "query_count": 0, "credits": 0}])
            .rename(columns={"warehouse": "Warehouse", "user": "User", "query_count": "Queries", "credits": "Credits Attributed"}),
        "Monthly Spend": pd.DataFrame(
            c["monthly_breakdown"] or [{"month_label": "", "actual_credits": 0, "actual_usd": 0, "estimated_usd": 0, "is_current": False}]
        )[["month_label", "actual_credits", "actual_usd", "estimated_usd", "is_current"]]
            .rename(columns={"month_label": "Month", "actual_credits": "Credits", "actual_usd": "Actual USD",
                              "estimated_usd": "Projected Month-End USD", "is_current": "In Progress"}),
    }
    return rc.write_excel_workbook(
        title="Cost Governance Report",
        subtitle="Nero Platform — warehouse and Cortex compute spend against budget",
        sheets=sheets,
    )


# ==================================================================
# Tab 5: Chatbot Cost
# ==================================================================

@st.cache_data(ttl=300)
def load_chatbot_cost_data():
    rate_row = session.sql("""
        SELECT EFFECTIVE_RATE FROM SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY
        WHERE USAGE_TYPE = 'compute' ORDER BY DATE DESC LIMIT 1
    """).collect()
    credit_rate = float(rate_row[0]["EFFECTIVE_RATE"]) if rate_row else 0.0

    user_rows = rows(f"""
        SELECT USER_NAME, SUM(REQUEST_COUNT) AS Q, SUM(CREDITS) AS C, SUM(TOKENS) AS T
        FROM {GOV}.COST.CHATBOT_AGENT_CREDITS_DAILY
        GROUP BY USER_NAME ORDER BY C DESC
    """)
    by_user = [
        {"user_name": r["USER_NAME"], "request_count": int(r["Q"] or 0),
         "credits": float(r["C"] or 0), "tokens": int(r["T"] or 0)}
        for r in user_rows
    ]
    total_credits = sum(u["credits"] for u in by_user)
    total_requests = sum(u["request_count"] for u in by_user)

    tool_rows = rows(f"""
        SELECT SOURCE, SUM(CREDITS) AS C, SUM(REQUEST_COUNT) AS R
        FROM {GOV}.COST.CORTEX_CREDITS_DAILY
        WHERE SOURCE IN ('Cortex Analyst', 'Cortex Search')
        GROUP BY SOURCE ORDER BY C DESC
    """)
    tool_usage = [
        {"source": r["SOURCE"], "credits": float(r["C"] or 0), "requests": int(r["R"] or 0)}
        for r in tool_rows
    ]

    monthly_rows = rows(f"""
        SELECT DATE_TRUNC('month', USAGE_DATE) AS MONTH, SUM(CREDITS) AS C, SUM(REQUEST_COUNT) AS R
        FROM {GOV}.COST.CHATBOT_AGENT_CREDITS_DAILY
        GROUP BY MONTH ORDER BY MONTH
    """)
    today = datetime.now(timezone.utc)
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_elapsed = today.day
    current_month_key = today.strftime("%Y-%m")
    monthly_breakdown = []
    for r in monthly_rows:
        m = r["MONTH"]
        actual_credits = float(r["C"] or 0)
        month_key = m.strftime("%Y-%m")
        is_current = month_key == current_month_key
        estimated_credits = (
            (actual_credits / days_elapsed * days_in_month) if is_current and days_elapsed else actual_credits
        )
        monthly_breakdown.append({
            "month": month_key,
            "month_label": m.strftime("%B %Y"),
            "is_current": is_current,
            "actual_credits": actual_credits,
            "actual_usd": actual_credits * credit_rate,
            "estimated_credits": estimated_credits,
            "estimated_usd": estimated_credits * credit_rate,
            "request_count": int(r["R"] or 0),
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "credit_rate": credit_rate,
        "total_credits": total_credits,
        "total_usd": total_credits * credit_rate,
        "total_requests": total_requests,
        "by_user": by_user,
        "tool_usage": tool_usage,
        "monthly_breakdown": monthly_breakdown,
    }


def build_chatbot_pdf(d: dict) -> bytes:
    rate = d["credit_rate"]
    user_rows = [
        [u["user_name"], f"{u['request_count']:,}", f"{u['tokens']:,}", f"{u['credits']:.4f}", f"${u['credits'] * rate:.2f}"]
        for u in d["by_user"]
    ] or [["—", "No agent usage recorded", "", "", ""]]
    tool_rows = [
        [t["source"], f"{t['credits']:.4f}", f"${t['credits'] * rate:.2f}", f"{t['requests']:,}"]
        for t in d["tool_usage"]
    ] or [["—", "No standalone tool usage recorded", "", ""]]
    monthly_rows = [
        [m["month_label"], f"{m['actual_credits']:.4f}", f"${m['actual_usd']:.2f}",
         f"${m['estimated_usd']:.2f}" if m["is_current"] else "—",
         "In progress" if m["is_current"] else "Complete"]
        for m in d["monthly_breakdown"]
    ] or [["—", "No usage history yet", "", "", ""]]
    sections = [
        ("Executive Summary", [
            rc.body(f"Total chatbot spend to date: <b>${d['total_usd']:.2f}</b> ({d['total_credits']:.4f} credits at ${d['credit_rate']:.2f}/credit) across <b>{d['total_requests']}</b> agent requests."),
            rc.caption("Source: SNOWFLAKE.ACCOUNT_USAGE.CORTEX_AGENT_USAGE_HISTORY, keyed by USER_NAME (the chatbot calls the lite-run API without referencing a named agent object)."),
        ]),
        ("1. Agent Usage by Caller", [
            rc.styled_table(["User", "Requests", "Tokens", "Credits", "USD"], user_rows, col_widths=[150, 80, 90, 80, 80]),
        ]),
        ("2. Underlying Tool Usage", [
            rc.styled_table(["Tool", "Credits", "USD", "Requests"], tool_rows, col_widths=[150, 100, 100, 110]),
        ]),
        ("3. Monthly Spend & Estimate", [
            rc.styled_table(["Month", "Credits", "Actual USD", "Projected Month-End USD", "Status"], monthly_rows,
                             col_widths=[100, 80, 90, 130, 90]),
            rc.caption(
                "Grouped by calendar month from NERO_GOVERNANCE.COST.CHATBOT_AGENT_CREDITS_DAILY. Completed "
                "months show final actuals; the current month's 'Projected Month-End' is a linear estimate "
                "from days elapsed so far this month."
            ),
        ]),
    ]
    return rc.build_pdf(
        report_title="Chatbot Cost Governance Report",
        report_subtitle="Nero Platform — what the Nero Assistant chatbot costs, by user",
        prepared_for="Platform Engineering",
        sections=sections,
    )


def build_chatbot_excel(d: dict) -> bytes:
    rate = d["credit_rate"]
    by_user = [
        {"user_name": u["user_name"], "request_count": u["request_count"], "credits": u["credits"],
         "usd": u["credits"] * rate, "tokens": u["tokens"]}
        for u in d["by_user"]
    ] or [{"user_name": "", "request_count": 0, "credits": 0, "usd": 0, "tokens": 0}]
    tool_usage = [
        {"source": t["source"], "credits": t["credits"], "usd": t["credits"] * rate, "requests": t["requests"]}
        for t in d["tool_usage"]
    ] or [{"source": "", "credits": 0, "usd": 0, "requests": 0}]
    sheets = {
        "Agent Usage by User": pd.DataFrame(by_user)
            .rename(columns={"user_name": "User", "request_count": "Requests", "credits": "Credits", "usd": "USD", "tokens": "Tokens"}),
        "Tool Usage": pd.DataFrame(tool_usage)
            .rename(columns={"source": "Tool", "credits": "Credits", "usd": "USD", "requests": "Requests"}),
        "Monthly Spend": pd.DataFrame(
            d["monthly_breakdown"] or [{"month_label": "", "actual_credits": 0, "actual_usd": 0, "estimated_usd": 0, "is_current": False}]
        )[["month_label", "actual_credits", "actual_usd", "estimated_usd", "is_current"]]
            .rename(columns={"month_label": "Month", "actual_credits": "Credits", "actual_usd": "Actual USD",
                              "estimated_usd": "Projected Month-End USD", "is_current": "In Progress"}),
    }
    return rc.write_excel_workbook(
        title="Chatbot Cost Governance Report",
        subtitle="Nero Platform — what the Nero Assistant chatbot costs, by user",
        sheets=sheets,
    )


# ==================================================================
# Tab 6: Chatbot Security
# ==================================================================

CHATBOT_OBJECT_NAMES = [
    "NERO_PLATFORM_AGENT", "NERO_PLATFORM_MCP_SERVER",
    "FINDINGS_SEARCH_SVC", "LOYALTY_SEMANTIC_VIEW", "GOVERNANCE_SEMANTIC_VIEW",
]


@st.cache_data(ttl=120)
def load_chatbot_security_data():
    placeholders = ", ".join("?" for _ in CHATBOT_OBJECT_NAMES)
    grant_rows_raw = session.sql(
        f"""SELECT OBJECT_NAME, GRANTED_ON, PRIVILEGE, ROLE_NAME
            FROM {GOV}.SECURITY.ROLE_GRANTS_INVENTORY
            WHERE OBJECT_NAME IN ({placeholders})
            ORDER BY OBJECT_NAME, ROLE_NAME""",
        params=CHATBOT_OBJECT_NAMES,
    ).collect()
    grants = [
        {"object_name": r["OBJECT_NAME"], "object_type": r["GRANTED_ON"],
         "privilege": r["PRIVILEGE"], "grantee": r["ROLE_NAME"]}
        for r in grant_rows_raw
    ]

    mcp_rows = rows(f"SELECT SERVER_NAME FROM {GOV}.SECURITY.MCP_SERVER_REGISTRY WHERE IS_DISABLED = FALSE")
    mcp_registered = any(r["SERVER_NAME"] == "NERO_PLATFORM_MCP_SERVER" for r in mcp_rows)

    mcp_log_rows = rows(f"SELECT * FROM {GOV}.SECURITY.MCP_TOOL_CALL_LOG LIMIT 100")
    mcp_log = [
        {"timestamp": str(r["TIMESTAMP"]), "tool_name": r["TOOL_NAME"], "user_name": r["USER_NAME"],
         "role_name": r["ROLE_NAME"], "status": r["STATUS"]}
        for r in mcp_log_rows
    ]

    login_rows = rows("""
        SELECT EVENT_TIMESTAMP, USER_NAME, IS_SUCCESS, REPORTED_CLIENT_TYPE
        FROM SNOWFLAKE.ACCOUNT_USAGE.LOGIN_HISTORY
        WHERE EVENT_TIMESTAMP >= DATEADD('day', -30, CURRENT_TIMESTAMP())
          AND (USER_NAME IN ('NERO_COPILOT_USER', 'NERO_BI_USER') OR USER_NAME ILIKE 'STPLATSTREAMLIT%')
        ORDER BY EVENT_TIMESTAMP DESC
        LIMIT 200
    """)
    logins = [
        {"event_timestamp": str(r["EVENT_TIMESTAMP"]), "user_name": r["USER_NAME"],
         "is_success": r["IS_SUCCESS"], "client_type": r["REPORTED_CLIENT_TYPE"]}
        for r in login_rows
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mcp_registered": mcp_registered,
        "grants": grants,
        "mcp_log": mcp_log,
        "logins": logins,
    }


def build_chatbot_security_pdf(d: dict) -> bytes:
    grant_rows = [[g["object_name"], g["object_type"], g["privilege"], g["grantee"]] for g in d["grants"]] or [["—", "—", "—", "No grants found"]]
    mcp_rows = [[r["timestamp"][:19], r["tool_name"], r["user_name"], r["status"]] for r in d["mcp_log"]] or [["—", "—", "—", "No MCP tool calls logged yet"]]
    login_rows = [[r["event_timestamp"][:19], r["user_name"], r["is_success"]] for r in d["logins"]] or [["—", "—", "No chatbot-identity logins in 30 days"]]

    expected_grantees = ("ACCOUNTADMIN", "NERO_BI_ROLE", "NERO_GOVERNANCE_ROLE", "NERO_COPILOT_ROLE")
    nonstandard = [g for g in d["grants"] if g["grantee"] not in expected_grantees and g["privilege"] != "OWNERSHIP"]

    sections = [
        ("Executive Summary", [
            rc.body(f"MCP server status: <b>{'Registered and live' if d['mcp_registered'] else 'NOT FOUND'}</b>."),
            rc.body(f"{len(d['grants'])} grant(s) found on the four chatbot objects (agent, MCP server, search service, semantic view)."
                     + (f" <b>{len(nonstandard)} unexpected grantee(s)</b> outside ACCOUNTADMIN/NERO_BI_ROLE/NERO_COPILOT_ROLE." if nonstandard else " All grantees match the expected three roles.")),
            rc.body(f"{len(d['mcp_log'])} MCP tool call(s) logged; {len(d['logins'])} login(s) from chatbot-related identities in the last 30 days."),
            rc.caption("Access review reads NERO_GOVERNANCE.SECURITY.ROLE_GRANTS_INVENTORY (an ACCOUNT_USAGE.GRANTS_TO_ROLES wrapper, lags up to a few hours), not live SHOW GRANTS ON."),
        ]),
        ("1. Access to Chatbot Tools", [
            rc.styled_table(["Object", "Type", "Privilege", "Granted To"], grant_rows, col_widths=[150, 110, 80, 130]),
        ]),
        ("2. MCP Tool-Call Audit Log", [
            rc.styled_table(["Time", "Tool", "User", "Status"], mcp_rows, col_widths=[110, 130, 110, 110]),
        ]),
        ("3. Chatbot-Related Logins, Last 30 Days", [
            rc.styled_table(["Time", "User", "Success"], login_rows, col_widths=[130, 160, 100]),
        ]),
    ]
    return rc.build_pdf(
        report_title="Chatbot Security Governance Report",
        report_subtitle="Nero Platform — access, MCP audit log and login activity for the Nero Assistant chatbot",
        prepared_for="Platform Engineering",
        sections=sections,
    )


def build_chatbot_security_excel(d: dict) -> bytes:
    sheets = {
        "Access Grants": pd.DataFrame(d["grants"] or [{"object_name": "", "object_type": "", "privilege": "", "grantee": ""}])
            .rename(columns={"object_name": "Object", "object_type": "Type", "privilege": "Privilege", "grantee": "Granted To"}),
        "MCP Tool Calls": pd.DataFrame(d["mcp_log"] or [{"timestamp": "", "tool_name": "", "user_name": "", "role_name": "", "status": ""}])
            .rename(columns={"timestamp": "Time", "tool_name": "Tool", "user_name": "User", "role_name": "Role", "status": "Status"}),
        "Chatbot Logins": pd.DataFrame(d["logins"] or [{"event_timestamp": "", "user_name": "", "is_success": "", "client_type": ""}])
            .rename(columns={"event_timestamp": "Time", "user_name": "User", "is_success": "Success", "client_type": "Client"}),
    }
    return rc.write_excel_workbook(
        title="Chatbot Security Governance Report",
        subtitle="Nero Platform — access, MCP audit log and login activity for the Nero Assistant chatbot",
        sheets=sheets,
    )


# ==================================================================
# Render: six tabs, one app
# ==================================================================

today_str = datetime.now(timezone.utc).strftime("%Y%m%d")

pipeline_data = load_pipeline_data()
security_data = load_security_data()
security_data["logo_b64"] = branding.LOGO_B64
quality_data = load_quality_data()

tab_pipeline, tab_security, tab_quality, tab_cost, tab_chatbot_cost, tab_chatbot_security = st.tabs(
    ["Pipeline Health", "Security & Governance", "Data Quality", "Platform Cost", "Chatbot Cost", "Chatbot Security"]
)

with tab_pipeline:
    template_path = Path(__file__).parent / "pipeline_health_template.html"
    html = template_path.read_text().replace("__DATA_JSON__", json.dumps(pipeline_data))
    components.html(html, height=1900, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
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

with tab_cost:
    cost_data = load_cost_data()
    cost_data["logo_b64"] = branding.LOGO_B64
    html4 = (Path(__file__).parent / "cost_dashboard_template.html").read_text().replace(
        "__DATA_JSON__", json.dumps(cost_data)
    )
    components.html(html4, height=2100, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download PDF report", data=build_cost_pdf(cost_data),
            file_name=f"nero_cost_governance_report_{today_str}.pdf", mime="application/pdf",
            key="platform_pdf",
        )
    with col2:
        st.download_button(
            "Download Excel workbook", data=build_cost_excel(cost_data),
            file_name=f"nero_cost_governance_report_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="platform_xlsx",
        )

with tab_chatbot_cost:
    chatbot_data = load_chatbot_cost_data()
    chatbot_data["logo_b64"] = branding.LOGO_B64
    html5 = (Path(__file__).parent / "chatbot_cost_dashboard_template.html").read_text().replace(
        "__DATA_JSON__", json.dumps(chatbot_data)
    )
    components.html(html5, height=1500, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download PDF report", data=build_chatbot_pdf(chatbot_data),
            file_name=f"nero_chatbot_cost_report_{today_str}.pdf", mime="application/pdf",
            key="chatbot_pdf",
        )
    with col2:
        st.download_button(
            "Download Excel workbook", data=build_chatbot_excel(chatbot_data),
            file_name=f"nero_chatbot_cost_report_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="chatbot_xlsx",
        )

with tab_chatbot_security:
    chatbot_security_data = load_chatbot_security_data()
    chatbot_security_data["logo_b64"] = branding.LOGO_B64
    html6 = (Path(__file__).parent / "chatbot_security_dashboard_template.html").read_text().replace(
        "__DATA_JSON__", json.dumps(chatbot_security_data)
    )
    components.html(html6, height=1700, scrolling=True)

    st.divider()
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "Download PDF report", data=build_chatbot_security_pdf(chatbot_security_data),
            file_name=f"nero_chatbot_security_report_{today_str}.pdf", mime="application/pdf",
            key="chatbot_security_pdf",
        )
    with col2:
        st.download_button(
            "Download Excel workbook", data=build_chatbot_security_excel(chatbot_security_data),
            file_name=f"nero_chatbot_security_report_{today_str}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="chatbot_security_xlsx",
        )
