"""Chatbot Cost Governance Report — what the Nero Assistant chatbot itself
costs, separate from the platform-wide Cost Governance Report (governance_app/).

Keys off USER_NAME rather than AGENT_NAME: chatbot_app/nero_assistant.py
calls the Cortex Agent lite-run API with inline tools rather than
referencing NERO_PLATFORM_AGENT by name, so CORTEX_AGENT_USAGE_HISTORY's
AGENT_NAME column is always null for its traffic. A user_name matching
STPLATSTREAMLIT... is the chatbot's own Streamlit service identity --
i.e. real evidence someone used the chat UI, not just API testing.
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

st.set_page_config(page_title="Chatbot Cost Governance Report", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


@st.cache_data(ttl=300)
def load_data():
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


data = load_data()

template_path = Path(__file__).parent / "chatbot_cost_dashboard_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))
components.html(html, height=1500, scrolling=True)


def build_pdf(d: dict) -> bytes:
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


def build_excel(d: dict) -> bytes:
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


st.divider()
st.subheader("Export")
col1, col2 = st.columns(2)
today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
with col1:
    st.download_button("Download PDF report", data=build_pdf(data),
                        file_name=f"nero_chatbot_cost_report_{today_str}.pdf", mime="application/pdf")
with col2:
    st.download_button("Download Excel workbook", data=build_excel(data),
                        file_name=f"nero_chatbot_cost_report_{today_str}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
