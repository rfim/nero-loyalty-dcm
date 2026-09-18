"""Shared Caffè Nero logo asset, one source for every streamlit_apps/ app --
dashboards embed it as a base64 data URI in their HTML template header;
chat-based apps (chatbots/leaders) pass the raw bytes to st.image()."""
import base64
from pathlib import Path

LOGO_PATH = Path(__file__).parent / "assets" / "caffe_nero_logo.png"
LOGO_BYTES = LOGO_PATH.read_bytes()
LOGO_B64 = base64.b64encode(LOGO_BYTES).decode("ascii")
