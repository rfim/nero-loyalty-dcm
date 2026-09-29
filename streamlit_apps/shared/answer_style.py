"""Answer style shared by every chatbot: the user chooses how much the agent
writes, trading answer depth for cost and speed.

- MVT: only the Minimum Viable Truth headline -- fewest output tokens.
- Story: the headline plus setup, turn and so-what (~150 words).
- Chart (independent switch): adds the data_to_chart tool, an extra agent
  step. The agent sometimes charts on its own without it, so the apps also
  hide response.chart events when the switch is off.

Measured over the same 24 questions (median): Story 142 words / 26.4 s,
MVT 34 words / 21.1 s, MVT without chart 18.6 s.

Kept in one module so all 12 apps answer the same way; persona_core.py and
the two chatbot apps only call sidebar_controls() and build_request_parts().
"""
import streamlit as st

MVT = "mvt"
STORY = "story"

STYLE_LABELS = {
    STORY: "Story — setup, turn, so what",
    MVT: "MVT — one-line answer",
}
# Figures from a 24-question run against the live agent (all personas).
STYLE_HELP = {
    STORY: "Headline plus context, what stands out, and a next step. About 140 words, ~26 s.",
    MVT: "Just the Minimum Viable Truth: about 75% fewer words and ~20% faster. Best for quick lookups.",
}

HEADLINE_RULE = (
    "Start directly with a bold line, 'Minimum Viable Truth: ...' -- nothing before it -- the single fact "
    "that answers the question, with its number. "
)
MVT_INSTRUCTION = (
    HEADLINE_RULE
    + "Write only that line, plus one short sentence if a period is incomplete or a human decision is "
    "needed. No setup, turn or so-what, and no lists. "
)
STORY_INSTRUCTION = (
    "Answer as a short data story in plain language, under about 180 words. "
    + HEADLINE_RULE
    + "Then three beats, each with its bold label: **The setup** -- one or two sentences of context (the "
    "baseline, period or scope the numbers cover). **The turn** -- what stands out (the biggest gap, change, "
    "outlier or risk), with numbers and a comparison. **So what** -- why it matters to the person you serve, "
    "and one concrete next step or decision for a human. Never invent drama: if nothing stands out, say the "
    "numbers are steady. If a period is incomplete (such as the current month), say so. For a simple lookup, "
    "keep each beat to one sentence. Put any caveat inside a beat, not in a note after the story. "
)

# Charts only make sense over Analyst result sets. data_to_chart needs no
# tool_resources entry.
CHART_TOOL_SPEC = {"type": "data_to_chart", "name": "data_to_chart"}
CHART_ORCHESTRATION = (
    "After answering a question from numeric query results, use data_to_chart to draw one simple chart of "
    "the key finding: a bar chart for comparisons, a line chart for trends over time."
)
NO_NARRATION = "Do not narrate your plan before calling tools; write only the final answer."


def chart_available(tools: list[dict]) -> bool:
    return any(t["tool_spec"]["type"] == "cortex_analyst_text_to_sql" for t in tools)


def sidebar_controls(tools: list[dict]) -> tuple[str, bool]:
    """Render the answer-style controls; returns (style, with_chart)."""
    st.markdown("### ⚙️ Answer style")
    style = st.radio("Answer style", [STORY, MVT], format_func=STYLE_LABELS.get,
                     key="answer_style", label_visibility="collapsed")
    st.caption(STYLE_HELP[style])
    with_chart = False
    if chart_available(tools):
        with_chart = st.toggle("Include a chart", value=True, key="answer_chart",
                               help="On: the agent adds a chart step (a few seconds slower). "
                                    "Off: no chart step, and no charts are shown.")
    return style, with_chart


def build_request_parts(tools: list[dict], role_instruction: str, orchestration: str,
                        style: str, with_chart: bool) -> tuple[list[dict], dict]:
    """Return (tools, instructions) for one agent request in the chosen style."""
    style_instruction = MVT_INSTRUCTION if style == MVT else STORY_INSTRUCTION
    if with_chart and chart_available(tools):
        tools = tools + [{"tool_spec": CHART_TOOL_SPEC}]
        orchestration = f"{orchestration} {CHART_ORCHESTRATION}"
    return tools, {"response": f"{role_instruction} {style_instruction}".strip(),
                   "orchestration": f"{orchestration} {NO_NARRATION}"}


def from_headline(text: str) -> str:
    """Drop the agent's pre-tool narration ("I'll look at...") so the answer
    starts at its Minimum Viable Truth headline."""
    i = text.find("**Minimum Viable Truth")
    return text[i:] if i > 0 else text
