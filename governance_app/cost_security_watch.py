"""Cost & Security Watch — native Streamlit-in-Snowflake governance dashboard.

Queries NERO_GOVERNANCE (COST + SECURITY schemas) live: warehouse/Cortex
credit spend against the resource-monitor budgets set up in
account_setup/warehouses_and_monitors.sql, and open Horizon (Trust Center)
findings from account_setup/trust_center_findings.sql.

Same design system as streamlit_app/loyalty_trading_pulse.py (Fraunces +
IBM Plex Sans, dataviz-validated palette, hand-rolled SVG charts with hover
tooltips) — dashboard_template.html holds that markup, filled at render
time via a single __DATA_JSON__ placeholder.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Cost & Security Watch", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


@st.cache_data(ttl=300)
def load_dashboard_data():
    # ---- cost ----
    budget_rows = rows(f'SELECT * FROM {GOV}.COST.BUDGET_VS_ACTUAL_MONTH_TO_DATE')
    warehouses = [
        {
            "name": r["WAREHOUSE_NAME"], "workload": r["WORKLOAD"],
            "quota": float(r["CREDIT_QUOTA"]), "used": float(r["CREDITS_USED_MTD"]),
            "pct": float(r["PCT_OF_BUDGET_USED"]),
        }
        for r in budget_rows
    ]
    max_pct_warehouse = max(warehouses, key=lambda w: w["pct"]) if warehouses else None

    lifetime = one(f"""
        SELECT SUM(CREDITS_USED) AS TOTAL_CREDITS
        FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY
    """)
    total_credits_lifetime = float(lifetime["TOTAL_CREDITS"] or 0)

    rate_row = session.sql("""
        SELECT EFFECTIVE_RATE FROM SNOWFLAKE.ORGANIZATION_USAGE.RATE_SHEET_DAILY
        WHERE USAGE_TYPE = 'compute'
        ORDER BY DATE DESC LIMIT 1
    """).collect()
    credit_rate = float(rate_row[0]["EFFECTIVE_RATE"]) if rate_row else 0.0

    seven_day = one(f"""
        SELECT SUM(CREDITS_USED) AS N FROM {GOV}.COST.WAREHOUSE_CREDITS_DAILY
        WHERE USAGE_DATE >= DATEADD('day', -7, CURRENT_DATE())
    """)
    credits_7d = float(seven_day["N"] or 0)

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
    daily_by_warehouse = [{"date": d, "credits": credits} for d, credits in sorted(by_date.items())]

    cortex_lifetime = one(f"SELECT SUM(CREDITS) AS N FROM {GOV}.COST.CORTEX_CREDITS_DAILY")
    cortex_credits_lifetime = float(cortex_lifetime["N"] or 0)

    # ---- security ----
    open_findings_rows = rows(f'SELECT * FROM {GOV}.SECURITY.OPEN_FINDINGS')
    open_findings = [
        {
            "scanner_name": r["SCANNER_NAME"], "severity": r["SEVERITY"],
            "risk_description": r["RISK_DESCRIPTION"], "created_on": str(r["CREATED_ON"]),
        }
        for r in open_findings_rows
    ]
    open_by_severity = {}
    for f in open_findings:
        open_by_severity[f["severity"]] = open_by_severity.get(f["severity"], 0) + 1
    open_critical = open_by_severity.get("CRITICAL", 0) + open_by_severity.get("HIGH", 0)

    summary_rows = rows(f'SELECT * FROM {GOV}.SECURITY.FINDINGS_SUMMARY')
    hist = {}
    for r in summary_rows:
        sev = r["SEVERITY"]
        hist.setdefault(sev, {"severity": sev, "open": 0, "resolved": 0})
        key = "open" if r["STATE"] == "Open" else "resolved"
        hist[sev][key] += r["FINDING_COUNT"]
    findings_history = list(hist.values())

    admin_rows = rows(f'SELECT * FROM {GOV}.SECURITY.ACCOUNTADMIN_HOLDERS')
    accountadmin_users = sorted({r["USER_NAME"] for r in admin_rows})

    failed = one(f"""
        SELECT COALESCE(SUM(FAILED_ATTEMPTS), 0) AS N, COUNT(DISTINCT USER_NAME) AS U
        FROM {GOV}.SECURITY.FAILED_LOGINS_DAILY
    """)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cost": {
            "warehouses": warehouses,
            "max_pct_warehouse": max_pct_warehouse,
            "total_credits_lifetime": total_credits_lifetime,
            "total_usd_lifetime": total_credits_lifetime * credit_rate,
            "credit_rate": credit_rate,
            "credits_7d": credits_7d,
            "daily_by_warehouse": daily_by_warehouse,
            "cortex_credits_lifetime": cortex_credits_lifetime,
        },
        "security": {
            "open_findings": open_findings,
            "open_count": len(open_findings),
            "open_by_severity": open_by_severity,
            "open_critical": open_critical,
            "findings_history": findings_history,
            "accountadmin_users": accountadmin_users,
            "accountadmin_count": len(accountadmin_users),
            "failed_logins_30d": int(failed["N"]),
            "failed_login_users": int(failed["U"]),
        },
    }


data = load_dashboard_data()

template_path = Path(__file__).parent / "dashboard_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))

components.html(html, height=2400, scrolling=True)
