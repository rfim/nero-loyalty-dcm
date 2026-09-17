"""CISO / Security lead persona. Owned by NERO_SECURITY_LEAD_ROLE, granted
ONLY the security findings search -- no business or cost data reachable
at all, the narrowest of the 5 personas."""
import persona_core

persona_core.run({
    "title": "Nero Security Assistant",
    "persona_label": "the Security lead (CISO)",
    "tagline": "Security and Trust Center findings only — for the Security lead.",
    "domain_description": "security and Trust Center findings: MFA readiness, network policy coverage, client CVEs, and compliance",
    "tools": ["search"],
    "accent": "#6b3d43", "accent_ink": "#4a2a2e", "accent_wash": "#f3eaeb",
    "file_prefix": "nero_security_lead",
    "input_placeholder": "Ask about security or Trust Center findings...",
    "suggestions": [
        "What critical security findings are open?",
        "Are there any findings related to MFA?",
        "What's the status of network policy coverage?",
    ],
})
