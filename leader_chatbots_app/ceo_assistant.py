"""CEO persona. Owned by NERO_CEO_ROLE, the only persona granted all three
tools -- full cross-domain read access."""
import persona_core

persona_core.run({
    "title": "Nero CEO Assistant",
    "persona_label": "the CEO",
    "tagline": "Loyalty, sales, platform cost, and security — the full picture.",
    "domain_description": "loyalty/sales performance, platform compute cost, and security posture, across the whole business",
    "tools": ["loyalty", "governance", "search"],
    "accent": "#4a3a2b", "accent_ink": "#2e2318", "accent_wash": "#ece5dc",
    "file_prefix": "nero_ceo",
    "input_placeholder": "Ask about sales, cost, or security — anything across the business...",
    "suggestions": [
        "Give me a quick overview: sales, cost, and any open security findings.",
        "Which store is driving the most revenue?",
        "Are we close to any warehouse budget limit?",
    ],
})
