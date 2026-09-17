"""CMO / Marketing lead persona. Owned by NERO_MARKETING_ROLE, granted
only the loyalty semantic view -- no cost or security data reachable."""
import persona_core

persona_core.run({
    "title": "Nero Marketing Assistant",
    "persona_label": "the Marketing lead",
    "tagline": "Loyalty signups, tier mix and redemption trends — for Marketing.",
    "domain_description": "loyalty signups, tier distribution, redemption rates and customer engagement trends",
    "tools": ["loyalty"],
    "accent": "#a13d6b", "accent_ink": "#7a2c4f", "accent_wash": "#f3e0e9",
    "file_prefix": "nero_marketing",
    "input_placeholder": "Ask about signups, tiers, or redemption...",
    "suggestions": [
        "Which region has the most loyalty signups?",
        "How has redemption rate changed recently by store?",
        "What's the customer count by tier?",
    ],
})
