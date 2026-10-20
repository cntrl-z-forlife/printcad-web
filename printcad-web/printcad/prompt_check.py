from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from printcad.schema import Params, Spec

_WORDS = {
    "one": 1,
    "a": 1,
    "an": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "eight": 8,
}


def parse_prompt_requirements(prompt: str) -> dict[str, Any]:
    text = prompt or ""
    lower = text.lower()
    req: dict[str, Any] = {"raw_excerpt": text[:400]}

    per = _first_int(
        lower,
        r"(\d+|one|two|three|four|five|six|eight)\s+(?:through[-\s])?holes?\s+(?:on|per|in)\s+each",
    )
    groups = 0
    if re.search(r"\beach\s+leg\b", lower):
        groups = 2
    elif re.search(r"\beach\s+side\b", lower):
        groups = 2
    elif re.search(r"\beach\s+corner\b", lower):
        groups = 4
    nx = _first_int(lower, r"(\d+)\s*[x×]\s*(?:m\d+|holes?|through)")
    total_words = _first_int(lower, r"(\d+|two|three|four|five|six|eight)\s+(?:through[-\s])?holes?")

    if per and groups:
        req["expected_holes"] = per * groups
        req["holes_per_group"] = per
        req["groups"] = groups
    elif nx:
        req["expected_holes"] = nx
    elif total_words and "each" not in lower:
        req["expected_holes"] = total_words
    elif per:
        req["expected_holes"] = per

    m_fit = re.search(r"\bm(\d+(?:\.\d+)?)\b", lower)
    if m_fit:
        req["thread_size"] = f"M{m_fit.group(1)}"

    hole_d = _nearby_mm(text, r"(?:hole|clearance|through).{0,40}?(\d+(?:\.\d+)?)\s*mm")
    if hole_d is None:
        hole_d = _nearby_mm(text, r"(\d+(?:\.\d+)?)\s*mm.{0,24}(?:diameter|clearance|hole)")
    if hole_d is not None:
        req["expected_hole_d"] = hole_d

    fillet = _nearby_mm(text, r"(?:inner\s+)?(?:corner\s+)?fillet\s+(\d+(?:\.\d+)?)\s*mm")
    if fillet is None:
        fillet = _nearby_mm(text, r"fillet\s+(\d+(?:\.\d+)?)")
    if fillet is not None:
        req["expected_fillet"] = fillet

    chamfer = _nearby_mm(text, r"chamfer\s+(\d+(?:\.\d+)?)\s*mm")
    if chamfer is not None:
        req["expected_chamfer"] = chamfer

    thick = _nearby_mm(text, r"(\d+(?:\.\d+)?)\s*mm\s+thick")
    if thick is None:
        thick = _nearby_mm(text, r"thick(?:ness)?\s+(\d+(?:\.\d+)?)\s*mm")
    if thick is not None:
        req["expected_thickness"] = thick

    envelope = re.search(
        r"inside\s+(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*mm",
        lower,
    )
    if envelope:
        req["expected_envelope"] = [float(envelope.group(i)) for i in range(1, 4)]

    return req


def measure_step_features(step_path: Path) -> dict[str, Any]:
    measured = {"cylinders": [], "source": None}
    occ = _measure_occ(step_path)
    if occ is not None:
        measured.update(occ)
        measured["source"] = "occ"
        return measured
    text = _measure_step_text(step_path)
    measured.update(text)
    measured["source"] = "step_text"
    return measured


def compare_prompt_to_step(
    prompt: str,
    spec: Spec | None,
    params: Params | None,
    size_mm: list[float] | None,
    step_path: Path | None,
) -> dict[str, Any]:
    req = parse_prompt_requirements(prompt)
    values = dict(params.values) if params else {}
    hole_d = values.get("hole_d") or values.get("hole_diameter") or req.get("expected_hole_d")
    fillet_r = None
    for key, val in values.items():
        if "fillet" in key:
            fillet_r = float(val)
            break
    if fillet_r is None:
        fillet_r = req.get("expected_fillet")

    features: dict[str, Any] = {}
    if step_path and step_path.exists():
        features = measure_step_features(step_path)

    cylinders = list(features.get("cylinders") or [])
    holes = _classify_holes(cylinders, hole_d, fillet_r)
    hole_count = len(holes)

    mismatches: list[str] = []
    notes: list[str] = []

    expected_holes = req.get("expected_holes")
    if expected_holes and hole_count != expected_holes:
        mismatches.append(
            f"prompt asks for {expected_holes} through-holes; STEP has {hole_count} "
            f"(centers {[h['xy'] for h in holes]})"
        )

    if hole_d and holes:
        bad = [h for h in holes if abs(h["diameter"] - float(hole_d)) > 0.15]
        if bad:
            mismatches.append(
                f"hole diameters {[round(h['diameter'], 3) for h in bad]} do not match hole_d={hole_d}"
            )
    elif req.get("expected_hole_d") and not holes:
        mismatches.append(f"prompt hole diameter {req['expected_hole_d']} mm not found in STEP")

    if size_mm and req.get("expected_thickness"):
        if abs(size_mm[2] - float(req["expected_thickness"])) > 0.3:
            mismatches.append(
                f"thickness {size_mm[2]} mm vs prompt {req['expected_thickness']} mm"
            )

    if size_mm and req.get("expected_envelope"):
        env = req["expected_envelope"]
        for axis, actual, limit in zip("XYZ", size_mm, env):
            if actual > float(limit) + 0.25:
                mismatches.append(f"{axis} {actual} mm exceeds prompt envelope {limit} mm")

    if fillet_r:
        filleted = [c for c in cylinders if abs(c["radius"] - float(fillet_r)) <= 0.15]
        if not filleted:
            notes.append(f"no cylinder near fillet radius {fillet_r} mm (fillet may be missing)")

    if req.get("expected_chamfer") and size_mm:
        notes.append("chamfer presence is not measured; inspect the STEP in Fusion")

    # Major plan dimensions named in params should fit inside bbox
    if size_mm:
        plan = sorted(size_mm[:2], reverse=True)
        claimed = []
        for key in ("leg_a", "leg_b", "length", "width"):
            if key in values:
                claimed.append(float(values[key]))
        claimed = sorted(claimed, reverse=True)[:2]
        if len(claimed) == 2:
            if plan[0] + 0.4 < claimed[0] or plan[1] + 0.4 < claimed[1]:
                mismatches.append(
                    f"bbox plan {plan[0]:.2f}x{plan[1]:.2f} mm is smaller than params {claimed[0]}x{claimed[1]} mm"
                )

    family = (spec.family if spec else "") or ""
    if family == "l_bracket" and expected_holes is None and hole_count in {1, 2}:
        notes.append(
            f"L-bracket STEP has {hole_count} hole(s); confirm that matches the request"
        )

    return {
        "ok": not mismatches,
        "requirements": req,
        "hole_count": hole_count,
        "holes": holes,
        "cylinders": cylinders,
        "measure_source": features.get("source"),
        "mismatches": mismatches,
        "notes": notes,
    }


def _classify_holes(
    cylinders: list[dict[str, Any]],
    hole_d: float | None,
    fillet_r: float | None,
) -> list[dict[str, Any]]:
    holes: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for cyl in cylinders:
        r = float(cyl["radius"])
        xy = (round(float(cyl["x"]), 2), round(float(cyl["y"]), 2))
        if xy in seen:
            continue
        if fillet_r is not None and abs(r - float(fillet_r)) <= 0.12:
            continue
        if hole_d is not None and abs(2 * r - float(hole_d)) <= 0.15:
            seen.add(xy)
            holes.append({"xy": [xy[0], xy[1]], "diameter": round(2 * r, 4), "radius": r})
            continue
        span = cyl.get("angle_span")
        if span is not None and span >= 5.5 and r <= 8:
            seen.add(xy)
            holes.append({"xy": [xy[0], xy[1]], "diameter": round(2 * r, 4), "radius": r})
            continue
        if hole_d is None and fillet_r is None and r <= 6 and cyl.get("kind") != "fillet":
            # STEP-text fallback: small cylinders that are not the large inner-corner blend
            seen.add(xy)
            holes.append({"xy": [xy[0], xy[1]], "diameter": round(2 * r, 4), "radius": r})
    return holes


def _measure_occ(step_path: Path) -> dict[str, Any] | None:
    try:
        from build123d import import_step
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
    except Exception:
        return None
    try:
        shape = import_step(str(step_path))
        solid = getattr(shape, "part", shape)
        faces = solid.faces()
    except Exception:
        return None
    cylinders: list[dict[str, Any]] = []
    for face in faces:
        try:
            adaptor = BRepAdaptor_Surface(face.wrapped)
            if adaptor.GetType() != GeomAbs_Cylinder:
                continue
            cyl = adaptor.Cylinder()
            loc = cyl.Axis().Location()
            span = abs(float(adaptor.LastUParameter()) - float(adaptor.FirstUParameter()))
            cylinders.append(
                {
                    "radius": float(cyl.Radius()),
                    "x": float(loc.X()),
                    "y": float(loc.Y()),
                    "z": float(loc.Z()),
                    "angle_span": span,
                    "kind": "hole" if span >= 5.5 else "blend",
                }
            )
        except Exception:
            continue
    return {"cylinders": cylinders}


def _measure_step_text(step_path: Path) -> dict[str, Any]:
    try:
        text = step_path.read_text(errors="replace")
    except Exception:
        return {"cylinders": []}
    id_map: dict[str, str] = {}
    for match in re.finditer(r"#(\d+)\s*=\s*([^;]+);", text):
        id_map[match.group(1)] = match.group(2)

    def _point(entity_id: str) -> tuple[float, float, float] | None:
        body = id_map.get(entity_id, "")
        pts = re.search(
            r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*([^)]+)\)",
            body,
        )
        if not pts and "CARTESIAN_POINT" not in body:
            ref = re.search(r"CARTESIAN_POINT\s*\(", body)
            if not ref:
                # AXIS2 -> #pt
                inner = re.search(r"AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)", body)
                if inner:
                    return _point(inner.group(1))
                if body.startswith("CARTESIAN_POINT") or "CARTESIAN_POINT" in body:
                    pass
                else:
                    # entity is AXIS2 stored as full
                    inner = re.search(r"#(\d+)", body)
                    if inner:
                        return _point(inner.group(1))
                    return None
        raw = pts.group(1) if pts else None
        if raw is None:
            pts = re.search(r"\(\s*'[^']*'\s*,\s*\(\s*([^)]+)\)", body)
            if not pts:
                return None
            raw = pts.group(1)
        nums = [float(x) for x in raw.split(",")]
        if len(nums) < 3:
            return None
        return nums[0], nums[1], nums[2]

    cylinders: list[dict[str, Any]] = []
    for cid, body in id_map.items():
        m = re.search(r"CYLINDRICAL_SURFACE\s*\(\s*'[^']*'\s*,\s*#(\d+)\s*,\s*([0-9.]+)\s*\)", body)
        if not m:
            continue
        axis_id, radius_s = m.group(1), m.group(2)
        axis_body = id_map.get(axis_id, "")
        pt_id = re.search(r"AXIS2_PLACEMENT_3D\s*\(\s*'[^']*'\s*,\s*#(\d+)", axis_body)
        if not pt_id:
            continue
        xyz = _point(pt_id.group(1))
        if xyz is None:
            continue
        cylinders.append(
            {
                "radius": float(radius_s),
                "x": xyz[0],
                "y": xyz[1],
                "z": xyz[2],
                "kind": "unknown",
            }
        )
    return {"cylinders": cylinders}


def _first_int(text: str, pattern: str) -> int | None:
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    token = m.group(1).lower()
    if token in _WORDS:
        return _WORDS[token]
    try:
        return int(token)
    except ValueError:
        return None


def _nearby_mm(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None
