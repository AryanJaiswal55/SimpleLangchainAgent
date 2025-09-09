# ui.py — Streamlit front-end for Offline Campus Helpdesk Agent
import json
import streamlit as st
from app import ask, TEMPLATES_DIR, policy_fetch

st.set_page_config(page_title="Campus Helpdesk Agent)", page_icon="🎓")
st.title("🎓 Campus Helpdesk Agent")

q = st.text_input("Ask a question (e.g., 'Steps to apply for re-evaluation?')")

if st.button("Ask") and q.strip():
    with st.spinner("Working..."):
        answer, steps = ask(q)

    st.markdown("### Answer")
    # Render as markdown so headings/bullets look nice
    st.markdown(answer)

    # Optional: show a full policy expander if user asked about policy topics
    ql = q.lower()
    if any(k in ql for k in ["policy", "attendance", "plagiarism", "leave", "discipline"]):
        with st.expander("Show full policy"):
            topic = "attendance" if "attend" in ql else ("plagiarism" if "plag" in ql else "attendance")
            pol = policy_fetch(topic)
            if pol.get("found"):
                if "body" in pol:
                    st.markdown(f"## {pol['section']}")
                    st.markdown(pol["body"])
                else:
                    st.markdown(f"# {pol['title']}")
                    for sec in pol.get("sections", []):
                        st.markdown(f"## {sec['heading']}")
                        st.markdown(sec["body"])

    st.markdown("---")
    with st.expander("Evidence (tools)"):
        if isinstance(steps, list):
            for i, step in enumerate(steps, 1):
                try:
                    action, result = step
                    st.markdown(f"**Step {i}:** `{action}`")
                    st.code(json.dumps(result, indent=2), language="json")
                except Exception:
                    st.write(step)

st.markdown("---")
st.caption("Try: 'How to get transcript?', 'attendance policy', 'tuition fees', 'Steps to apply for re-evaluation?'")
