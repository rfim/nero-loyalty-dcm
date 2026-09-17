"""Cost Governance Report — formal, exportable Streamlit-in-Snowflake app.

Deeper than the earlier combined Cost & Security Watch app: adds
month-to-date spend, a linear month-end forecast, cost attribution by role,
and a Cortex usage breakdown — all still reading NERO_GOVERNANCE.COST live.
Exports the same data as a formal PDF (reportlab) and a multi-sheet Excel
workbook (xlsxwriter) via report_common.py.
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

import report_common as rc

st.set_page_config(page_title="Cost Governance Report", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


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
        },
    }


data = load_cost_data()

template_path = Path(__file__).parent / "cost_dashboard_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))
components.html(html, height=2100, scrolling=True)


# ---------------- exports ----------------

def build_cost_pdf(d: dict) -> bytes:
    c = d["cost"]
    budget_rows = [[w["name"], w["workload"], f"{w['quota']:.2f}", f"{w['used']:.3f}", f"{w['pct']:.1f}%"] for w in c["warehouses"]]
    role_rows = [[r["warehouse"], r["role"], f"{r['query_count']:,}", f"{r['execution_hours']:.2f}", f"{r['cloud_services_credits']:.3f}"] for r in c["role_attribution"]] or [["—", "No query activity in the last 30 days", "", "", ""]]
    cortex_rows = [[r["source"], f"{r['credits']:.3f}", f"{r['tokens']:,}", f"{r['requests']:,}"] for r in c["cortex_by_source"]] or [["—", "No Cortex usage recorded", "", ""]]
    wu_rows = [[r["warehouse"], r["user"], f"{r['query_count']:,}", f"{r['credits']:.3f}"] for r in c["warehouse_user_costs"]] or [["—", "No data yet — QUERY_ATTRIBUTION_HISTORY lags up to ~24h", "", ""]]

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
    }
    return rc.write_excel_workbook(
        title="Cost Governance Report",
        subtitle="Nero Platform — warehouse and Cortex compute spend against budget",
        sheets=sheets,
    )


st.divider()
st.subheader("Export")
col1, col2 = st.columns(2)
today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
with col1:
    st.download_button(
        "Download PDF report", data=build_cost_pdf(data),
        file_name=f"nero_cost_governance_report_{today_str}.pdf", mime="application/pdf",
    )
with col2:
    st.download_button(
        "Download Excel workbook", data=build_cost_excel(data),
        file_name=f"nero_cost_governance_report_{today_str}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
