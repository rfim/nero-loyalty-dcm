"""COO / Store Operations lead persona. Owned by NERO_OPS_ROLE, granted
only the loyalty semantic view (same data breadth as Marketing, different
questions -- store/basket/format operations, not engagement)."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import persona_core

persona_core.run({
    "title": "Nero Store Operations Assistant",
    "persona_label": "the Store Operations lead",
    "tagline": "Store performance, basket size and visit frequency — for Store Operations.",
    "domain_description": "store-level performance, basket size, visit frequency, and store format/region comparisons",
    "tools": ["loyalty"],
    "accent": "#4a6741", "accent_ink": "#35492f", "accent_wash": "#e8ede4",
    "file_prefix": "nero_ops",
    "input_placeholder": "Ask about store performance, baskets, or visit frequency...",
    "suggestions": [
        "Which store has the highest average basket size?",
        "How does visit frequency compare across store formats?",
        "Which region has the highest transaction count?",
    ],
})
