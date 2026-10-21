/* printcad browser agent — OpenRouter tool calling + Pyodide/OCP.wasm build123d */

const SYSTEM = `You are printcad, an engineering CAD agent.
Goal: turn a user's text into a watertight build123d solid and export STEP for Fusion 360 finishing.
Units are millimeters. Never invent inches.

Workflow for a NEW part:
1. If the user named a product (phone, console, camera), call lookup_device.
2. If required sizes are missing, call ask_user once (assume defaults if needed). Then keep modeling.
3. set_spec
4. set_params with every dimension named
5. write_model
6. run_model
7. inspect
8. If inspect fails, apply_patch or rewrite and run again
9. finish — list assumptions. ok=false if inspect failed.

Workflow for a SECOND TAKE / edit:
- Dimensional change only → set_params (merge) then run_model then inspect
- Small feature change → apply_patch on model.py then run_model then inspect
- Do not regenerate model.py from scratch if params or a patch will do

model.py contract:
- Define exactly: def build(params: dict):
- Read EVERY size from params[name]
- Return one solid
- Units in params are millimeters
- from build123d import * does NOT define bare X/Y/Z. Use Axis.Z
- Prefer builder style. Always return part.part or a Shape
- Sit the part on the XY plane: lowest Z = 0
- Keep model.py under ~150 lines

Print-aware defaults: FDM walls >= 1.6 mm, clearance holes slightly oversized (M4 ~3.4–3.5 mm).
When inspect reports errors, fix the code. Call finish when the STEP is good or when you cannot proceed.
`;

const TOOLS = [
  {
    type: "function",
    function: {
      name: "set_spec",
      description: "Create or replace the engineering spec. Units always millimeters.",
      parameters: {
        type: "object",
        properties: {
          summary: { type: "string" },
          process: { type: "string" },
          envelope_mm: { type: "array", items: { type: "number" }, minItems: 3, maxItems: 3 },
          print_orientation: { type: "string" },
          family: { type: "string" },
          notes: { type: "array", items: { type: "string" } },
          constraints: { type: "array", items: { type: "string" } },
        },
        required: ["summary"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "set_params",
      description: "Create or merge named numeric parameters. Prefer this for dimensional changes.",
      parameters: {
        type: "object",
        properties: {
          values: { type: "object", additionalProperties: { type: "number" } },
          units: { type: "object", additionalProperties: { type: "string" } },
          replace: { type: "boolean" },
        },
        required: ["values"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_model",
      description: "Write the full build123d model.py. Must define build(params: dict) and return a single solid.",
      parameters: {
        type: "object",
        properties: { code: { type: "string" } },
        required: ["code"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "apply_patch",
      description: "Replace an exact substring in model.py.",
      parameters: {
        type: "object",
        properties: {
          old_str: { type: "string" },
          new_str: { type: "string" },
        },
        required: ["old_str", "new_str"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_model",
      description: "Execute model.py with current params, export STEP, and return a report.",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "inspect",
      description: "Return the last run report (bbox, validity, errors).",
      parameters: { type: "object", properties: {} },
    },
  },
  {
    type: "function",
    function: {
      name: "lookup_device",
      description: "Look up published outer dimensions for a named phone.",
      parameters: {
        type: "object",
        properties: { query: { type: "string" } },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "ask_user",
      description: "Ask clarifying questions. In this demo defaults are applied.",
      parameters: {
        type: "object",
        properties: {
          questions: { type: "array", items: { type: "string" } },
          assumptions: { type: "array", items: { type: "string" } },
        },
        required: ["questions"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "finish",
      description: "Stop the tool loop and report the result.",
      parameters: {
        type: "object",
        properties: {
          summary: { type: "string" },
          ok: { type: "boolean" },
          follow_up: { type: "string" },
        },
        required: ["summary", "ok"],
      },
    },
  },
];

const DEVICES = {
  "iphone 12 mini": { length: 131.5, width: 64.2, thickness: 7.4, corner_r: 10.0 },
  "iphone 12": { length: 146.7, width: 71.5, thickness: 7.4, corner_r: 11.0 },
  "iphone 13 mini": { length: 131.5, width: 64.2, thickness: 7.65, corner_r: 10.0 },
  "iphone 13": { length: 146.7, width: 71.5, thickness: 7.65, corner_r: 11.0 },
  "iphone 14": { length: 146.7, width: 71.5, thickness: 7.8, corner_r: 11.0 },
  "iphone 15": { length: 147.6, width: 71.6, thickness: 7.8, corner_r: 11.0 },
  "iphone 16": { length: 147.6, width: 71.6, thickness: 7.8, corner_r: 11.0 },
};

let pyodide = null;
let session = {
  spec: null,
  params: {},
  modelCode: "",
  report: null,
  stepBytes: null,
  mesh: null,
};

function log(msg) {
  const el = document.getElementById("log");
  el.textContent += (el.textContent ? "\n" : "") + msg;
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById("log").textContent = "";
}

async function initPyodide() {
  const status = document.getElementById("initStatus");
  status.textContent = "Loading Pyodide…";
  try {
    pyodide = await loadPyodide({ indexURL: "https://cdn.jsdelivr.net/pyodide/v0.29.5/full/" });
    status.textContent = "Loading micropip…";
    await pyodide.loadPackage("micropip");
    status.textContent = "Installing OCP.wasm + build123d (may take a minute)…";
    // 7.8.1.2 WASM wheel matches build123d 0.10.x (cadquery-ocp>=7.8,<7.9).
    // Newer build123d wants cadquery-ocp-novtk>=8.0, but only a 7.9.3 WASM wheel exists.
    await pyodide.runPythonAsync(`
import micropip
await micropip.install("https://yeicor.github.io/OCP.wasm/cadquery-ocp/cadquery_ocp-7.8.1.2-cp313-cp313-pyodide_2025_0_wasm32.whl")
await micropip.install("build123d==0.10.0")
`);
    status.textContent = "CAD engine ready";
    document.getElementById("runBtn").disabled = false;
    document.getElementById("editBtn").disabled = false;
    log("Pyodide + OCP.wasm + build123d ready.");
  } catch (err) {
    status.textContent = "Failed: " + err;
    log("Init error: " + err);
    console.error(err);
  }
}

function lookupDevice(query) {
  const q = query.toLowerCase().trim();
  if (DEVICES[q]) return { found: true, device: { key: q, ...DEVICES[q] }, suggested_case_params: caseParams(DEVICES[q]) };
  for (const [key, data] of Object.entries(DEVICES)) {
    if (key.includes(q) || q.includes(key)) {
      return { found: true, device: { key, ...data }, suggested_case_params: caseParams(data) };
    }
  }
  return { found: false, hint: "unknown device; do not guess body size" };
}

function caseParams(device, wall = 2.0, clearance = 0.4, lip = 1.2) {
  return {
    device_length: device.length,
    device_width: device.width,
    device_thickness: device.thickness,
    corner_r: device.corner_r || 8.0,
    clearance,
    wall,
    lip,
    inner_length: device.length + 2 * clearance,
    inner_width: device.width + 2 * clearance,
    outer_length: device.length + 2 * clearance + 2 * wall,
    outer_width: device.width + 2 * clearance + 2 * wall,
    outer_height: device.thickness + clearance + wall + lip,
  };
}

async function runModelInPyodide() {
  if (!pyodide) throw new Error("CAD engine not initialized");
  if (!session.modelCode) throw new Error("no model.py");
  // Pass data safely
  pyodide.globals.set("user_params", session.params);
  pyodide.globals.set("user_code", session.modelCode);
  const py = `
import json, io, traceback
params = user_params
ns = {}
try:
    exec(user_code, ns)
except Exception as e:
    raise RuntimeError("exec failed: " + str(e))
if "build" not in ns:
    raise RuntimeError("model.py must define build(params)")
try:
    solid = ns["build"](params)
except Exception as e:
    raise RuntimeError("build() failed: " + str(e))
if solid is None:
    raise RuntimeError("build returned None")
solid = getattr(solid, "part", solid)
if hasattr(solid, "solid"):
    try:
        maybe = solid.solid()
        if maybe is not None:
            solid = maybe
    except Exception:
        pass
try:
    bb = solid.bounding_box()
    zmin = float(bb.min.Z)
    if abs(zmin) > 1e-6:
        from build123d import Location
        solid = solid.moved(Location((0.0, 0.0, -zmin)))
except Exception:
    pass
from build123d import export_step
buf = io.BytesIO()
ok = export_step(solid, buf)
if ok is False:
    raise RuntimeError("export_step failed")
step_bytes = buf.getvalue()
verts, tris = [], []
try:
    raw = solid.tessellate(0.5)
    verts = [(float(v.X), float(v.Y), float(v.Z)) for v in raw[0]]
    tris = [tuple(int(i) for i in tri) for tri in raw[1]]
except Exception:
    pass
import base64
result = {
    "ok": True,
    "step_b64": base64.b64encode(step_bytes).decode(),
    "verts": verts,
    "tris": tris,
}
json.dumps(result)
`;
  try {
    const raw = await pyodide.runPythonAsync(py);
    const data = JSON.parse(raw);
    session.stepBytes = Uint8Array.from(atob(data.step_b64), c => c.charCodeAt(0));
    session.mesh = { verts: data.verts || [], tris: data.tris || [] };
    const report = {
      ok: true,
      n_verts: data.verts?.length || 0,
      n_tris: data.tris?.length || 0,
      errors: [],
    };
    session.report = report;
    return JSON.stringify(report);
  } catch (err) {
    const report = { ok: false, errors: [String(err)] };
    session.report = report;
    return JSON.stringify(report);
  }
}

async function dispatch(name, args) {
  try {
    if (name === "set_spec") {
      session.spec = args;
      return "spec saved";
    }
    if (name === "set_params") {
      if (args.replace) session.params = {};
      Object.assign(session.params, args.values || {});
      return JSON.stringify(session.params);
    }
    if (name === "write_model") {
      const code = args.code || "";
      if (code.length > 12000) return "ERROR: model.py too long";
      if (!code.includes("def build")) return "ERROR: must define build(params)";
      session.modelCode = code;
      return `wrote model.py (${code.length} chars)`;
    }
    if (name === "apply_patch") {
      if (!session.modelCode.includes(args.old_str)) return "ERROR: old_str not found";
      session.modelCode = session.modelCode.replace(args.old_str, args.new_str);
      return "patched";
    }
    if (name === "run_model" || name === "inspect") {
      if (name === "run_model") {
        return await runModelInPyodide();
      }
      return JSON.stringify(session.report || { ok: false, errors: ["no run yet"] });
    }
    if (name === "lookup_device") {
      return JSON.stringify(lookupDevice(args.query || ""));
    }
    if (name === "ask_user") {
      return JSON.stringify({
        status: "defaults_applied",
        assumptions: args.assumptions || [],
        answers: {},
      });
    }
    if (name === "finish") {
      session.finished = true;
      session.finishSummary = args.summary || "";
      session.finishOk = !!args.ok;
      return "finished";
    }
    return `unknown tool ${name}`;
  } catch (err) {
    return `ERROR: ${err}`;
  }
}

async function callOpenRouter(messages, apiKey, model) {
  const resp = await fetch("https://openrouter.ai/api/v1/chat/completions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
      messages,
      tools: TOOLS,
      tool_choice: "auto",
      temperature: 0.2,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`OpenRouter ${resp.status}: ${text.slice(0, 400)}`);
  }
  return resp.json();
}

async function runAgent(userText, mode) {
  const apiKey = document.getElementById("apiKey").value.trim();
  const model = document.getElementById("model").value.trim() || "openrouter/free";
  if (!apiKey) {
    log("Paste an OpenRouter API key first.");
    return;
  }
  if (!pyodide) {
    log("Initialize the CAD engine first.");
    return;
  }
  clearLog();
  session.finished = false;
  const context = `MODE: ${mode}\nUSER REQUEST:\n${userText}\n\nCURRENT SPEC: ${JSON.stringify(session.spec)}\nCURRENT PARAMS: ${JSON.stringify(session.params)}\nMODEL.PY EXISTS: ${!!session.modelCode}\nLAST REPORT: ${JSON.stringify(session.report)}`;
  let messages = [
    { role: "system", content: SYSTEM },
    { role: "user", content: context },
  ];
  const maxIters = 12;
  for (let step = 1; step <= maxIters; step++) {
    log(`[${step}/${maxIters}] ${model}`);
    let data;
    try {
      data = await callOpenRouter(messages, apiKey, model);
    } catch (err) {
      log(String(err));
      break;
    }
    const msg = data.choices?.[0]?.message;
    if (!msg) {
      log("empty response");
      break;
    }
    if (msg.content) log(msg.content.slice(0, 500));
    messages.push(msg);
    const calls = msg.tool_calls || [];
    if (!calls.length) break;
    for (const call of calls) {
      const name = call.function.name;
      let args = {};
      try { args = JSON.parse(call.function.arguments || "{}"); } catch {}
      log(`  tool ${name}(${JSON.stringify(args).slice(0, 120)})`);
      const result = await dispatch(name, args);
      messages.push({
        role: "tool",
        tool_call_id: call.id,
        content: result,
      });
      if (session.finished) break;
    }
    if (session.finished) break;
  }
  log(session.finishSummary || "Done.");
  updateUI();
}

function updateUI() {
  document.getElementById("modelCode").textContent = session.modelCode || "(none)";
  document.getElementById("paramsJson").textContent = JSON.stringify(session.params, null, 2);
  const downloads = document.getElementById("downloads");
  downloads.innerHTML = "";
  if (session.stepBytes) {
    const blob = new Blob([session.stepBytes], { type: "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "part.step";
    a.textContent = "Download part.step";
    a.className = "download";
    downloads.appendChild(a);
  }
  document.getElementById("preview-area").classList.remove("hidden");
  if (session.mesh && session.mesh.verts.length) {
    renderMesh(session.mesh);
  }
}

function renderMesh({ verts, tris }) {
  const container = document.getElementById("viewer");
  container.innerHTML = "";
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xe8e4dc);
  const camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 10000);
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setSize(container.clientWidth, container.clientHeight);
  container.appendChild(renderer.domElement);
  const geometry = new THREE.BufferGeometry();
  const positions = [];
  for (const tri of tris) {
    for (const idx of tri) {
      const v = verts[idx];
      positions.push(v[0], v[1], v[2]);
    }
  }
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.computeVertexNormals();
  const material = new THREE.MeshStandardMaterial({ color: 0x88aaee, metalness: 0.1, roughness: 0.7, side: THREE.DoubleSide });
  const mesh = new THREE.Mesh(geometry, material);
  scene.add(mesh);
  // Center camera
  geometry.computeBoundingSphere();
  const center = geometry.boundingSphere.center;
  const radius = geometry.boundingSphere.radius || 50;
  camera.position.set(center.x + radius * 1.5, center.y + radius * 1.2, center.z + radius * 1.5);
  camera.lookAt(center);
  const light = new THREE.DirectionalLight(0xffffff, 1);
  light.position.set(1, 2, 3);
  scene.add(light);
  scene.add(new THREE.AmbientLight(0xffffff, 0.4));
  function animate() {
    requestAnimationFrame(animate);
    mesh.rotation.z += 0.003;
    renderer.render(scene, camera);
  }
  animate();
}

document.getElementById("initBtn").onclick = initPyodide;
document.getElementById("runBtn").onclick = () => {
  const prompt = document.getElementById("prompt").value.trim();
  if (prompt) runAgent(prompt, session.modelCode ? "edit" : "new");
};
document.getElementById("editBtn").onclick = () => {
  const prompt = document.getElementById("prompt").value.trim();
  if (prompt) runAgent(prompt, "edit");
};
