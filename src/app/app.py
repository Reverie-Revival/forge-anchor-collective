import streamlit as st
from dotenv import load_dotenv
load_dotenv()

st.set_page_config(page_title="Forge Anchor", layout="wide", page_icon="⚓")

st.markdown("""
<style>
[data-testid="metric-container"] {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 18px 20px 14px 20px;
}
[data-testid="stSidebar"] > div:first-child { padding-top: 1rem; }
.grade-badge {
    display: inline-block;
    padding: 6px 18px;
    border-radius: 20px;
    font-size: 0.9rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    margin-bottom: 4px;
}
.section-label {
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #888;
    margin: 0 0 8px 0;
}
.config-group-header {
    font-size: 0.75rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #aaa;
    margin-top: 12px;
    margin-bottom: 2px;
}
.stream-ref-label {
    font-size: 0.75rem;
    font-weight: 700;
    color: #ccc;
    margin-bottom: 2px;
}
</style>
""", unsafe_allow_html=True)

pg = st.navigation([
    st.Page("stream_tester.py", title="Stream Tester", icon="📡"),
    st.Page("pages/1_model_tester.py", title="Model Tester", icon="🏆"),
    st.Page("pages/2_live_monitor.py", title="Live Monitor", icon="🔴"),
])
pg.run()
