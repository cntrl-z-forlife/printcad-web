"""Runtime shims so generated model.py can use Axis.Z or bare Z / CadQuery-ish edges."""

from __future__ import annotations

from types import ModuleType


def install_build123d_shims() -> None:
    """Run before exec of model.py so `from build123d.operations_generic import hull` resolves."""
    try:
        import build123d.operations_generic as og
    except Exception:
        return
    if not hasattr(og, "hull"):
        og.hull = _missing_hull


def patch_module(mod: ModuleType) -> None:
    from build123d import Axis

    for name, value in (("Axis", Axis), ("X", Axis.X), ("Y", Axis.Y), ("Z", Axis.Z)):
        if not hasattr(mod, name):
            setattr(mod, name, value)
    if not hasattr(mod, "regular_dodecahedron"):
        setattr(mod, "regular_dodecahedron", regular_dodecahedron)
    if not hasattr(mod, "hull"):
        setattr(mod, "hull", _missing_hull)
    _patch_edges()


def _missing_hull(*_args, **_kwargs):
    raise RuntimeError(
        "hull() is not available in this build123d. "
        "For a regular dodecahedron call regular_dodecahedron(edge_mm) "
        "with edge_mm in millimeters (1 cm = 10). "
        "Do not import hull from build123d.operations_generic."
    )


def regular_dodecahedron(edge_mm: float):
    """Regular dodecahedron, edge length in mm, centered at the origin."""
    from build123d import Face, Shell, Solid, Vector, Wire

    phi = (1 + 5 ** 0.5) / 2
    raw = []
    for x in (-1, 1):
        for y in (-1, 1):
            for z in (-1, 1):
                raw.append((x, y, z))
    for y in (-1 / phi, 1 / phi):
        for z in (-phi, phi):
            raw.append((0, y, z))
    for x in (-1 / phi, 1 / phi):
        for y in (-phi, phi):
            raw.append((x, y, 0))
    for x in (-phi, phi):
        for z in (-1 / phi, 1 / phi):
            raw.append((x, 0, z))
    # Unit set edge length is 2/phi
    scale = float(edge_mm) * phi / 2.0
    pts = [Vector(x * scale, y * scale, z * scale) for x, y, z in raw]
    faces_idx = _dodeca_faces(pts)
    faces = []
    for idx in faces_idx:
        wire = Wire.make_polygon([pts[i] for i in idx], close=True)
        faces.append(Face(wire))
    return Solid(Shell(faces))


def _dodeca_faces(pts) -> list[list[int]]:
    """Pentagon vertex cycles by nearest-neighbor walk on the 20-vertex set."""
    n = len(pts)
    edge = min(
        (pts[i] - pts[j]).length
        for i in range(n)
        for j in range(i + 1, n)
    )
    tol = edge * 0.08
    nbrs = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if abs((pts[i] - pts[j]).length - edge) <= tol:
                nbrs[i].append(j)
                nbrs[j].append(i)
    faces: list[tuple[int, ...]] = []
    seen: set[frozenset[int]] = set()
    for a in range(n):
        for b in nbrs[a]:
            for c in nbrs[b]:
                if c == a:
                    continue
                cycle = _pentagon(a, b, c, nbrs)
                if not cycle:
                    continue
                key = frozenset(cycle)
                if key in seen:
                    continue
                seen.add(key)
                faces.append(tuple(cycle))
    if len(faces) != 12:
        raise RuntimeError(f"dodecahedron face walk produced {len(faces)} faces, expected 12")
    return [list(f) for f in faces]


def _pentagon(a: int, b: int, c: int, nbrs: list[list[int]]) -> list[int] | None:
    cycle = [a, b, c]
    for _ in range(2):
        prev, cur = cycle[-2], cycle[-1]
        nxts = [n for n in nbrs[cur] if n != prev]
        # pick the neighbor that continues a pentagon (still connected to start later)
        found = None
        for n in nxts:
            if n in cycle:
                continue
            found = n
            break
        if found is None:
            return None
        cycle.append(found)
    if cycle[0] not in nbrs[cycle[-1]]:
        return None
    if len(set(cycle)) != 5:
        return None
    return cycle


def _patch_edges() -> None:
    from build123d import Axis, Shape

    if getattr(Shape.edges, "_printcad_patched", False):
        return

    original = Shape.edges

    def edges(self, *args, axis=None, **kwargs):
        selector = axis
        if selector is None and args:
            selector = args[0]
            args = args[1:]
        result = original(self, *args, **kwargs) if args or kwargs else original(self)
        if selector is not None:
            axis_obj = _as_axis(selector)
            if axis_obj is not None:
                result = result.filter_by(axis_obj)
        return _Selectable(self, result)

    edges._printcad_patched = True  # type: ignore[attr-defined]
    Shape.edges = edges  # type: ignore[method-assign]


def _as_axis(value):
    from build123d import Axis

    if value in (Axis.X, Axis.Y, Axis.Z):
        return value
    mapping = {
        "X": Axis.X,
        "Y": Axis.Y,
        "Z": Axis.Z,
        "|X": Axis.X,
        "|Y": Axis.Y,
        "|Z": Axis.Z,
        "+X": Axis.X,
        "+Y": Axis.Y,
        "+Z": Axis.Z,
    }
    if isinstance(value, str) and value in mapping:
        return mapping[value]
    return None


class _Selectable:
    def __init__(self, owner, edges):
        self._owner = owner
        self._edges = edges

    def __iter__(self):
        return iter(self._edges)

    def __len__(self):
        return len(self._edges)

    def __getattr__(self, name):
        return getattr(self._edges, name)

    def filter_by(self, *args, **kwargs):
        return _Selectable(self._owner, self._edges.filter_by(*args, **kwargs))

    def fillet(self, radius):
        owner = self._owner
        if hasattr(owner, "fillet"):
            try:
                return owner.fillet(radius, self._edges)
            except TypeError:
                return owner.fillet(self._edges, radius)
        from build123d import fillet as fillet_fn

        return fillet_fn(self._edges, radius)

    def chamfer(self, size, size2=None):
        owner = self._owner
        if hasattr(owner, "chamfer"):
            try:
                if size2 is None:
                    return owner.chamfer(size, self._edges)
                return owner.chamfer(size, size2, self._edges)
            except TypeError:
                pass
        from build123d import chamfer as chamfer_fn

        if size2 is None:
            return chamfer_fn(self._edges, size)
        return chamfer_fn(self._edges, size, size2)
