"""Customer Experience lead persona. Owned by NERO_CX_ROLE, granted only
the loyalty semantic view -- same data as Marketing/Ops, framed around
customer behavior rather than engagement or store performance."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import persona_core

persona_core.run({
    "title": "Nero CX Assistant",
    "persona_label": "the Customer Experience lead",
    "tagline": "Customer behavior — walk-in vs. loyalty, repeat visits — for CX.",
    "domain_description": "customer behavior: walk-in vs. loyalty transaction mix, repeat-visit patterns, and basket differences by tier",
    "tools": ["loyalty"],
    "accent": "#8a5a3d", "accent_ink": "#5f3d29", "accent_wash": "#f0e5da",
    "file_prefix": "nero_cx",
    "input_placeholder": "Ask about customer behavior, walk-ins, or repeat visits...",
    "suggestions": [
        "What share of transactions are walk-in vs. loyalty customers?",
        "Do Gold-tier customers visit more often than Bronze?",
        "How does basket size compare between loyalty and walk-in customers?",
    ],
})
