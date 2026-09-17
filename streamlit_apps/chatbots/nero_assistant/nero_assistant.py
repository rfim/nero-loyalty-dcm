"""Nero Assistant — chat interface over a real Cortex Agent, with
persistent history, per-result report export, and explicit guardrails.

Guardrails in effect (see the "Guardrails" panel in the sidebar for the
user-facing summary):
  1. Least-privilege execution: this app is owned by NERO_BI_ROLE, a
     read-only role (SELECT/REFERENCES/USAGE only, no DDL/DML grants
     anywhere) -- deployed via a session already authenticated as
     NERO_BI_USER so ownership is assigned correctly at creation time
     (GRANT OWNERSHIP ON STREAMLIT is not supported by Snowflake, so
     this is the only way to get a non-ACCOUNTADMIN owner). Even a
     successful prompt-injection against Cortex Analyst's generated SQL
     can only ever SELECT.
  2. Rate limiting: a hard cap on requests per browser session.
  3. Input bounds: length-capped, trimmed, rejected if empty.
  4. Parameterized SQL for all history writes (chat_store.py) -- chat
     content is untrusted input and is never spliced into a query.
  5. Scoped, explicit system instructions: refuses to take destructive
     action, stays in the loyalty/security domain, says plainly when a
     question needs a human decision.
  6. Full audit trail: every message is persisted
     (NERO_GOVERNANCE.APPS_CHATBOTS.CHAT_MESSAGES) with user attribution.
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

st.set_page_config(page_title="Nero Assistant", layout="wide", initial_sidebar_state="expanded")

SEMANTIC_VIEW = "NERO_DB.NERO_LOYALTY.LOYALTY_SEMANTIC_VIEW"
SEARCH_SERVICE = "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC"
WAREHOUSE = "NERO_BI_WH"

MAX_MESSAGES_PER_SESSION = 40
MAX_INPUT_CHARS = 2000

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
        "security settings, enrolling users in MFA, editing network policies) "
        "rather than just data -- you must never claim to have made such a change "
        "yourself. Stay within the loyalty, sales and security-governance domain; "
        "decline unrelated requests. You are read-only: you can query and explain "
        "data, never modify it."
    ),
    "orchestration": (
        "Use loyalty_analyst for questions about stores, customers, transactions, "
        "baskets, redemption, or loyalty events. Use findings_search for questions "
        "about security findings, Trust Center, MFA, network policy, or compliance. "
        "Use both if a question spans both topics."
    ),
}

# ---------------- design ----------------
st.markdown("""
<style>
:root {
  --nero-ink: #2b211c; --nero-ink-secondary: #6b5d52; --nero-ink-muted: #9c8f83;
  --nero-paper: #faf8f5; --nero-accent: #a8631c; --nero-accent-ink: #7a4712;
  --nero-wash: #f3e6d4; --nero-rule: #e4dcd2;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --nero-ink: #f3ece3; --nero-ink-secondary: #cbbdae; --nero-ink-muted: #8a7c6e;
    --nero-paper: #1a1512; --nero-accent: #d9924a; --nero-accent-ink: #efc292;
    --nero-wash: #2a2016; --nero-rule: #3a2f26;
  }
}
h1#nero-title { font-family: "Georgia", "Times New Roman", serif; font-style: italic; font-weight: 600;
  color: var(--nero-accent-ink); font-size: 30px; margin-bottom: 0; }
.nero-tagline { color: var(--nero-ink-secondary); font-size: 13.5px; margin-top: 2px; margin-bottom: 18px; }
[data-testid="stChatMessage"] { border-radius: 14px; padding: 4px 6px; }
[data-testid="stChatMessageAvatarUser"] { background: var(--nero-accent) !important; }
[data-testid="stChatMessageAvatarAssistant"] { background: var(--nero-wash) !important; }
.nero-badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:10.5px; font-weight:600;
  letter-spacing:.03em; text-transform:uppercase; background: var(--nero-wash); color: var(--nero-accent-ink); margin-right: 6px; }
</style>
""", unsafe_allow_html=True)

session = get_active_session()
root = Root(session)
current_user = session.sql("SELECT CURRENT_USER() AS U").collect()[0]["U"]


# ---------------- history persistence ----------------
def ensure_conversation():
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = chat_store.new_conversation_id()
        st.session_state.conversation_created = False
        st.session_state.messages = []
        st.session_state.tables = {}


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


ensure_conversation()
if "request_count" not in st.session_state:
    st.session_state.request_count = 0

# ---------------- sidebar ----------------
with st.sidebar:
    st.markdown("### 💬 Conversations")
    if st.button("➕ New chat", use_container_width=True):
        start_new_conversation()
        st.rerun()

    past = chat_store.list_conversations(session, current_user)
    for conv in past:
        label = conv["TITLE"] or "Untitled conversation"
        is_current = conv["CONVERSATION_ID"] == st.session_state.conversation_id
        if st.button(("• " if is_current else "") + label[:40], key=f"conv_{conv['CONVERSATION_ID']}", use_container_width=True):
            load_conversation(conv["CONVERSATION_ID"])
            st.rerun()

    st.divider()
    with st.expander("🛡️ Guardrails in effect"):
        st.markdown(f"""
- **Read-only execution** — this app runs under a least-privilege role with no write access anywhere; it can query and explain data, never change it.
- **Rate limited** — max **{MAX_MESSAGES_PER_SESSION}** requests per session.
- **Scoped domain** — answers loyalty, sales and security-governance questions only; declines the rest.
- **Human-in-the-loop** — never claims to make security/config changes itself.
- **Full audit trail** — every message is saved with your user attribution.
        """)
    st.caption(f"Signed in as `{current_user}`")

# ---------------- header ----------------
st.markdown('<h1 id="nero-title">Nero Assistant</h1>', unsafe_allow_html=True)
st.markdown('<div class="nero-tagline">Ask about loyalty &amp; sales data or security findings — powered by a real Cortex Agent, the same tools exposed over MCP.</div>', unsafe_allow_html=True)


def render_table(placeholder_key: str, columns: list[str], data_rows: list[list], question: str):
    import pandas as pd
    df = pd.DataFrame(data_rows, columns=columns)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.session_state.tables[placeholder_key] = {"question": question, "columns": columns, "rows": data_rows}
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇ Excel", data=chat_export.build_table_excel(question, columns, data_rows),
            file_name="nero_assistant_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xlsx_{placeholder_key}",
        )
    with c2:
        st.download_button(
            "⬇ PDF", data=chat_export.build_table_pdf(question, columns, data_rows),
            file_name="nero_assistant_result.pdf", mime="application/pdf",
            key=f"pdf_{placeholder_key}",
        )


def render_chart(chart_spec_str: str):
    try:
        spec = json.loads(chart_spec_str)
        st.vega_lite_chart(spec.get("data", {}).get("values", []), spec, use_container_width=True)
    except Exception:
        pass


def run_agent(history: list[dict], question: str, msg_index: int):
    req = AgentRunRequest(messages=history, tools=TOOLS, tool_resources=TOOL_RESOURCES, instructions=INSTRUCTIONS)
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


# ---------------- chat rendering ----------------
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])

if not st.session_state.messages:
    st.write("Try asking:")
    suggestions = [
        "Which store has the highest redemption rate?",
        "Do Gold-tier customers have a bigger basket than Bronze?",
        "What open security findings do we have right now?",
    ]
    cols = st.columns(len(suggestions))
    for col, q in zip(cols, suggestions):
        if col.button(q, use_container_width=True):
            st.session_state.pending_input = q

# ---------------- guardrail: rate limit ----------------
if st.session_state.request_count >= MAX_MESSAGES_PER_SESSION:
    st.warning(f"You've reached the {MAX_MESSAGES_PER_SESSION}-request limit for this session. Start a new chat to continue.")
    prompt = None
else:
    prompt = st.chat_input("Ask about stores, loyalty, sales, or security findings...")
    if "pending_input" in st.session_state:
        prompt = st.session_state.pop("pending_input")

# ---------------- guardrail: input bounds ----------------
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
