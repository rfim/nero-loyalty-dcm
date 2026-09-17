"""Security & Horizon Governance Report — formal, exportable Streamlit-in-Snowflake app.

Deeper than the earlier combined Cost & Security Watch app: adds the full
role-grants inventory (grouped), a 30-day login-activity trend (success vs.
failed, not just failed), and an explicit network-policy status check.
Exports the same data as a formal PDF (reportlab) and a multi-sheet Excel
workbook (xlsxwriter) via report_common.py.
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

import report_common as rc

st.set_page_config(page_title="Security & Horizon Governance Report", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


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

    return {
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


data = load_security_data()
if data["security"]["login_daily"]:
    data["period_start"] = min(r["date"] for r in data["security"]["login_daily"])
    data["period_end"] = max(r["date"] for r in data["security"]["login_daily"])

template_path = Path(__file__).parent / "security_dashboard_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))
components.html(html, height=2400, scrolling=True)


# ---------------- exports ----------------

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


st.divider()
st.subheader("Export")
col1, col2 = st.columns(2)
today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
with col1:
    st.download_button(
        "Download PDF report", data=build_security_pdf(data),
        file_name=f"nero_security_governance_report_{today_str}.pdf", mime="application/pdf",
    )
with col2:
    st.download_button(
        "Download Excel workbook", data=build_security_excel(data),
        file_name=f"nero_security_governance_report_{today_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
