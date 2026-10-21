# printcad-web

Static, browser-only demo of the printcad text-to-STEP pipeline.

Everything runs client-side:

- OpenRouter API calls (you paste your own limited key)
- Agent tool loop (JavaScript)
- Geometry: Pyodide + [OCP.wasm](https://github.com/yeicor/OCP.wasm) so `build123d` executes in the browser
- Three.js preview of the tessellated solid
- STEP download



## Run locally

```bash
# any static server
python -m http.server 8000
# open http://localhost:8000
```

1. Paste an OpenRouter key.
2. Click “Initialize CAD engine” (downloads Pyodide + build123d/OCP.wasm; first load is slow).
3. Describe a part and click Generate.
4. Second-take edits reuse the same session (params + model.py).


## What is still Python

Only the generated `model.py` and the one-time execution of it inside Pyodide. The harness, tool dispatch, OpenRouter client, session state, and UI are plain JavaScript.

## Limitations

- First load of OCP.wasm is large and slow.
- Inspect is simplified (bbox / success) compared with the CLI’s prompt-vs-STEP checks.
- `ask_user` applies defaults.
- Browser CORS and OpenRouter rate limits apply.
- Not a full replacement for the CLI’s multi-model fallback and strict linting.


