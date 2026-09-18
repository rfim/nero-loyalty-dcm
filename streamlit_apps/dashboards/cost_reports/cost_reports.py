"""Cost Reports — combined Streamlit-in-Snowflake app, replacing the two
separate Cost Governance Report and Chatbot Cost Governance Report apps
with one app, two tabs. Same data, same PDF/Excel exports per tab; just
one place to land instead of two.

Tab 1 (Platform Cost): warehouse + Cortex compute spend against budget,
attribution by role/user, monthly spend & estimate. Reads
NERO_GOVERNANCE.COST live.

Tab 2 (Chatbot Cost): what the Nero Assistant chatbot itself costs, by
caller. Keys off USER_NAME rather than AGENT_NAME: nero_assistant.py calls
the Cortex Agent lite-run API with inline tools rather than referencing
NERO_PLATFORM_AGENT by name, so CORTEX_AGENT_USAGE_HISTORY's AGENT_NAME
column is always null for its traffic. A user_name matching
STPLATSTREAMLIT... is the chatbot's own Streamlit service identity --
real evidence someone used the chat UI, not just API testing.
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

st.set_page_config(page_title="Cost Reports", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


def one(sql: str):
    return session.sql(sql).collect()[0].as_dict()


# ==================================================================
# Tab 1: Platform Cost
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
# Tab 2: Chatbot Cost
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
# Layout: two tabs, one app
# ==================================================================

today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
tab_platform, tab_chatbot = st.tabs(["Platform Cost", "Chatbot Cost"])

with tab_platform:
    cost_data = load_cost_data()
    cost_data["logo_b64"] = branding.LOGO_B64
    html = (Path(__file__).parent / "cost_dashboard_template.html").read_text().replace(
        "__DATA_JSON__", json.dumps(cost_data)
    )
    components.html(html, height=2100, scrolling=True)

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

with tab_chatbot:
    chatbot_data = load_chatbot_cost_data()
    chatbot_data["logo_b64"] = branding.LOGO_B64
    html2 = (Path(__file__).parent / "chatbot_cost_dashboard_template.html").read_text().replace(
        "__DATA_JSON__", json.dumps(chatbot_data)
    )
    components.html(html2, height=1500, scrolling=True)

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
