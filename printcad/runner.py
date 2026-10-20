from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from printcad.schema import InspectReport
from printcad.session import Session


WORKER = r'''
import json, sys, traceback
from pathlib import Path

session = Path(sys.argv[1])
params_path = session / "params.json"
model_path = session / "model.py"
step_path = session / "out" / "part.step"
step_path.parent.mkdir(parents=True, exist_ok=True)

raw = json.loads(params_path.read_text())
params = raw.get("values", raw)

from printcad.compat import install_build123d_shims, patch_module
install_build123d_shims()
import importlib.util
spec = importlib.util.spec_from_file_location("printcad_user_model", model_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load model.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
patch_module(mod)
if not hasattr(mod, "build"):
    raise SystemExit("model.py must define build(params)")

try:
    part = mod.build(params)
except RuntimeError as exc:
    msg = str(exc)
    if "can't be combined" in msg or "part' attribute" in msg:
        raise SystemExit(
            "BuildPart cannot use += / +. Assign solid = bp.part then solid + other, "
            "or add(other) inside the with-block. Original: " + msg
        ) from exc
    raise
if part is None:
    raise SystemExit("build(params) returned None")

# Accept BuildPart context, Shape, or object with .part
solid = getattr(part, "part", part)
if hasattr(solid, "solid"):
    try:
        maybe = solid.solid()
        if maybe is not None:
            solid = maybe
    except Exception:
        pass

from build123d import export_step
try:
    bb = solid.bounding_box()
    zmin = float(bb.min.Z)
    if abs(zmin) > 1e-6:
        from build123d import Location
        solid = solid.moved(Location((0.0, 0.0, -zmin)))
except Exception:
    pass
ok = export_step(solid, str(step_path))
if ok is False:
    raise SystemExit("export_step failed")
preview = {}
try:
    from printcad.preview import write_preview
    preview = write_preview(solid, step_path.parent)
except Exception as exc:
    preview = {"error": str(exc)}
print(json.dumps({"step": str(step_path), "preview": preview}))
'''


def run_model(session: Session, timeout: int = 60) -> InspectReport:
    if not session.model_path.exists():
        report = InspectReport(ok=False, errors=["model.py is missing"])
        session.write_report(report)
        return report
    if not session.params_path.exists():
        report = InspectReport(ok=False, errors=["params.json is missing"])
        session.write_report(report)
        return report

    proc = subprocess.run(
        [sys.executable, "-c", WORKER, str(session.root)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(session.root),
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "model failed").strip()
        report = InspectReport(
            ok=False,
            errors=[err[-4000:]],
            extras={"stage": "run"},
        )
        session.write_report(report)
        return report
    return inspect_step(session)


def inspect_step(session: Session) -> InspectReport:
    from .validate import inspect_file

    report = inspect_file(
        session.step_path,
        session.spec(),
        session.params(),
        prompt=session.last_prompt(),
    )
    session.write_report(report)
    if report.ok:
        session.snapshot_good()
    return report


def model_template() -> str:
    return textwrap.dedent(
        '''\
        from build123d import *


        def build(params: dict):
            """Return a single build123d solid. Read sizes only from params."""
            length = params["length"]
            width = params["width"]
            thickness = params["thickness"]
            part = Box(length, width, thickness, align=(Align.CENTER, Align.CENTER, Align.MIN))
            # Fillets: fillet(part.edges().filter_by(Axis.Z), r)  or  part.edges(axis=Axis.Z).fillet(r)
            return part
        '''
    )
