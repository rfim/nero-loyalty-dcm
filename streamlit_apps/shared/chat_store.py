"""Persistent conversation history for Nero Assistant.

Backed by NERO_GOVERNANCE.APPS_CHATBOTS.CHAT_CONVERSATIONS / CHAT_MESSAGES
(account_setup/chat_history_tables.sql). Every write uses bound
parameters, never string-interpolated SQL -- chat content is arbitrary
user input and must never be spliced into a query directly.
"""
import uuid
from datetime import datetime, timezone

CONVERSATIONS = "NERO_GOVERNANCE.APPS_CHATBOTS.CHAT_CONVERSATIONS"
MESSAGES = "NERO_GOVERNANCE.APPS_CHATBOTS.CHAT_MESSAGES"


def new_conversation_id() -> str:
    return uuid.uuid4().hex


def create_conversation(session, conversation_id: str, user_name: str, title: str):
    session.sql(
        f"INSERT INTO {CONVERSATIONS} (CONVERSATION_ID, USER_NAME, TITLE) VALUES (?, ?, ?)",
        params=[conversation_id, user_name, title[:500]],
    ).collect()


def touch_conversation(session, conversation_id: str):
    session.sql(
        f"UPDATE {CONVERSATIONS} SET UPDATED_AT = CURRENT_TIMESTAMP() WHERE CONVERSATION_ID = ?",
        params=[conversation_id],
    ).collect()


def list_conversations(session, user_name: str, limit: int = 50):
    rows = session.sql(
        f"""SELECT CONVERSATION_ID, TITLE, UPDATED_AT FROM {CONVERSATIONS}
            WHERE USER_NAME = ? ORDER BY UPDATED_AT DESC LIMIT ?""",
        params=[user_name, limit],
    ).collect()
    return [r.as_dict() for r in rows]


def load_messages(session, conversation_id: str):
    rows = session.sql(
        f"""SELECT ROLE, CONTENT, CREATED_AT FROM {MESSAGES}
            WHERE CONVERSATION_ID = ? ORDER BY CREATED_AT ASC""",
        params=[conversation_id],
    ).collect()
    return [r.as_dict() for r in rows]


def save_message(session, conversation_id: str, role: str, content: str):
    session.sql(
        f"INSERT INTO {MESSAGES} (CONVERSATION_ID, ROLE, CONTENT) VALUES (?, ?, ?)",
        params=[conversation_id, role, content],
    ).collect()
    touch_conversation(session, conversation_id)
