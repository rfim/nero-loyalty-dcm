"""Nero Assistant — chat interface over a real Cortex Agent.

Not a hand-rolled router: this calls Snowflake's actual Cortex Agent "lite
run" API (snowflake.core.Root().cortex_agent_service.run), which does its
own tool-choice reasoning between two real tools:

  - loyalty_analyst  (cortex_analyst_text_to_sql) -> NERO_DB.NERO_LOYALTY.
    LOYALTY_SEMANTIC_VIEW -- structured NL-to-SQL over stores/customers/
    transactions/loyalty events.
  - findings_search  (cortex_search) -> NERO_GOVERNANCE.SECURITY.
    FINDINGS_SEARCH_SVC -- semantic search over Trust Center security
    findings (materialized into FINDINGS_DOCS, since Cortex Search needs
    change tracking on a real table, not a shared system view).

The same two tools are also exposed to external MCP clients via
NERO_GOVERNANCE.APPS.NERO_PLATFORM_MCP_SERVER (account_setup/
cortex_chatbot_stack.sql) -- this app and any MCP client share the exact
same underlying tools, just through different front doors.
"""
import json

import streamlit as st
from snowflake.core import Root
from snowflake.core.cortex.lite_agent_service._generated.models.agent_run_request import AgentRunRequest
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Nero Assistant", layout="centered")

SEMANTIC_VIEW = "NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW"
SEARCH_SERVICE = "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC"
WAREHOUSE = "NERO_BI_WH"

TOOLS = [
    {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "loyalty_analyst"}},
    {"tool_spec": {"type": "cortex_search", "name": "findings_search"}},
]
TOOL_RESOURCES = {
    "loyalty_analyst": {
        "semantic_view": SEMANTIC_VIEW,
        "execution_environment": {"type": "warehouse", "warehouse": WAREHOUSE},
    },
    "findings_search": {"name": SEARCH_SERVICE, "max_results": 5},
}
INSTRUCTIONS = {
    "response": (
        "You are the Nero Platform assistant. Answer questions about loyalty/sales "
        "data using loyalty_analyst, and questions about security/Trust Center "
        "findings using findings_search. Be concise, cite concrete numbers, and say "
        "plainly when a question needs a human decision (e.g. changing account "
        "security settings) rather than just data."
    ),
    "orchestration": (
        "Use loyalty_analyst for questions about stores, customers, transactions, "
        "baskets, redemption, or loyalty events. Use findings_search for questions "
        "about security findings, Trust Center, MFA, network policy, or compliance. "
        "Use both if a question spans both topics."
    ),
}

session = get_active_session()
root = Root(session)


def render_table(result_set: dict):
    cols = [c["name"] for c in result_set["resultSetMetaData"]["rowType"]]
    rows = result_set["data"]
    st.dataframe([dict(zip(cols, r)) for r in rows], use_container_width=True, hide_index=True)


def render_chart(chart_spec_str: str):
    try:
        spec = json.loads(chart_spec_str)
        st.vega_lite_chart(spec.get("data", {}).get("values", []), spec, use_container_width=True)
    except Exception:
        pass


def run_agent(history: list[dict]):
    """history: list of {"role": ..., "content": [{"type": "text", "text": ...}]}"""
    req = AgentRunRequest(
        messages=history,
        tools=TOOLS,
        tool_resources=TOOL_RESOURCES,
        instructions=INSTRUCTIONS,
    )
    resp = root.cortex_agent_service.run(req)

    text_placeholder = st.empty()
    status_placeholder = st.empty()
    accumulated_text = ""
    suggested_queries = []

    for event in resp.events():
        if not event.data:
            continue
        try:
            payload = json.loads(event.data)
        except (json.JSONDecodeError, TypeError):
            continue

        if event.event == "response.status":
            status_placeholder.caption(f"🔄 {payload.get('message', '')}")
        elif event.event == "response.text.delta":
            accumulated_text += payload.get("text", "")
            text_placeholder.markdown(accumulated_text)
        elif event.event == "response.table":
            status_placeholder.empty()
            render_table(payload["result_set"])
        elif event.event == "response.chart":
            render_chart(payload["chart_spec"])
        elif event.event == "response.suggested_queries":
            suggested_queries = [q["query"] for q in payload.get("suggested_queries", [])]
        elif event.event == "error":
            status_placeholder.empty()
            st.error(payload.get("message", "The assistant hit an error."))

    status_placeholder.empty()
    return accumulated_text, suggested_queries


st.title("💬 Nero Assistant")
st.caption(
    "Ask about loyalty & sales data (Cortex Analyst) or security findings (Cortex Search) — "
    "answered by a real Cortex Agent, the same tools exposed over MCP to external clients."
)

if "messages" not in st.session_state:
    st.session_state.messages = []
if "suggested_queries" not in st.session_state:
    st.session_state.suggested_queries = [
        "Which store has the highest redemption rate?",
        "Do Gold-tier customers have a bigger basket than Bronze?",
        "What open security findings do we have right now?",
    ]

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])

if st.session_state.suggested_queries and not st.session_state.messages:
    st.write("Try asking:")
    cols = st.columns(len(st.session_state.suggested_queries))
    for col, q in zip(cols, st.session_state.suggested_queries):
        if col.button(q, use_container_width=True):
            st.session_state.pending_input = q

prompt = st.chat_input("Ask about stores, loyalty, sales, or security findings...")
if "pending_input" in st.session_state:
    prompt = st.session_state.pop("pending_input")

if prompt:
    st.session_state.messages.append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    agent_history = [
        {"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
        for m in st.session_state.messages
    ]

    with st.chat_message("assistant"):
        answer, suggestions = run_agent(agent_history)

    st.session_state.messages.append({"role": "assistant", "text": answer})
    if suggestions:
        st.session_state.suggested_queries = suggestions
    st.rerun()
