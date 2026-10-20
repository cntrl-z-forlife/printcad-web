from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from printcad.schema import InspectReport, Params, Spec

SESSION_MARKER = "printcad.json"


class Session:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.spec_path = self.root / "spec.json"
        self.params_path = self.root / "params.json"
        self.model_path = self.root / "model.py"
        self.history_path = self.root / "history.jsonl"
        self.report_path = self.root / "out" / "report.json"
        self.step_path = self.root / "out" / "part.step"
        self.meta_path = self.root / SESSION_MARKER
        self.prompt_path = self.root / "prompt.txt"

    @classmethod
    def create(cls, root: Path) -> "Session":
        sess = cls(root)
        sess.root.mkdir(parents=True, exist_ok=True)
        (sess.root / "out").mkdir(exist_ok=True)
        if not sess.meta_path.exists():
            sess.meta_path.write_text(
                json.dumps(
                    {
                        "created": _now(),
                        "takes": 0,
                        "last_good_step": None,
                    },
                    indent=2,
                )
                + "\n"
            )
        if not sess.spec_path.exists():
            sess.write_spec(Spec())
        if not sess.params_path.exists():
            sess.write_params(Params())
        if not sess.history_path.exists():
            sess.history_path.write_text("")
        return sess

    @classmethod
    def find(cls, start: Path | None = None) -> "Session":
        start = (start or Path.cwd()).resolve()
        for path in [start, *start.parents]:
            if (path / SESSION_MARKER).exists():
                return cls(path)
        raise FileNotFoundError(
            "No printcad session here. Run `printcad init` or `printcad new` first."
        )

    def meta(self) -> dict[str, Any]:
        if not self.meta_path.exists():
            return {}
        return json.loads(self.meta_path.read_text())

    def write_meta(self, **updates: Any) -> None:
        data = self.meta()
        data.update(updates)
        self.meta_path.write_text(json.dumps(data, indent=2) + "\n")

    def spec(self) -> Spec:
        if not self.spec_path.exists():
            return Spec()
        return Spec.model_validate_json(self.spec_path.read_text())

    def write_spec(self, spec: Spec) -> None:
        self.spec_path.write_text(spec.model_dump_json(indent=2) + "\n")

    def last_prompt(self) -> str:
        if self.prompt_path.exists():
            return self.prompt_path.read_text()
        return str(self.meta().get("last_prompt") or "")

    def write_prompt(self, prompt: str) -> None:
        text = prompt.strip() + "\n"
        self.prompt_path.write_text(text)
        self.write_meta(last_prompt=prompt.strip())

    def params(self) -> Params:
        if not self.params_path.exists():
            return Params()
        raw = json.loads(self.params_path.read_text())
        if "values" in raw:
            return Params.model_validate(raw)
        return Params(values={k: float(v) for k, v in raw.items()})

    def write_params(self, params: Params) -> None:
        self.params_path.write_text(params.model_dump_json(indent=2) + "\n")

    def model_source(self) -> str:
        if not self.model_path.exists():
            return ""
        return self.model_path.read_text()

    def write_model(self, code: str) -> None:
        text = code.strip() + "\n"
        self.model_path.write_text(text)

    def apply_replace(self, target: str, old: str, new: str) -> str:
        if target == "model.py":
            path = self.model_path
        elif target == "params.json":
            path = self.params_path
        else:
            raise ValueError(f"Unknown patch target: {target}")
        if not path.exists():
            raise FileNotFoundError(f"{target} does not exist yet")
        text = path.read_text()
        count = text.count(old)
        if count == 0:
            raise ValueError(f"old_str not found in {target}")
        if count > 1:
            raise ValueError(f"old_str found {count} times in {target}; make it unique")
        path.write_text(text.replace(old, new, 1))
        return f"patched {target}"

    def write_report(self, report: InspectReport) -> None:
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(report.model_dump_json(indent=2) + "\n")

    def report(self) -> InspectReport | None:
        if not self.report_path.exists():
            return None
        return InspectReport.model_validate_json(self.report_path.read_text())

    def snapshot_good(self) -> None:
        good = self.root / "out" / "last_good"
        good.mkdir(parents=True, exist_ok=True)
        if self.step_path.exists():
            shutil.copy2(self.step_path, good / "part.step")
        if self.model_path.exists():
            shutil.copy2(self.model_path, good / "model.py")
        if self.params_path.exists():
            shutil.copy2(self.params_path, good / "params.json")
        self.write_meta(last_good_step=str(good / "part.step"), last_good_at=_now())

    def revert_good(self) -> bool:
        good = self.root / "out" / "last_good"
        model = good / "model.py"
        params = good / "params.json"
        step = good / "part.step"
        if not model.exists():
            return False
        shutil.copy2(model, self.model_path)
        if params.exists():
            shutil.copy2(params, self.params_path)
        if step.exists():
            shutil.copy2(step, self.step_path)
        return True

    def append_history(self, event: dict[str, Any]) -> None:
        event = {"ts": _now(), **event}
        with self.history_path.open("a") as fh:
            fh.write(json.dumps(event) + "\n")

    def context_bundle(self) -> str:
        spec = self.spec().model_dump()
        params = self.params().model_dump()
        model = self.model_source() or "(no model.py yet)"
        report = self.report().model_dump() if self.report() else None
        prompt = self.last_prompt() or "(no saved prompt)"
        return (
            "CURRENT SESSION STATE\n"
            f"user prompt =\n{prompt}\n\n"
            f"spec.json =\n{json.dumps(spec, indent=2)}\n\n"
            f"params.json =\n{json.dumps(params, indent=2)}\n\n"
            f"model.py =\n{model}\n\n"
            f"last inspect =\n{json.dumps(report, indent=2)}\n"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
