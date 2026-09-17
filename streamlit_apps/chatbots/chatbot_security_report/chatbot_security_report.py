"""Chatbot Security Governance Report — who can reach the Nero Assistant's
tools and who has actually called them, separate from the platform-wide
Security & Horizon Governance Report (governance_app/).

Access review reads NERO_GOVERNANCE.SECURITY.ROLE_GRANTS_INVENTORY (an
ACCOUNT_USAGE.GRANTS_TO_ROLES wrapper, lags up to a few hours) rather than
live SHOW GRANTS ON. That's a deliberate trade: SHOW GRANTS ON an object
requires either owning it or holding the account-wide MANAGE GRANTS
privilege, which only ACCOUNTADMIN-equivalent roles have -- granting that
to this app's role just to get a real-time access review would defeat the
point of a least-privilege reporting role. A few hours of staleness on an
audit view is the right trade against handing an app MANAGE GRANTS.
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

st.set_page_config(page_title="Chatbot Security Governance Report", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"

CHATBOT_OBJECT_NAMES = [
    "NERO_PLATFORM_AGENT", "NERO_PLATFORM_MCP_SERVER",
    "FINDINGS_SEARCH_SVC", "LOYALTY_SEMANTIC_VIEW", "GOVERNANCE_SEMANTIC_VIEW",
]


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


@st.cache_data(ttl=120)
def load_data():
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


data = load_data()

template_path = Path(__file__).parent / "chatbot_security_dashboard_template.html"
html = template_path.read_text().replace("__DATA_JSON__", json.dumps(data))
components.html(html, height=1700, scrolling=True)


def build_pdf(d: dict) -> bytes:
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
            rc.caption("Access review runs live SHOW GRANTS ON at report generation time, not a cached ACCOUNT_USAGE snapshot."),
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


def build_excel(d: dict) -> bytes:
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


st.divider()
st.subheader("Export")
col1, col2 = st.columns(2)
today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
with col1:
    st.download_button("Download PDF report", data=build_pdf(data),
                        file_name=f"nero_chatbot_security_report_{today_str}.pdf", mime="application/pdf")
with col2:
    st.download_button("Download Excel workbook", data=build_excel(data),
                        file_name=f"nero_chatbot_security_report_{today_str}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
