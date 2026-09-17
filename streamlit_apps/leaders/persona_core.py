"""Shared engine for the 5 department-leader chatbots.

Five near-identical ~300-line Streamlit apps would just be five copies of
the same bugs waiting to diverge. Instead: one engine, run(config) per
persona, each persona a ~20-line entrypoint file that only says which
tools it gets and how it should talk about them. The actual data-access
boundary isn't this file at all -- it's each persona's own least-privilege
Snowflake role (account_setup/leader_personas_stack.sql), which is what
makes "role-based" real rather than a UI label. This engine only decides
which of an already-granted role's tools to wire into the agent request;
it can't grant a persona access to something its role doesn't have.

Guardrails (same as chatbot_app/nero_assistant.py, not re-derived here):
least-privilege execution (each persona owned by its own scoped role,
deployed via that role's own connection since Snowflake doesn't support
GRANT OWNERSHIP ON STREAMLIT or per-viewer execute-as-caller), session
rate limit, input bounds, parameterized history writes, scoped read-only
instructions, full audit trail.
"""
import json
from datetime import datetime, timezone

import streamlit as st
from snowflake.core import Root
from snowflake.core.cortex.lite_agent_service._generated.models.agent_run_request import AgentRunRequest
from snowflake.snowpark.context import get_active_session

import sys as _sys
from pathlib import Path as _Path
_p = _Path(__file__).resolve().parent
while not (_p / "shared").is_dir() and _p != _p.parent:
    _p = _p.parent
_sys.path.insert(0, str(_p / "shared"))

import chat_export
import chat_store

WAREHOUSE = "NERO_BI_WH"
MAX_MESSAGES_PER_SESSION = 40
MAX_INPUT_CHARS = 2000

TOOL_DEFS = {
    "loyalty": {
        "tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "loyalty_analyst"},
        "resource": {"semantic_view": "NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW",
                     "execution_environment": {"type": "warehouse", "warehouse": WAREHOUSE}},
        "resource_key": "loyalty_analyst",
        "orchestration": "Use loyalty_analyst for questions about stores, customers, transactions, baskets, redemption, or loyalty events.",
    },
    "governance": {
        "tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "governance_analyst"},
        "resource": {"semantic_view": "NERO_GOVERNANCE.CORTEX_TOOLS.GOVERNANCE_SEMANTIC_VIEW",
                     "execution_environment": {"type": "warehouse", "warehouse": WAREHOUSE}},
        "resource_key": "governance_analyst",
        "orchestration": "Use governance_analyst for questions about warehouse credit usage, budgets, or cost by workload.",
    },
    "search": {
        "tool_spec": {"type": "cortex_search", "name": "findings_search"},
        "resource": {"name": "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC", "max_results": 5},
        "resource_key": "findings_search",
        "orchestration": "Use findings_search for questions about security findings, Trust Center, MFA, network policy, or compliance.",
    },
}


def _build_tools_and_resources(tool_keys: list[str]):
    tools, resources = [], {}
    for key in tool_keys:
        d = TOOL_DEFS[key]
        tools.append({"tool_spec": d["tool_spec"]})
        resources[d["resource_key"]] = d["resource"]
    return tools, resources


def _inject_css(accent: str, accent_ink: str, accent_wash: str, title_id: str):
    st.markdown(f"""
<style>
:root {{
  --nero-ink: #2b211c; --nero-ink-secondary: #6b5d52; --nero-ink-muted: #9c8f83;
  --nero-paper: #faf8f5; --nero-accent: {accent}; --nero-accent-ink: {accent_ink}; --nero-wash: {accent_wash};
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --nero-ink: #f3ece3; --nero-ink-secondary: #cbbdae; --nero-ink-muted: #8a7c6e;
    --nero-paper: #1a1512;
  }}
}}
h1#{title_id} {{ font-family: "Georgia", "Times New Roman", serif; font-style: italic; font-weight: 600;
  color: var(--nero-accent-ink); font-size: 30px; margin-bottom: 0; }}
.nero-tagline {{ color: var(--nero-ink-secondary); font-size: 13.5px; margin-top: 2px; margin-bottom: 18px; }}
[data-testid="stChatMessage"] {{ border-radius: 14px; padding: 4px 6px; }}
[data-testid="stChatMessageAvatarUser"] {{ background: var(--nero-accent) !important; }}
[data-testid="stChatMessageAvatarAssistant"] {{ background: var(--nero-wash) !important; }}
</style>
""", unsafe_allow_html=True)


def run(config: dict):
    """config keys: title, tagline, accent, accent_ink, accent_wash, tools
    (list of 'loyalty'/'governance'/'search'), domain_description,
    suggestions (list[str]), file_prefix (for downloads)."""
    st.set_page_config(page_title=config["title"], layout="wide", initial_sidebar_state="expanded")
    title_id = "nero-persona-title"
    _inject_css(config["accent"], config["accent_ink"], config["accent_wash"], title_id)

    tools, tool_resources = _build_tools_and_resources(config["tools"])
    orchestration = " ".join(TOOL_DEFS[k]["orchestration"] for k in config["tools"])
    instructions = {
        "response": (
            f"You are the {config['title']}, for {config['persona_label']}. Answer questions about "
            f"{config['domain_description']}. Be concise, cite concrete numbers, and say plainly when "
            "a question needs a human decision rather than just data -- you must never claim to have "
            "made a change yourself. Stay within your domain; decline unrelated requests. You are "
            "read-only: you can query and explain data, never modify it."
        ),
        "orchestration": orchestration,
    }

    session = get_active_session()
    root = Root(session)
    current_user = session.sql("SELECT CURRENT_USER() AS U").collect()[0]["U"]

    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = chat_store.new_conversation_id()
        st.session_state.conversation_created = False
        st.session_state.messages = []
        st.session_state.tables = {}
    if "request_count" not in st.session_state:
        st.session_state.request_count = 0

    def start_new_conversation():
        st.session_state.conversation_id = chat_store.new_conversation_id()
        st.session_state.conversation_created = False
        st.session_state.messages = []
        st.session_state.tables = {}
        st.session_state.request_count = 0

    def load_conversation(conversation_id: str):
        st.session_state.conversation_id = conversation_id
        st.session_state.conversation_created = True
        st.session_state.messages = [
            {"role": m["ROLE"], "text": m["CONTENT"]} for m in chat_store.load_messages(session, conversation_id)
        ]
        st.session_state.tables = {}

    with st.sidebar:
        st.markdown("### 💬 Conversations")
        if st.button("➕ New chat", use_container_width=True):
            start_new_conversation()
            st.rerun()
        for conv in chat_store.list_conversations(session, current_user):
            label = conv["TITLE"] or "Untitled conversation"
            is_current = conv["CONVERSATION_ID"] == st.session_state.conversation_id
            if st.button(("• " if is_current else "") + label[:40], key=f"conv_{conv['CONVERSATION_ID']}", use_container_width=True):
                load_conversation(conv["CONVERSATION_ID"])
                st.rerun()
        st.divider()
        with st.expander("🛡️ Guardrails in effect"):
            st.markdown(f"""
- **Role-scoped** — this app is owned by a role granted only the tools listed below; it cannot reach any other Nero data.
- **Tools available**: {', '.join(config['tools'])}
- **Rate limited** — max **{MAX_MESSAGES_PER_SESSION}** requests per session.
- **Human-in-the-loop** — never claims to make config changes itself.
- **Full audit trail** — every message saved with your user attribution.
            """)
        st.caption(f"Signed in as `{current_user}`")

    st.markdown(f'<h1 id="{title_id}">{config["title"]}</h1>', unsafe_allow_html=True)
    st.markdown(f'<div class="nero-tagline">{config["tagline"]}</div>', unsafe_allow_html=True)

    def render_table(placeholder_key: str, columns: list[str], data_rows: list[list], question: str):
        import pandas as pd
        df = pd.DataFrame(data_rows, columns=columns)
        st.dataframe(df, use_container_width=True, hide_index=True)
        c1, c2 = st.columns(2)
        with c1:
            st.download_button("⬇ Excel", data=chat_export.build_table_excel(question, columns, data_rows),
                                file_name=f"{config['file_prefix']}_result.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                key=f"xlsx_{placeholder_key}")
        with c2:
            st.download_button("⬇ PDF", data=chat_export.build_table_pdf(question, columns, data_rows),
                                file_name=f"{config['file_prefix']}_result.pdf", mime="application/pdf",
                                key=f"pdf_{placeholder_key}")

    def render_chart(chart_spec_str: str):
        try:
            spec = json.loads(chart_spec_str)
            st.vega_lite_chart(spec.get("data", {}).get("values", []), spec, use_container_width=True)
        except Exception:
            pass

    def run_agent(history: list[dict], question: str, msg_index: int):
        req = AgentRunRequest(messages=history, tools=tools, tool_resources=tool_resources, instructions=instructions)
        resp = root.cortex_agent_service.run(req)
        text_placeholder = st.empty()
        status_placeholder = st.empty()
        accumulated_text = ""
        table_idx = 0
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
                rs = payload["result_set"]
                cols = [c["name"] for c in rs["resultSetMetaData"]["rowType"]]
                render_table(f"{msg_index}_{table_idx}", cols, rs["data"], question)
                table_idx += 1
            elif event.event == "response.chart":
                render_chart(payload["chart_spec"])
            elif event.event == "error":
                status_placeholder.empty()
                st.error(payload.get("message", "The assistant hit an error."))
        status_placeholder.empty()
        return accumulated_text

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])

    if not st.session_state.messages:
        st.write("Try asking:")
        cols = st.columns(len(config["suggestions"]))
        for col, q in zip(cols, config["suggestions"]):
            if col.button(q, use_container_width=True):
                st.session_state.pending_input = q

    if st.session_state.request_count >= MAX_MESSAGES_PER_SESSION:
        st.warning(f"You've reached the {MAX_MESSAGES_PER_SESSION}-request limit for this session. Start a new chat to continue.")
        prompt = None
    else:
        prompt = st.chat_input(config["input_placeholder"])
        if "pending_input" in st.session_state:
            prompt = st.session_state.pop("pending_input")

    if prompt is not None:
        prompt = prompt.strip()
        if not prompt:
            prompt = None
        elif len(prompt) > MAX_INPUT_CHARS:
            st.error(f"That message is too long ({len(prompt)} characters, max {MAX_INPUT_CHARS}). Please shorten it.")
            prompt = None

    if prompt:
        if not st.session_state.conversation_created:
            chat_store.create_conversation(session, st.session_state.conversation_id, current_user, prompt[:80])
            st.session_state.conversation_created = True

        st.session_state.messages.append({"role": "user", "text": prompt})
        chat_store.save_message(session, st.session_state.conversation_id, "user", prompt)
        st.session_state.request_count += 1

        with st.chat_message("user"):
            st.markdown(prompt)

        agent_history = [
            {"role": m["role"], "content": [{"type": "text", "text": m["text"]}]}
            for m in st.session_state.messages
        ]
        with st.chat_message("assistant"):
            answer = run_agent(agent_history, prompt, len(st.session_state.messages))

        st.session_state.messages.append({"role": "assistant", "text": answer})
        chat_store.save_message(session, st.session_state.conversation_id, "assistant", answer)
        st.rerun()
