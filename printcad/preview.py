"""Reference views of a solid, written next to the STEP. No CAD app required."""

from __future__ import annotations

import html
import json
from pathlib import Path


def write_preview(solid, out_dir: Path, linear_deflection: float = 0.4) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    verts, tris = _tessellate(solid, linear_deflection)
    svg_path = out_dir / "preview.svg"
    html_path = out_dir / "preview.html"
    svg_path.write_text(_svg(verts, tris))
    html_path.write_text(_html(verts, tris))
    return {"svg": str(svg_path), "html": str(html_path), "triangles": str(len(tris))}


def _tessellate(solid, deflection: float):
    if hasattr(solid, "tessellate"):
        raw = solid.tessellate(deflection)
        verts = [(float(v.X), float(v.Y), float(v.Z)) for v in raw[0]]
        tris = [tuple(int(i) for i in tri) for tri in raw[1]]
        return verts, tris
    raise RuntimeError("solid has no tessellate(); cannot build preview")


def _bounds(verts):
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    zs = [p[2] for p in verts]
    return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)


def _svg(verts, tris) -> str:
    if not verts:
        return "<svg xmlns='http://www.w3.org/2000/svg'/>"
    xmin, xmax, ymin, ymax, zmin, zmax = _bounds(verts)
    panels = [
        ("top  Z up", lambda p: (p[0], -p[1]), xmin, xmax, -ymax, -ymin),
        ("front  Y up", lambda p: (p[0], -p[2]), xmin, xmax, -zmax, -zmin),
        ("right  Y up", lambda p: (p[1], -p[2]), ymin, ymax, -zmax, -zmin),
    ]
    width, height, gap = 320, 280, 24
    parts = [
        "<?xml version='1.0'?>",
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width * 3 + gap * 2}' height='{height + 28}'>",
        "<rect width='100%' height='100%' fill='#f4f1ea'/>",
    ]
    for i, (title, proj, a0, a1, b0, b1) in enumerate(panels):
        ox = i * (width + gap)
        parts.append(f"<text x='{ox + 12}' y='18' font-family='sans-serif' font-size='13'>{title}</text>")
        parts.append(_panel(verts, tris, proj, ox + 8, 28, width - 16, height - 8, a0, a1, b0, b1))
    parts.append("</svg>")
    return "\n".join(parts)


def _panel(verts, tris, proj, ox, oy, w, h, a0, a1, b0, b1) -> str:
    span_a = max(a1 - a0, 1e-6)
    span_b = max(b1 - b0, 1e-6)
    scale = min(w / span_a, h / span_b) * 0.92
    cx = ox + w / 2
    cy = oy + h / 2
    mid_a = (a0 + a1) / 2
    mid_b = (b0 + b1) / 2

    def xy(p):
        a, b = proj(p)
        return cx + (a - mid_a) * scale, cy + (b - mid_b) * scale

    lines = []
    for i, j, k in tris:
        p0, p1, p2 = xy(verts[i]), xy(verts[j]), xy(verts[k])
        lines.append(
            f"<polygon points='{p0[0]:.2f},{p0[1]:.2f} {p1[0]:.2f},{p1[1]:.2f} {p2[0]:.2f},{p2[1]:.2f}' "
            "fill='#d7deea' stroke='#2c3a4a' stroke-width='0.4'/>"
        )
    return "\n".join(lines)


def _html(verts, tris) -> str:
    payload = json.dumps({"v": [[round(c, 4) for c in p] for p in verts], "t": tris})
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>printcad preview</title>
<style>
  body {{ margin: 0; background: #1c1f24; color: #e8e4dc; font-family: sans-serif; }}
  canvas {{ width: 100vw; height: 100vh; display: block; }}
  #hud {{ position: fixed; left: 12px; top: 10px; font-size: 13px; opacity: 0.8; }}
</style>
</head>
<body>
<div id="hud">drag to orbit · scroll to zoom · reference only</div>
<canvas id="c"></canvas>
<script>
const mesh = {payload};
const canvas = document.getElementById('c');
const ctx = canvas.getContext('2d');
let yaw = 0.6, pitch = 0.5, dist = 2.2, drag = null;
function resize() {{
  canvas.width = innerWidth * devicePixelRatio;
  canvas.height = innerHeight * devicePixelRatio;
  draw();
}}
function draw() {{
  const w = canvas.width, h = canvas.height;
  ctx.fillStyle = '#1c1f24';
  ctx.fillRect(0, 0, w, h);
  const cx = mesh.v.reduce((s, p) => s + p[0], 0) / Math.max(mesh.v.length, 1);
  const cy = mesh.v.reduce((s, p) => s + p[1], 0) / Math.max(mesh.v.length, 1);
  const cz = mesh.v.reduce((s, p) => s + p[2], 0) / Math.max(mesh.v.length, 1);
  let span = 1;
  for (const p of mesh.v) span = Math.max(span, Math.hypot(p[0]-cx, p[1]-cy, p[2]-cz));
  const cyaw = Math.cos(yaw), syaw = Math.sin(yaw);
  const cp = Math.cos(pitch), sp = Math.sin(pitch);
  const rot = p => {{
    let x = p[0]-cx, y = p[1]-cy, z = p[2]-cz;
    const x1 = x * cyaw + y * syaw;
    const y1 = -x * syaw + y * cyaw;
    const y2 = y1 * cp - z * sp;
    const z2 = y1 * sp + z * cp;
    return [x1, y2, z2];
  }};
  const scale = Math.min(w, h) / (span * dist);
  const faces = [];
  for (const tri of mesh.t) {{
    const a = rot(mesh.v[tri[0]]), b = rot(mesh.v[tri[1]]), c = rot(mesh.v[tri[2]]);
    const nx = (b[1]-a[1])*(c[2]-a[2]) - (b[2]-a[2])*(c[1]-a[1]);
    const ny = (b[2]-a[2])*(c[0]-a[0]) - (b[0]-a[0])*(c[2]-a[2]);
    const nz = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0]);
    const depth = (a[2]+b[2]+c[2]) / 3;
    faces.push({{a, b, c, light: Math.max(0.25, nz / (Math.hypot(nx, ny, nz) || 1)), depth}});
  }}
  faces.sort((p, q) => p.depth - q.depth);
  for (const f of faces) {{
    const shade = Math.round(150 + 80 * f.light);
    ctx.fillStyle = `rgb(${{shade}},${{shade-12}},${{shade-28}})`;
    ctx.beginPath();
    ctx.moveTo(w/2 + f.a[0]*scale, h/2 - f.a[1]*scale);
    ctx.lineTo(w/2 + f.b[0]*scale, h/2 - f.b[1]*scale);
    ctx.lineTo(w/2 + f.c[0]*scale, h/2 - f.c[1]*scale);
    ctx.closePath();
    ctx.fill();
  }}
}}
addEventListener('resize', resize);
canvas.addEventListener('pointerdown', e => {{ drag = [e.clientX, e.clientY, yaw, pitch]; }});
addEventListener('pointerup', () => drag = null);
addEventListener('pointermove', e => {{
  if (!drag) return;
  yaw = drag[2] + (e.clientX - drag[0]) * 0.01;
  pitch = Math.max(-1.2, Math.min(1.2, drag[3] + (e.clientY - drag[1]) * 0.01));
  draw();
}});
canvas.addEventListener('wheel', e => {{
  dist = Math.max(0.6, Math.min(8, dist + e.deltaY * 0.002));
  draw();
  e.preventDefault();
}}, {{passive: false}});
resize();
</script>
</body>
</html>
"""
