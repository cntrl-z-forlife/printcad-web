"""printcad-web: password-protected Streamlit demo of the printcad text-to-STEP pipeline.

Set environment variables:
  OPENROUTER_API_KEY   (required; use a limited, time-expiring key)
  PRINTCAD_PASSWORD    (optional; defaults to 'demo' for local testing)
  PRINTCAD_MODEL       (optional; default openrouter/free)

Deploy with the OPENROUTER_API_KEY as a secret, never committed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import streamlit as st

# Make the local printcad package importable
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

PASSWORD = os.environ.get("PRINTCAD_PASSWORD", "demo")
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = os.environ.get("PRINTCAD_MODEL", "openrouter/free")

st.set_page_config(page_title="printcad Web Demo", layout="wide")

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.title("printcad Web Demo")
    st.caption("Password-protected demo of the text → build123d → STEP pipeline.")
    pwd = st.text_input("Password", type="password", key="pwd")
    if st.button("Enter"):
        if pwd == PASSWORD:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")
    st.stop()

st.title("printcad")
st.caption("Natural language → build123d script + parameters → STEP. Second-take edits keep model.py and params.json as source of truth.")

if not API_KEY:
    st.warning("OPENROUTER_API_KEY is not set. Generation will fail. Set it in the deployment secrets.")

if "session_dir" not in st.session_state:
    st.session_state.session_dir = None

prompt = st.text_area(
    "Part description",
    height=120,
    placeholder="FDM L-bracket 80x60 mm, 5 mm thick, two M4 clearance holes per leg",
)

col1, col2 = st.columns([1, 3])
with col1:
    generate = st.button("Generate STEP", type="primary", disabled=not API_KEY or not prompt.strip())

def _run_printcad(mode: str, text: str, session: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["OPENROUTER_API_KEY"] = API_KEY
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable,
        "-m",
        "printcad",
        mode,
        text,
        "--provider",
        "openrouter",
        "--model",
        MODEL,
        "--api-key",
        API_KEY,
        "--session",
        str(session),
        "--assume-defaults",
        "--max-iters",
        "14",
    ]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        cwd=str(ROOT),
    )

if generate and prompt.strip():
    # Fresh session for a new part
    if st.session_state.session_dir and Path(st.session_state.session_dir).exists():
        shutil.rmtree(st.session_state.session_dir, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="printcad-web-")
    st.session_state.session_dir = tmp
    session = Path(tmp) / "session"
    session.mkdir()
    with st.spinner("Running printcad agent (may try multiple free models)..."):
        try:
            proc = _run_printcad("new", prompt.strip(), session)
        except subprocess.TimeoutExpired:
            st.error("Generation timed out after 10 minutes.")
            st.stop()
        st.session_state.last_log = (proc.stdout or "") + (proc.stderr or "")
        st.session_state.last_returncode = proc.returncode

session = Path(st.session_state.session_dir) / "session" if st.session_state.session_dir else None
if session and session.exists():
    st.subheader("Agent log")
    st.code(st.session_state.get("last_log", ""), language="text")
    step = session / "out" / "part.step"
    preview_html = session / "out" / "preview.html"
    model_py = session / "model.py"
    params = session / "params.json"
    if st.session_state.get("last_returncode") == 0 and step.exists():
        st.success("STEP generated successfully.")
        if preview_html.exists():
            st.subheader("Preview")
            html = preview_html.read_text()
            st.components.v1.html(html, height=600, scrolling=True)
        with st.expander("Download artifacts"):
            if step.exists():
                st.download_button(
                    "Download part.step",
                    data=step.read_bytes(),
                    file_name="part.step",
                    mime="application/octet-stream",
                )
            if model_py.exists():
                st.download_button(
                    "Download model.py",
                    data=model_py.read_text(),
                    file_name="model.py",
                    mime="text/plain",
                )
            if params.exists():
                st.download_button(
                    "Download params.json",
                    data=params.read_text(),
                    file_name="params.json",
                    mime="application/json",
                )
        st.subheader("Second take")
        edit_prompt = st.text_input("Edit request")
        if st.button("Apply edit") and edit_prompt.strip():
            with st.spinner("Applying edit..."):
                try:
                    edit_proc = _run_printcad("edit", edit_prompt.strip(), session)
                except subprocess.TimeoutExpired:
                    st.error("Edit timed out.")
                    st.stop()
                st.session_state.last_log = (edit_proc.stdout or "") + (edit_proc.stderr or "")
                st.session_state.last_returncode = edit_proc.returncode
                st.rerun()
    else:
        st.error(f"Generation failed (exit {st.session_state.get('last_returncode')}). See log above.")

st.sidebar.header("About")
st.sidebar.markdown(
    """
This is a thin web wrapper around [printcad](https://github.com/your-org/printcad).

- Uses free OpenRouter models with tool calling.
- Source of truth remains `model.py` + `params.json`.
- STEP is intended for Fusion 360 finishing.
- Use a **limited, time-expiring** OpenRouter key.
- Keep the repo **private**.
    """
)
st.sidebar.caption(f"Model: `{MODEL}`")
