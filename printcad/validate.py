from __future__ import annotations

from pathlib import Path

from .prompt_check import compare_prompt_to_step
from .schema import InspectReport, Params, Spec


def inspect_file(
    step_path: Path,
    spec: Spec | None = None,
    params: Params | None = None,
    prompt: str | None = None,
) -> InspectReport:
    if not step_path.exists():
        return InspectReport(ok=False, errors=[f"STEP not found: {step_path}"])

    try:
        from build123d import import_step
    except Exception as exc:
        return InspectReport(ok=False, errors=[f"build123d import failed: {exc}"])

    try:
        shape = import_step(str(step_path))
    except Exception as exc:
        return InspectReport(ok=False, step_path=str(step_path), errors=[f"STEP import failed: {exc}"])

    solid = getattr(shape, "part", shape)
    errors: list[str] = []
    warnings: list[str] = []

    valid = None
    try:
        valid = bool(solid.is_valid)
    except Exception:
        try:
            valid = bool(solid.is_valid())
        except Exception:
            warnings.append("could not read is_valid")

    bbox = None
    size = None
    try:
        bb = solid.bounding_box()
        xmin, xmax = float(bb.min.X), float(bb.max.X)
        ymin, ymax = float(bb.min.Y), float(bb.max.Y)
        zmin, zmax = float(bb.min.Z), float(bb.max.Z)
        bbox = {
            "xmin": xmin,
            "xmax": xmax,
            "ymin": ymin,
            "ymax": ymax,
            "zmin": zmin,
            "zmax": zmax,
        }
        size = [round(xmax - xmin, 4), round(ymax - ymin, 4), round(zmax - zmin, 4)]
        if abs(zmin) > 0.05:
            warnings.append(
                f"part is not on the XY plane (zmin={zmin:.3f} mm); expected bottom at Z=0"
            )
    except Exception as exc:
        errors.append(f"bbox failed: {exc}")

    volume = None
    try:
        volume = float(solid.volume)
        if volume <= 1e-6:
            errors.append("volume is ~0; not a solid")
    except Exception as exc:
        warnings.append(f"volume failed: {exc}")

    if spec and spec.envelope_mm and size:
        for axis, actual, limit in zip("XYZ", size, spec.envelope_mm):
            if actual > float(limit) + 0.25:
                errors.append(f"{axis} size {actual} mm exceeds envelope {limit} mm")

    extras: dict = {}
    if params and size:
        extras.update(_fit_checks(size, volume, spec, params, warnings))

    if prompt:
        check = compare_prompt_to_step(prompt, spec, params, size, step_path)
        extras["prompt_check"] = check
        errors.extend(check.get("mismatches") or [])
        warnings.extend(check.get("notes") or [])

    if valid is False:
        errors.append("solid reported as invalid")

    ok = not errors and valid is not False
    return InspectReport(
        ok=ok,
        valid=valid,
        bbox_mm=bbox,
        size_mm=size,
        volume_mm3=volume,
        step_path=str(step_path),
        errors=errors,
        warnings=warnings,
        extras=extras,
    )


def _fit_checks(size, volume, spec, params, warnings) -> dict:
    extras: dict = {}
    values = params.values
    family = (spec.family if spec else "") or ""
    device_l = values.get("device_length")
    device_w = values.get("device_width")
    inner_l = values.get("inner_length")
    inner_w = values.get("inner_width")
    wall = values.get("wall")
    clearance = values.get("clearance")

    if device_l and inner_l and inner_l + 1e-6 < device_l:
        warnings.append(
            f"inner_length {inner_l} mm is smaller than device_length {device_l} mm; phone will not fit"
        )
    if device_w and inner_w and inner_w + 1e-6 < device_w:
        warnings.append(
            f"inner_width {inner_w} mm is smaller than device_width {device_w} mm; phone will not fit"
        )

    if device_l and device_w and wall:
        min_outer_l = device_l + 2 * (clearance or 0) + 2 * wall
        min_outer_w = device_w + 2 * (clearance or 0) + 2 * wall
        extras["min_outer_mm"] = [round(min_outer_l, 3), round(min_outer_w, 3)]
        # bbox axes are unordered vs phone length; compare against the two largest
        a, b = sorted(size[:2], reverse=True)
        need = sorted([min_outer_l, min_outer_w], reverse=True)
        if a + 0.3 < need[0] or b + 0.3 < need[1]:
            warnings.append(
                f"outer bbox {size[0]:.2f}x{size[1]:.2f} mm is smaller than device+walls "
                f"{min_outer_l:.2f}x{min_outer_w:.2f} mm"
            )

    if family in {"phone_case", "enclosure"} and volume and size:
        box = size[0] * size[1] * size[2]
        if box > 0 and volume / box > 0.85:
            warnings.append(
                "solid is >85% of its bounding box; looks like a block, not a hollow case"
            )
        extras["fill_ratio"] = round(volume / box, 3) if box else None

    if family == "phone_case":
        missing = [k for k in ("device_length", "device_width", "device_thickness", "wall", "clearance") if k not in values]
        if missing:
            warnings.append("phone_case missing params: " + ", ".join(missing))
        warnings.append(
            "phone_case completeness: confirm camera well, charging opening, and side-button cutouts; "
            "a blank tub is only a first draft"
        )
    return extras
