"""Guess the Truth: a quiz mode shared by the chatbots.

The user commits to a guess first, then the agent reveals the real answer
from live data as a short story with a chart, and a "why it matters" lesson
follows. Committing first is what makes it engaging; having the reveal
correct a gut feeling with a real number is what builds awareness.

Three topics: business performance, platform cost, and data-quality
caveats (when a number shouldn't be trusted yet). Each question names the
tool it needs, so a persona only gets questions its role can answer.

The truth is read from the agent's result table, not its prose, so
scoring doesn't depend on wording. If the agent returns no usable table,
the round is shown unscored rather than marked wrong.
"""
import random
import re
from dataclasses import dataclass, field

import streamlit as st

BUSINESS, COST, QUALITY = "Business", "Cost", "Data quality"

REGIONS = ["London", "North West", "South East", "Midlands", "Yorkshire", "South West", "Scotland", "North East"]
TIERS = ["Bronze", "Silver", "Gold"]
FORMATS = ["High street", "Travel", "Drive-thru"]
WORKLOADS = ["Reporting / BI", "dbt transforms", "CI/CD", "Ingestion"]


@dataclass(frozen=True)
class Question:
    id: str
    topic: str
    tool: str                  # TOOL_DEFS key the question needs: "loyalty" or "governance"
    text: str                  # shown to the user
    ask: str                   # sent to the agent
    kind: str                  # "pick": choose one option; "number": estimate a value
    lesson: str                # shown after the reveal
    options: list = field(default_factory=list)
    pick: str = "max"          # "pick" questions: the row with the max or min value wins
    unit: str = ""             # "number" questions: "%" or ""
    max_value: int = 100       # "number" questions: upper bound of the guess input
    tolerance: tuple = (0.10, 0.25)  # relative error for full / half points ("%": absolute points)
    metric: tuple = ()         # words in the result column that holds the answer, e.g. ("pct", "percent")


QUESTIONS = [
    Question("region-sales", BUSINESS, "loyalty", "Which region has the highest total sales?",
             "What are total sales by region over the full transaction window? Return a table with exactly "
             "two columns: region and total sales.",
             "pick", options=REGIONS, metric=("sales",),
             lesson="Regional totals mostly follow how many stores a region has. Before ranking regions, "
                    "compare sales per store, or you reward size rather than performance."),
    Question("tier-basket", BUSINESS, "loyalty", "Which loyalty tier has the highest average basket?",
             "What is the average basket value by loyalty tier? Return a table with exactly two columns: "
             "tier and average basket.",
             "pick", options=TIERS, metric=("basket",),
             lesson="Tier gaps in basket size are often pennies. If higher tiers don't spend more per visit, "
                    "their value comes from visiting more often, which is a different lever."),
    Question("format-basket", BUSINESS, "loyalty", "Which store format has the highest average basket?",
             "What is the average basket value by store format? Return a table with exactly two columns: "
             "store format and average basket.",
             "pick", options=FORMATS, metric=("basket",),
             lesson="Formats can look different on total sales but near-identical per basket. Total sales "
                    "track footfall; basket size tracks what each visit is worth."),
    Question("loyalty-share", BUSINESS, "loyalty",
             "What percentage of transactions are made by loyalty members (not walk-ins)?",
             "What percentage of transactions have a loyalty customer, rather than being walk-in baskets? "
             "Return a table with exactly one column: loyalty share as a percentage.",
             "number", unit="%", tolerance=(5, 12), metric=("pct", "percent", "share"),
             lesson="Every walk-in basket is a customer the loyalty programme can't see or reach. This share "
                    "is the ceiling on how much of the business loyalty data can explain."),
    Question("customer-count", BUSINESS, "loyalty", "How many loyalty customers are enrolled?",
             "How many loyalty customers are enrolled? Return a table with exactly one column: customer count.",
             "number", max_value=5000, metric=("count", "customers"),
             lesson="Enrolled isn't the same as active. A count of members says nothing about how many "
                    "of them came back this month."),
    Question("workload-budget", COST, "governance",
             "Which workload has used the largest share of its monthly credit budget so far this month?",
             "For each workload, what share of its monthly credit budget has been used so far this month? "
             "Return a table with exactly two columns: workload and percentage of budget used.",
             "pick", options=WORKLOADS, metric=("pct", "percent", "share", "ratio"),
             lesson="The workload with the most credits isn't always the one closest to its limit; budgets "
                    "differ. Watch the percentage of budget, not the raw spend."),
    Question("least-credits", COST, "governance", "Which workload has used the fewest credits this month?",
             "How many credits has each workload used so far this month? Return a table with exactly two "
             "columns: workload and credits used.",
             "pick", options=WORKLOADS, pick="min", metric=("credit",),
             lesson="A workload far under budget is a chance to right-size it. Budgets that are never "
                    "approached hide money that could be planned elsewhere."),
    Question("budget-used", COST, "governance",
             "What percentage of the total monthly credit budget has been used so far this month?",
             "Across all workloads, what percentage of the total monthly credit budget has been used so far "
             "this month? Return a table with exactly one column: percentage of total budget used.",
             "number", unit="%", tolerance=(5, 12), metric=("pct", "percent", "share"),
             lesson="Month-to-date spend only means something next to how far through the month you are. "
                    "30% used on day 25 is very different from 30% used on day 5."),
    Question("days-this-month", QUALITY, "loyalty",
             "On how many distinct days this month does the transaction data have any transactions?",
             "On how many distinct days in the current calendar month does the transaction data have at "
             "least one transaction? Return a table with exactly one column: number of days.",
             "number", max_value=31, tolerance=(0.0, 0.15), metric=("day",),
             lesson="If the data doesn't cover the whole month, a month-to-date total looks like a drop. "
                    "Always check the date range before comparing a partial month with a full one."),
    Question("history-days", QUALITY, "loyalty",
             "How many days of transaction history does the data hold, from earliest to latest?",
             "How many days are there between the earliest and the latest transaction date in the data? "
             "Return a table with exactly one column: number of days.",
             "number", max_value=400, metric=("day",),
             lesson="A rolling window means old history silently disappears. 'All time' in this data only "
                    "goes back as far as the window, so year-on-year questions can't be answered."),
    Question("freshness", QUALITY, "loyalty", "How many days old is the most recent transaction in the data?",
             "How many days ago was the most recent transaction date, compared with today? Return a table "
             "with exactly one column: days since the latest transaction.",
             "number", max_value=60, tolerance=(0.0, 0.5), metric=("day",),
             lesson="Fresh-looking dashboards can be built on stale data. Knowing how old the newest row is "
                    "tells you whether 'today' in a report really means today."),
]


def available_questions(tool_keys) -> list[Question]:
    return [q for q in QUESTIONS if q.tool in set(tool_keys)]


def mode_selector(tool_keys) -> bool:
    """Sidebar Ask / Play switch; returns True in Play mode. Personas with no
    game questions (search-only roles) don't see it."""
    if not available_questions(tool_keys):
        return False
    mode = st.radio("Mode", ["💬 Ask", "🎯 Play: Guess the Truth"], key="app_mode", horizontal=True,
                    label_visibility="collapsed")
    return mode != "💬 Ask"


def audited_ask(run_agent, session, current_user):
    """Wrap a one-shot agent call so every game round is saved to chat history
    (same audit trail as the chat) and counts toward the session rate limit.
    run_agent(history) must return (text, attachments)."""
    import chat_store

    def ask(prompt: str):
        if "game_conversation_id" not in st.session_state:
            st.session_state.game_conversation_id = chat_store.new_conversation_id()
            chat_store.create_conversation(session, st.session_state.game_conversation_id, current_user,
                                           "🎯 Guess the Truth")
        conversation_id = st.session_state.game_conversation_id
        chat_store.save_message(session, conversation_id, "user", prompt)
        st.session_state.request_count = st.session_state.get("request_count", 0) + 1
        text, attachments = run_agent([{"role": "user", "content": [{"type": "text", "text": prompt}]}])
        chat_store.save_message(session, conversation_id, "assistant", text)
        return text, attachments

    return ask


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").replace("£", "").strip())
    except ValueError:
        return None


def _metric_col(q: Question, columns, numeric_cols):
    """The numeric column named like the question's metric, else the first numeric one."""
    for c in numeric_cols:
        if any(h in _norm(columns[c]) for h in q.metric):
            return c
    return numeric_cols[0] if numeric_cols else None


def _first_table(attachments):
    return next((a for a in attachments if a["kind"] == "table" and a["rows"]), None)


def _match_option(label, options):
    n = _norm(label)
    for o in options:
        if _norm(o) == n:
            return o
    for o in options:  # e.g. "Reporting/BI workload" vs "Reporting / BI"
        if _norm(o) in n or (n and n in _norm(o)):
            return o
    return None


def find_truth(q: Question, text: str, attachments: list):
    """Return the true answer (an option, or a number), or None if it can't be read."""
    table = _first_table(attachments)
    if q.kind == "pick":
        if table:
            rows = table["rows"]
            ncols = len(table["columns"])
            label_col = next((c for c in range(ncols) if any(_to_float(r[c]) is None for r in rows)), None)
            numeric = [c for c in range(ncols) if c != label_col and all(_to_float(r[c]) is not None for r in rows)]
            value_col = _metric_col(q, table["columns"], numeric)
            if label_col is not None and value_col is not None:
                best = (max if q.pick == "max" else min)(rows, key=lambda r: _to_float(r[value_col]))
                found = _match_option(best[label_col], q.options)
                if found:
                    return found
        headline = text.split("\n", 1)[0]  # fall back to the option the headline names
        named = [o for o in q.options if _norm(o) and _norm(o) in _norm(headline)]
        return named[0] if len(named) == 1 else None
    value = None
    if table:
        row = table["rows"][0]
        col = _metric_col(q, table["columns"], [c for c in range(len(row)) if _to_float(row[c]) is not None])
        value = _to_float(row[col]) if col is not None else None
    if value is None:
        m = re.search(r"-?\d[\d,]*\.?\d*", text.split("\n", 1)[0])
        value = _to_float(m.group()) if m else None
    if value is not None and q.unit == "%" and 0 < value <= 1:
        value *= 100  # a share returned as a fraction
    return value


def score(q: Question, guess, truth) -> tuple[float, str]:
    """Points (1, 0.5 or 0) and a verdict for one guess."""
    if q.kind == "pick":
        return (1.0, "Correct!") if guess == truth else (0.0, "Not quite.")
    error = abs(guess - truth)
    if q.unit != "%":
        error = error / max(abs(truth), 1)
    full, half = q.tolerance
    if error <= full:
        return 1.0, "Spot on!"
    if error <= half:
        return 0.5, "Close!"
    return 0.0, "Not quite."


def _fmt(q: Question, v) -> str:
    if q.kind == "pick":
        return str(v)
    return f"{v:,.1f}%" if q.unit == "%" else f"{v:,.0f}"


def _state():
    if "game" not in st.session_state:
        st.session_state.game = {"score": 0.0, "rounds": 0, "streak": 0, "best": 0, "asked": [],
                                 "current": None, "result": None}
    return st.session_state.game


def _next_question(game, questions):
    unasked = [q for q in questions if q.id not in game["asked"]] or questions
    game["current"] = random.choice(unasked).id
    game["result"] = None


def render(tool_keys, ask_agent, render_attachments, request_allowed: bool):
    """Draw the game. ask_agent(prompt) runs one agent call, rendering it live,
    and returns (text, attachments); render_attachments(key, attachments,
    question) redraws stored tables and charts."""
    questions = available_questions(tool_keys)
    game = _state()

    st.markdown("### 🎯 Guess the Truth")
    st.caption("Make your call first, then see what the data says. Questions cover business performance, "
               "platform cost, and when a number shouldn't be trusted yet.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Score", f"{game['score']:g} / {game['rounds']}")
    c2.metric("Streak", game["streak"])
    c3.metric("Best streak", game["best"])

    if game["current"] is None:
        _next_question(game, questions)
    q = next(x for x in questions if x.id == game["current"])

    st.markdown(f"**{q.topic}** · {q.text}")
    result = game["result"]

    if result is None:
        if q.kind == "pick":
            guess = st.radio("Your guess", q.options, index=None, key=f"guess_{q.id}_{game['rounds']}")
        elif q.unit == "%":
            guess = st.slider("Your guess (%)", 0, 100, 50, key=f"guess_{q.id}_{game['rounds']}")
        else:
            guess = st.number_input("Your guess", min_value=0, max_value=q.max_value, value=None, step=1,
                                    key=f"guess_{q.id}_{game['rounds']}")
        if not request_allowed:
            st.warning("You've reached this session's request limit. Start a new chat to keep playing.")
            return
        if st.button("Lock in my guess", type="primary", disabled=guess is None):
            with st.spinner("Checking the data..."):
                text, attachments = ask_agent(q.ask)
            truth = find_truth(q, text, attachments)
            points, verdict = score(q, guess, truth) if truth is not None else (None, "Couldn't score this one.")
            game["result"] = {"guess": guess, "truth": truth, "points": points, "verdict": verdict,
                              "text": text, "attachments": attachments}
            game["asked"].append(q.id)
            if points is not None:
                game["rounds"] += 1
                game["score"] += points
                game["streak"] = game["streak"] + 1 if points == 1 else 0
                game["best"] = max(game["best"], game["streak"])
            st.rerun()
        return

    if result["points"] is None:
        st.info(f"You guessed **{_fmt(q, result['guess'])}**. The answer couldn't be scored automatically; "
                "here's what the data says.")
    else:
        icon = {1.0: "✅", 0.5: "🟡"}.get(result["points"], "❌")
        st.markdown(f"#### {icon} {result['verdict']} The answer is **{_fmt(q, result['truth'])}**; "
                    f"you guessed **{_fmt(q, result['guess'])}**.")
    st.markdown(result["text"])
    render_attachments(f"game_{len(game['asked'])}", result["attachments"], q.text)
    st.info(f"**Why it matters.** {q.lesson}")
    if st.button("Next question", type="primary"):
        _next_question(game, questions)
        st.rerun()
