"""Regional Director persona. Owned by NERO_REGIONAL_ROLE, granted loyalty
+ search -- the first persona combining store/region performance with
security awareness, for a leader responsible for physical store regions."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import persona_core

persona_core.run({
    "title": "Nero Regional Assistant",
    "persona_label": "the Regional Director",
    "tagline": "Store and region performance, plus security awareness — for Regional Directors.",
    "domain_description": "store and region performance (sales, baskets, loyalty engagement by region) and security/Trust Center findings",
    "tools": ["loyalty", "search"],
    "accent": "#3d6b4a", "accent_ink": "#2a4a33", "accent_wash": "#e3ece6",
    "file_prefix": "nero_regional",
    "input_placeholder": "Ask about regional performance or security findings...",
    "suggestions": [
        "Which region has the highest sales revenue?",
        "How does redemption rate compare across regions?",
        "What open security findings should regional leadership know about?",
    ],
})
