"""CTO / Platform lead persona. Owned by NERO_PLATFORM_LEAD_ROLE, granted
platform cost AND security posture -- engineering-oriented, no business
loyalty/sales data."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import persona_core

persona_core.run({
    "title": "Nero Platform Assistant",
    "persona_label": "the Platform lead (CTO)",
    "tagline": "Platform cost and security posture — for the Platform lead.",
    "domain_description": "platform compute cost/budget by workload, and security/Trust Center findings",
    "tools": ["governance", "search"],
    "accent": "#3d5a73", "accent_ink": "#2c4256", "accent_wash": "#eaeff3",
    "file_prefix": "nero_platform_lead",
    "input_placeholder": "Ask about platform cost, budgets, or security posture...",
    "suggestions": [
        "Which warehouse is closest to its budget?",
        "What open security findings do we have right now?",
        "How much has CI/CD cost this month?",
    ],
})
