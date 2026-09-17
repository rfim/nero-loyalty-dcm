"""Internal Audit / Compliance lead persona. Owned by NERO_AUDIT_ROLE,
granted only findings_search -- same tool as the Security lead, framed
around audit/compliance posture rather than technical remediation."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

import persona_core

persona_core.run({
    "title": "Nero Audit Assistant",
    "persona_label": "the Internal Audit / Compliance lead",
    "tagline": "Compliance posture from Trust Center findings — for Internal Audit.",
    "domain_description": "compliance and audit posture from Trust Center findings: what's open, what's resolved, and what needs sign-off",
    "tools": ["search"],
    "accent": "#5a4a6b", "accent_ink": "#3f334a", "accent_wash": "#ece7f0",
    "file_prefix": "nero_audit",
    "input_placeholder": "Ask about compliance findings or audit posture...",
    "suggestions": [
        "What findings are open that need sign-off?",
        "Are there any findings related to authentication policy?",
        "Summarize our current compliance posture.",
    ],
})
