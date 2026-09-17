"""CFO / Finance lead persona. Owned by NERO_FINANCE_ROLE, the only
leader persona granted BOTH semantic views -- revenue and platform cost
are both finance's concern."""
import persona_core

persona_core.run({
    "title": "Nero Finance Assistant",
    "persona_label": "the Finance lead",
    "tagline": "Sales revenue and platform compute cost — for Finance.",
    "domain_description": "sales revenue, transaction volume, basket value, and platform compute cost/budget by workload",
    "tools": ["loyalty", "governance"],
    "accent": "#1f6b5c", "accent_ink": "#164a3f", "accent_wash": "#dceee9",
    "file_prefix": "nero_finance",
    "input_placeholder": "Ask about revenue, transactions, or platform cost...",
    "suggestions": [
        "What's total sales revenue by store?",
        "Which warehouse workload costs the most?",
        "How does average basket value compare across regions?",
    ],
})
