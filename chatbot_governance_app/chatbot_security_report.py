"""Chatbot Security Governance Report — who can reach the Nero Assistant's
tools and who has actually called them, separate from the platform-wide
Security & Horizon Governance Report (governance_app/).

Access review runs live SHOW GRANTS ON, not a cached ACCOUNT_USAGE view --
GRANTS_TO_ROLES lags several hours, and "who can reach this right now" is
worth getting exactly right rather than approximately right.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from snowflake.snowpark.context import get_active_session

import report_common as rc

st.set_page_config(page_title="Chatbot Security Governance Report", layout="wide")

session = get_active_session()
GOV = "NERO_GOVERNANCE"

CHATBOT_OBJECTS = [
    ("AGENT", "NERO_GOVERNANCE.APPS.NERO_PLATFORM_AGENT"),
    ("MCP SERVER", "NERO_GOVERNANCE.APPS.NERO_PLATFORM_MCP_SERVER"),
    ("CORTEX SEARCH SERVICE", "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC"),
    ("SEMANTIC VIEW", "NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW"),
]


def rows(sql: str):
    return [r.as_dict() for r in session.sql(sql).collect()]


@st.cache_data(ttl=120)
def load_data():
    grants = []
    for obj_type, obj_name in CHATBOT_OBJECTS:
        try:
            for r in rows(f"SHOW GRANTS ON {obj_type} {obj_name}"):
                grants.append({
                    "object_name": obj_name, "object_type": obj_type,
                    "privilege": r["privilege"], "grantee": r["grantee_name"],
                })
        except Exception:
            continue

    mcp_registered = False
    try:
        mcp_rows = rows("SHOW MCP SERVERS IN ACCOUNT")
        mcp_registered = any(r["name"] == "NERO_PLATFORM_MCP_SERVER" for r in mcp_rows)
    except Exception:
        pass

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

    nonstandard = [g for g in d["grants"] if g["grantee"] not in ("ACCOUNTADMIN", "NERO_BI_ROLE", "NERO_COPILOT_ROLE") and g["privilege"] != "OWNERSHIP"]

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
