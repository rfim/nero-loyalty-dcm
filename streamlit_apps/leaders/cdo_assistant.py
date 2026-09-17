"""Chief Data Officer / Data & Analytics lead persona. Owned by
NERO_CDO_ROLE, granted loyalty + governance -- same tool combination as
Finance, but framed around data-platform health rather than P&L."""
import persona_core

persona_core.run({
    "title": "Nero Data Assistant",
    "persona_label": "the Chief Data Officer",
    "tagline": "Data platform health and business data — for the CDO.",
    "domain_description": "loyalty/sales data (as a proxy for data quality and coverage) and platform compute cost by workload",
    "tools": ["loyalty", "governance"],
    "accent": "#3d5a5a", "accent_ink": "#2a3f3f", "accent_wash": "#e3ecec",
    "file_prefix": "nero_cdo",
    "input_placeholder": "Ask about data coverage, volume, or platform cost...",
    "suggestions": [
        "How many transactions and loyalty events do we have on file?",
        "Which warehouse workload consumes the most credits?",
        "How many stores and customers are in the data?",
    ],
})
