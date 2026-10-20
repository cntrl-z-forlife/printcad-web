"""Published outer sizes for named devices. Inner case cavity = device + clearance."""

from __future__ import annotations

DEVICES: dict[str, dict] = {
    "iphone 12 mini": {
        "length": 131.5,
        "width": 64.2,
        "thickness": 7.4,
        "corner_r": 10.0,
        "notes": [
            "Official body 131.5 x 64.2 x 7.4 mm.",
            "Camera bump sits proud of 7.4 mm; add camera_well extra depth.",
            "Buttons: volume + ring on left, power on right, Lightning on bottom.",
        ],
    },
    "iphone 12": {"length": 146.7, "width": 71.5, "thickness": 7.4, "corner_r": 11.0},
    "iphone 13 mini": {"length": 131.5, "width": 64.2, "thickness": 7.65, "corner_r": 10.0},
    "iphone 13": {"length": 146.7, "width": 71.5, "thickness": 7.65, "corner_r": 11.0},
    "iphone 14": {"length": 146.7, "width": 71.5, "thickness": 7.8, "corner_r": 11.0},
    "iphone 15": {"length": 147.6, "width": 71.6, "thickness": 7.8, "corner_r": 11.0},
    "iphone 16": {"length": 147.6, "width": 71.6, "thickness": 7.8, "corner_r": 11.0},
}


def lookup_device(query: str) -> dict | None:
    q = " ".join(query.lower().split())
    if q in DEVICES:
        return {"key": q, **DEVICES[q]}
    for key, data in DEVICES.items():
        if key in q or q in key:
            return {"key": key, **data}
    tokens = set(q.replace("-", " ").split())
    best = None
    best_n = 0
    for key, data in DEVICES.items():
        n = len(tokens & set(key.split()))
        if n > best_n and n >= 2:
            best_n = n
            best = {"key": key, **data}
    return best


def case_params_from_device(device: dict, wall: float = 2.0, clearance: float = 0.4, lip: float = 1.2) -> dict:
    """Cavity is larger than the phone. Outer is cavity + walls."""
    return {
        "device_length": device["length"],
        "device_width": device["width"],
        "device_thickness": device["thickness"],
        "corner_r": device.get("corner_r", 8.0),
        "clearance": clearance,
        "wall": wall,
        "lip": lip,
        "inner_length": device["length"] + 2 * clearance,
        "inner_width": device["width"] + 2 * clearance,
        "outer_length": device["length"] + 2 * clearance + 2 * wall,
        "outer_width": device["width"] + 2 * clearance + 2 * wall,
        "outer_height": device["thickness"] + clearance + wall + lip,
    }
