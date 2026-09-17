"""Nero Governance Assistant — the platform-engineer/admin counterpart to
chatbot_app/nero_assistant.py's business-facing chat.

Streamlit apps in Snowflake always execute with the OWNER's privileges --
there is no per-viewer "execute as caller" mode (confirmed: `ALTER
STREAMLIT ... SET EXECUTE_AS = CALLER` is not a supported property). So a
genuinely "role-based" chatbot can't be one app that adapts per viewer --
it has to be separate app instances, each owned by a different
least-privilege role, each scoped to what that role should see. This is
the governance/admin instance: owned by NERO_GOVERNANCE_ROLE (cost budgets,
credit usage, security findings, grants, MCP audit log), deployed via a
session already authenticated as NERO_GOVERNANCE_USER -- exactly the same
ownership-transfer workaround used for nero_assistant.py, since
`GRANT OWNERSHIP ON STREAMLIT` isn't supported either.

Guardrails: identical set to nero_assistant.py -- least-privilege
execution, session rate limit, input bounds, parameterized history writes,
scoped read-only instructions, full audit trail. See that file's docstring
for the fuller rationale; not repeated here to avoid drift between two
copies of the same explanation.
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

st.set_page_config(page_title="Nero Governance Assistant", layout="wide", initial_sidebar_state="expanded")

SEMANTIC_VIEW = "NERO_GOVERNANCE.CORTEX_TOOLS.GOVERNANCE_SEMANTIC_VIEW"
SEARCH_SERVICE = "NERO_GOVERNANCE.SECURITY.FINDINGS_SEARCH_SVC"
WAREHOUSE = "NERO_BI_WH"

MAX_MESSAGES_PER_SESSION = 40
MAX_INPUT_CHARS = 2000

TOOLS = [
    {"tool_spec": {"type": "cortex_analyst_text_to_sql", "name": "governance_analyst"}},
    {"tool_spec": {"type": "cortex_search", "name": "findings_search"}},
]
TOOL_RESOURCES = {
    "governance_analyst": {
        "semantic_view": SEMANTIC_VIEW,
        "execution_environment": {"type": "warehouse", "warehouse": WAREHOUSE},
    },
    "findings_search": {"name": SEARCH_SERVICE, "max_results": 5},
}
INSTRUCTIONS = {
    "response": (
        "You are the Nero Platform governance assistant, for platform engineers "
        "and admins. Answer questions about warehouse cost/budget using "
        "governance_analyst, and security/Trust Center findings using "
        "findings_search. Be concise, cite concrete numbers, and say plainly when "
        "a question needs a human decision (e.g. changing account security "
        "settings, raising a budget, enrolling users in MFA) rather than just "
        "data -- you must never claim to have made such a change yourself. Stay "
        "within cost and security governance; decline unrelated requests. You "
        "are read-only: you can query and explain data, never modify it."
    ),
    "orchestration": (
        "Use governance_analyst for questions about warehouse credit usage, "
        "budgets, or cost by workload. Use findings_search for questions about "
        "security findings, Trust Center, MFA, network policy, or compliance. "
        "Use both if a question spans both topics."
    ),
}

# ---------------- design: steel-blue, distinct from the business assistant's
# espresso/amber identity -- a visual signal that this is the admin surface.
st.markdown("""
<style>
:root {
  --nero-ink: #1c2b3a; --nero-ink-secondary: #4c5c6b; --nero-ink-muted: #8a97a3;
  --nero-paper: #f5f6f8; --nero-accent: #3d5a73; --nero-accent-ink: #2c4256;
  --nero-wash: #eaeff3; --nero-rule: #d7dde2;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --nero-ink: #eef1f4; --nero-ink-secondary: #b6c0c9; --nero-ink-muted: #7c8894;
    --nero-paper: #14191e; --nero-accent: #8fa9bd; --nero-accent-ink: #b7cbd9;
    --nero-wash: #202a32; --nero-rule: #2c353d;
  }
}
h1#nero-gov-title { font-family: "Georgia", "Times New Roman", serif; font-style: italic; font-weight: 600;
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
- **Least-privilege, role-scoped** — this app is owned by `NERO_GOVERNANCE_ROLE`, a separate identity from the business assistant's `NERO_BI_ROLE`, with no write access anywhere except its own two history tables.
- **Rate limited** — max **{MAX_MESSAGES_PER_SESSION}** requests per session.
- **Scoped domain** — cost and security governance questions only.
- **Human-in-the-loop** — never claims to make security/config/budget changes itself.
- **Full audit trail** — every message saved with your user attribution.
        """)
    st.caption(f"Signed in as `{current_user}` (role: platform governance)")

st.markdown('<h1 id="nero-gov-title">Nero Governance Assistant</h1>', unsafe_allow_html=True)
st.markdown('<div class="nero-tagline">Cost budgets, credit usage and security findings — for platform engineers, scoped separately from the business-facing Nero Assistant.</div>', unsafe_allow_html=True)


def render_table(placeholder_key: str, columns: list[str], data_rows: list[list], question: str):
    import pandas as pd
    df = pd.DataFrame(data_rows, columns=columns)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.session_state.tables[placeholder_key] = {"question": question, "columns": columns, "rows": data_rows}
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "⬇ Excel", data=chat_export.build_table_excel(question, columns, data_rows),
            file_name="nero_governance_result.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"xlsx_{placeholder_key}",
        )
    with c2:
        st.download_button(
            "⬇ PDF", data=chat_export.build_table_pdf(question, columns, data_rows),
            file_name="nero_governance_result.pdf", mime="application/pdf",
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


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["text"])

if not st.session_state.messages:
    st.write("Try asking:")
    suggestions = [
        "Which warehouse is closest to its budget?",
        "What open security findings do we have right now?",
        "How much has CI/CD cost this month?",
    ]
    cols = st.columns(len(suggestions))
    for col, q in zip(cols, suggestions):
        if col.button(q, use_container_width=True):
            st.session_state.pending_input = q

if st.session_state.request_count >= MAX_MESSAGES_PER_SESSION:
    st.warning(f"You've reached the {MAX_MESSAGES_PER_SESSION}-request limit for this session. Start a new chat to continue.")
    prompt = None
else:
    prompt = st.chat_input("Ask about warehouse costs, budgets, or security findings...")
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
