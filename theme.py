"""
Colour schemes. 'upstox' recolours the whole portal in Upstox's palette, sampled from upstox.com's own stylesheets
(brand purple #542087 with #61399f / #7132b0 / #37135b / #1d0733, green #31c090 / #148564, red #e75f53 / #d53627,
amber #ffb400), on a deep purple-black background. 'classic' is the original navy Kite-style look.

ui_kit and the app write colours inline, so instead of editing every component the scheme is applied by mapping the
classic hex / rgba values to Upstox ones in the HTML, CSS and Plotly figures this session renders (st.html,
st.markdown with HTML, st.plotly_chart). The wrappers read the scheme from st.session_state, so each viewer's choice
only affects their own session.
"""
import json
import re

import plotly.graph_objects as go
import streamlit as st

SCHEMES = {"upstox": "Upstox (purple)", "classic": "Classic (navy)"}
DEFAULT = "upstox"

_HEX = {  # classic -> Upstox
    # backgrounds and surfaces
    "#0b1220": "#140b1d", "#0f1a2e": "#1d1029", "#14243f": "#261537", "#111a2e": "#1d1029", "#0e1a2e": "#190d24",
    "#0c172a": "#170c21", "#13233f": "#24143a", "#131f36": "#2a1840", "#1e293b": "#2a1a3d", "#263247": "#3a2552",
    "#2b3b5c": "#48306a", "#111c31": "#1d1029",
    # lines
    "#1e2d4a": "#36234f", "#1f2937": "#2a1b3a",
    # text
    "#e6edf7": "#f1edf7", "#8ea0bf": "#a99bbd", "#cbd5e1": "#d9cfe6", "#94a3b8": "#a99bbd", "#64748b": "#7d6f92",
    "#475569": "#5e4f73",
    # accents (sky blue -> Upstox purple)
    "#38bdf8": "#9b6bd6", "#4184f3": "#8a5cc7", "#64b5f6": "#b18be6", "#7dd3fc": "#c7a8f0",
    # greens
    "#22c55e": "#31c090", "#4caf50": "#31c090", "#4ade80": "#5fd3aa", "#16a34a": "#148564", "#15803d": "#148564",
    "#2e7d32": "#0f6b50", "#a5d6a7": "#9fe3c9", "#26a69a": "#31c090",
    # reds
    "#ef4444": "#e75f53", "#ef5350": "#e75f53", "#f87171": "#f08a80", "#dc2626": "#d53627", "#b91c1c": "#c63224",
    "#c62828": "#c63224", "#7f1d1d": "#6e1f17", "#ef9a9a": "#f2b0a9",
    # amber
    "#f59e0b": "#ffb400", "#fbbf24": "#ffc94d", "#eab308": "#ffb400",
}
_RGBA = {  # rgba(r,g,b, ...) prefixes
    "56,189,248": "155,107,214", "34,197,94": "49,192,144", "239,68,68": "231,95,83", "76,175,80": "49,192,144",
    "239,83,80": "231,95,83", "245,158,11": "255,180,0", "148,163,184": "169,155,189", "234,179,8": "255,180,0",
    "74,222,128": "95,211,170", "248,113,113": "240,138,128", "65,132,243": "138,92,199",
}
_HEX_RE = re.compile("|".join(sorted(map(re.escape, _HEX), key=len, reverse=True)), re.I)
_RGBA_RE = re.compile(r"rgba?\(\s*(" + "|".join(re.escape(k).replace(",", r"\s*,\s*") for k in _RGBA) + r")", re.I)

NATIVE = {  # Streamlit's own widgets (config.toml sets the base; this follows the chosen scheme)
    "upstox": """<style>
.stApp{background:#140b1d}
[data-testid="stSidebar"]{background:#1a0f26;border-right:1px solid #36234f}
[data-testid="stHeader"]{background:rgba(20,11,29,.85)}
button[kind="primary"],[data-testid="stBaseButton-primary"]{background:#542087!important;border-color:#542087!important;color:#fff!important}
button[kind="primary"]:hover,[data-testid="stBaseButton-primary"]:hover{background:#61399f!important;border-color:#61399f!important}
[data-baseweb="tab-highlight"]{background-color:#9b6bd6!important}
.stTabs [aria-selected="true"] p{color:#c7a8f0!important}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{background:#22142f!important;border-color:#36234f!important}
[data-testid="stExpander"] details{border-color:#36234f}
[data-testid="stMetricDelta"] svg{display:none}
[role="slider"]{background:#9b6bd6!important}
a{color:#b18be6}
</style>""",
    "classic": """<style>
.stApp{background:#0b1220}
[data-testid="stSidebar"]{background:#111c31}
[data-testid="stHeader"]{background:rgba(11,18,32,.85)}
[data-baseweb="input"],[data-baseweb="select"]>div,[data-baseweb="textarea"]{background:#111c31!important}
[data-baseweb="tab-highlight"]{background-color:#22c55e!important}
button[kind="primary"],[data-testid="stBaseButton-primary"]{background:#22c55e!important;border-color:#22c55e!important;color:#0b1220!important}
</style>""",
}


def current():
    return st.session_state.get("color_scheme", DEFAULT)


def recolor(text):
    """Map classic colours to the active scheme's (no-op for classic)."""
    if current() == "classic" or not isinstance(text, str):
        return text
    text = _HEX_RE.sub(lambda m: _HEX[m.group(0).lower()], text)
    return _RGBA_RE.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + _RGBA[re.sub(r"\s", "", m.group(1))], text)


def _fig(fig):
    if current() == "classic" or not isinstance(fig, go.Figure):
        return fig
    try:
        return go.Figure(json.loads(recolor(fig.to_json())))
    except Exception:
        return fig


def install():
    """Wrap html / markdown / plotly_chart on every container (st.*, columns, sidebar, tabs) once per process; each
    call recolours according to the viewing session's scheme."""
    from streamlit.delta_generator import DeltaGenerator as DG
    if getattr(DG, "_scheme_wrapped", False):
        return
    html, md, pc = DG.html, DG.markdown, DG.plotly_chart
    DG.html = lambda self, body, *a, **k: html(self, recolor(body), *a, **k)
    DG.markdown = lambda self, body, *a, **k: md(self, recolor(body) if k.get("unsafe_allow_html") else body, *a, **k)
    DG.plotly_chart = lambda self, fig, *a, **k: pc(self, _fig(fig), *a, **k)
    DG._scheme_wrapped = True
    main = st._main  # the st.* shortcuts were bound before the patch: rebind them
    st.html, st.markdown, st.plotly_chart = main.html, main.markdown, main.plotly_chart


def apply_native():
    """Inject the Streamlit-widget CSS for the session's scheme (call once per run, after install)."""
    st.markdown(NATIVE[current()], unsafe_allow_html=True)
