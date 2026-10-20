from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from printcad import __version__, runner
from printcad.agent import Agent
from printcad.config import find_config, load_config, require_api_key, retryable_failure
from printcad.providers.registry import get_provider, list_providers
from printcad.session import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="printcad",
        description="Text → build123d → STEP. Iterative edits keep params + model.py as source of truth.",
    )
    parser.add_argument("--version", action="version", version=f"printcad {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Prompt-only run from printcad.toml (new, then edit)")
    p_run.add_argument("prompt", help="Part description, or a second-take edit if a model already exists")
    p_run.add_argument("--session", default=None, help="Override session directory from the config")

    p_init = sub.add_parser("init", help="Create a session directory")
    _add_session_flag(p_init)

    p_new = sub.add_parser("new", help="Describe a new part and generate STEP")
    p_new.add_argument("prompt", help="Natural-language part description")
    _add_session_flag(p_new)
    _add_llm_flags(p_new)

    p_edit = sub.add_parser("edit", help="Second take: alter the current part")
    p_edit.add_argument("prompt", help="What to change")
    _add_session_flag(p_edit)
    _add_llm_flags(p_edit)

    p_rebuild = sub.add_parser("rebuild", help="Run current model.py with no LLM")
    _add_session_flag(p_rebuild)

    p_status = sub.add_parser("status", help="Show spec, params, last inspect")
    _add_session_flag(p_status)

    p_revert = sub.add_parser("revert", help="Restore last good model/params/STEP")
    _add_session_flag(p_revert)

    p_preview = sub.add_parser("preview", help="Render out/preview.svg and preview.html from the STEP")
    _add_session_flag(p_preview)

    sub.add_parser("providers", help="List model providers and env vars")
    sub.add_parser("doctor", help="Check build123d and API keys")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "init":
            return cmd_init(args)
        if args.cmd == "run":
            return cmd_run(args)
        if args.cmd == "new":
            return cmd_generate(args, mode="new")
        if args.cmd == "edit":
            return cmd_generate(args, mode="edit")
        if args.cmd == "rebuild":
            return cmd_rebuild(args)
        if args.cmd == "status":
            return cmd_status(args)
        if args.cmd == "revert":
            return cmd_revert(args)
        if args.cmd == "preview":
            return cmd_preview(args)
        if args.cmd == "providers":
            return cmd_providers()
        if args.cmd == "doctor":
            return cmd_doctor()
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 1


def _add_session_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--session",
        default=os.environ.get("PRINTCAD_SESSION", "."),
        help="Session directory (default: cwd, or PRINTCAD_SESSION)",
    )


def _add_llm_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        default=os.environ.get("PRINTCAD_PROVIDER", "openai"),
        help="openai|anthropic|google|xai|openrouter|ollama|compatible",
    )
    parser.add_argument("--model", default=None, help="Model id for the provider")
    parser.add_argument("--base-url", default=None, help="Override API base URL")
    parser.add_argument("--api-key", default=None, help="Override API key")
    parser.add_argument("--max-iters", type=int, default=14)
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=None,
        help="Seconds to wait for each LLM HTTP response (default 300, env PRINTCAD_HTTP_TIMEOUT).",
    )
    parser.add_argument(
        "--assume-defaults",
        action="store_true",
        help="If the model calls ask_user, apply its listed assumptions instead of prompting.",
    )


def cmd_init(args: argparse.Namespace) -> int:
    sess = Session.create(Path(args.session))
    if not sess.model_path.exists():
        sess.write_model(runner.model_template())
    print(f"session ready: {sess.root}")
    print("next: ./printcad.sh \"FDM plate 80x50x4 mm with 4x M4 holes\"")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config()
    api_key = require_api_key(cfg)
    root = Path(args.session) if args.session else cfg.session
    sess = Session.create(root)
    mode = "edit" if _has_real_model(sess) else "new"
    print(f"config: {cfg.path}")
    print(f"session: {sess.root}  mode: {mode}")
    print(f"models: {', '.join(cfg.models)}")
    snapshot = _snapshot(sess)
    errors: list[str] = []
    for index, model in enumerate(cfg.models, start=1):
        if index > 1:
            _restore(sess, snapshot)
        print(f"trying {index}/{len(cfg.models)} openrouter:{model}")
        provider = get_provider("openrouter", model, None, api_key)
        takes = int(sess.meta().get("takes") or 0) + 1
        sess.write_meta(takes=takes, provider="openrouter", model=model)
        sess.write_prompt(args.prompt)
        agent = Agent(
            sess,
            provider,
            log=print,
            max_iters=cfg.max_iters,
            ask=_prompt_answers,
            assume_defaults=cfg.assume_defaults,
        )
        summary = agent.run(args.prompt, mode=mode)
        if _attempt_succeeded(sess, snapshot, agent):
            report = sess.report()
            print()
            print(summary)
            print()
            print(_report_lines(report))
            print(f"STEP: {sess.step_path}")
            print(f"model: {model}")
            _print_preview(sess)
            return 0
        report = sess.report()
        reason = summary or "no STEP"
        errors.append(f"{model}: {reason.splitlines()[0][:240]}")
        if retryable_failure(summary, agent.tool_calls_made):
            print(f"retryable failure on {model}: {reason.splitlines()[0][:240]}")
            continue
        print()
        print(summary)
        if report:
            print()
            print(_report_lines(report))
        _restore(sess, snapshot)
        if sess.step_path.exists():
            print(f"kept previous STEP: {sess.step_path}")
        return 2
    _restore(sess, snapshot)
    print("all configured models failed:")
    for item in errors:
        print(f"  - {item}")
    return 2


def _attempt_succeeded(sess: Session, snapshot: dict[str, str | None], agent: Agent) -> bool:
    """A previous inspect must not count. This model has to call tools and rewrite the report."""
    if agent.tool_calls_made <= 0 or (not agent.finish_ok and retryable_failure(agent.finish_summary, 0)):
        return False
    report = sess.report()
    if not report or not report.ok or not sess.step_path.exists():
        return False
    report_now = sess.report_path.read_text() if sess.report_path.exists() else None
    if report_now == snapshot.get(str(sess.report_path)):
        return False
    return True


def _has_real_model(sess: Session) -> bool:
    if not sess.model_path.exists():
        return False
    source = sess.model_source().strip()
    if not source or "def build" not in source:
        return False
    return source != runner.model_template().strip()


def _snapshot(sess: Session) -> dict[str, str | None]:
    paths = [
        sess.spec_path,
        sess.params_path,
        sess.model_path,
        sess.prompt_path,
        sess.meta_path,
        sess.report_path,
        sess.step_path,
    ]
    saved: dict[str, str | None] = {}
    for path in paths:
        saved[str(path)] = path.read_text() if path.exists() and path.suffix != ".step" else None
        if path.suffix == ".step":
            saved[str(path)] = path.read_bytes().hex() if path.exists() else None
    return saved


def _restore(sess: Session, snapshot: dict[str, str | None]) -> None:
    for raw, payload in snapshot.items():
        path = Path(raw)
        if payload is None:
            if path.exists():
                path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".step":
            path.write_bytes(bytes.fromhex(payload))
        else:
            path.write_text(payload)


def cmd_generate(args: argparse.Namespace, mode: str) -> int:
    root = Path(args.session)
    if mode == "new":
        sess = Session.create(root)
    else:
        try:
            sess = Session.find(root)
        except FileNotFoundError:
            sess = Session.create(root)
    if getattr(args, "http_timeout", None):
        os.environ["PRINTCAD_HTTP_TIMEOUT"] = str(args.http_timeout)
    provider = get_provider(args.provider, args.model, args.base_url, args.api_key)
    takes = int(sess.meta().get("takes") or 0) + 1
    sess.write_meta(takes=takes, provider=args.provider, model=getattr(provider, "model", args.model))
    sess.write_prompt(args.prompt)
    print(f"session: {sess.root}")
    print(f"provider: {provider.name}  model: {getattr(provider, 'model', '')}")
    agent = Agent(
        sess,
        provider,
        log=print,
        max_iters=args.max_iters,
        ask=_prompt_answers,
        assume_defaults=bool(args.assume_defaults),
    )
    summary = agent.run(args.prompt, mode=mode)
    print()
    print(summary)
    report = sess.report()
    if report:
        print()
        print(_report_lines(report))
    if report and report.ok and sess.step_path.exists():
        print(f"STEP: {sess.step_path}")
        _print_preview(sess)
    elif sess.step_path.exists():
        print(f"stale STEP left from last success: {sess.step_path}")
    return 0 if (report and report.ok) else 2


def cmd_rebuild(args: argparse.Namespace) -> int:
    sess = Session.find(Path(args.session))
    report = runner.run_model(sess)
    print(_report_lines(report))
    if report.ok and sess.step_path.exists():
        print(f"STEP: {sess.step_path}")
        _print_preview(sess)
    elif sess.step_path.exists():
        print(f"stale STEP left from last success: {sess.step_path}")
    return 0 if report.ok else 2


def cmd_preview(args: argparse.Namespace) -> int:
    from build123d import import_step

    from printcad.preview import write_preview

    sess = Session.find(Path(args.session))
    if not sess.step_path.exists():
        print("no STEP yet")
        return 1
    solid = import_step(str(sess.step_path))
    info = write_preview(getattr(solid, "part", solid), sess.step_path.parent)
    print(f"SVG:  {info['svg']}")
    print(f"HTML: {info['html']}")
    return 0


def _print_preview(sess: Session) -> None:
    svg = sess.step_path.parent / "preview.svg"
    page = sess.step_path.parent / "preview.html"
    if svg.exists():
        print(f"preview SVG:  {svg}")
    if page.exists():
        print(f"preview HTML: {page}")


def cmd_status(args: argparse.Namespace) -> int:
    sess = Session.find(Path(args.session))
    print(f"session: {sess.root}")
    print(f"takes: {sess.meta().get('takes', 0)}")
    spec = sess.spec()
    print(f"spec: {spec.summary or '(empty)'}  process={spec.process}  family={spec.family or '-'}")
    params = sess.params().values
    if params:
        print("params:")
        for key, val in params.items():
            unit = sess.params().units.get(key, "mm")
            print(f"  {key} = {val} {unit}")
    else:
        print("params: (none)")
    print(f"model.py: {'yes' if sess.model_path.exists() else 'no'}")
    report = sess.report()
    if report:
        print(_report_lines(report))
    else:
        print("inspect: none yet")
    if sess.step_path.exists():
        print(f"STEP: {sess.step_path}")
    return 0


def cmd_revert(args: argparse.Namespace) -> int:
    sess = Session.find(Path(args.session))
    if not sess.revert_good():
        print("no last-good snapshot")
        return 1
    print("restored last good model/params/STEP")
    return 0


def cmd_providers() -> int:
    print(f"{'name':12} {'default model':28} env / notes")
    for info in list_providers():
        keys = ",".join(info.env_keys) if info.env_keys else "(none)"
        print(f"{info.name:12} {info.default_model:28} {keys}  {info.notes}")
    print()
    print("Aliases: claude→anthropic, gemini→google, grok→xai")
    print("Any other host: --provider compatible --base-url http://host:port/v1")
    return 0


def cmd_doctor() -> int:
    ok = True
    import printcad as pkg

    print(f"printcad: {pkg.__version__}")
    print(f"module:   {pkg.__file__}")
    print(f"python:   {sys.executable}")
    cfg_path = find_config()
    if cfg_path is None:
        print("config:   not found (copy printcad.toml.example to printcad.toml)")
    else:
        try:
            cfg = load_config(cfg_path)
            key = "set" if cfg.api_key else "missing"
            print(f"config:   {cfg.path} key={key} models={len(cfg.models)} session={cfg.session}")
        except (OSError, ValueError) as exc:
            print(f"config:   {cfg_path} error: {exc}")
            ok = False
    try:
        import build123d

        print(f"build123d: {getattr(build123d, '__version__', 'ok')}")
    except Exception as exc:
        print(f"build123d: MISSING ({exc})")
        ok = False
    for label, names in [
        ("openai", ("OPENAI_API_KEY",)),
        ("anthropic", ("ANTHROPIC_API_KEY",)),
        ("google", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
        ("xai", ("XAI_API_KEY", "GROK_API_KEY")),
        ("openrouter", ("OPENROUTER_API_KEY",)),
        ("compatible", ("PRINTCAD_API_KEY", "PRINTCAD_BASE_URL")),
        ("ollama", ("OLLAMA_HOST",)),
    ]:
        present = [n for n in names if os.environ.get(n)]
        mark = ",".join(present) if present else "not set"
        print(f"{label:12} {mark}")
    return 0 if ok else 1


def _prompt_answers(questions: list[str], assumptions: list[str]) -> dict[str, str]:
    print()
    print("The model needs a few answers (blank = use the listed assumption):")
    if assumptions:
        print("Assumptions if you press Enter:")
        for item in assumptions:
            print(f"  - {item}")
    answers: dict[str, str] = {}
    for i, question in enumerate(questions, start=1):
        hint = assumptions[i - 1] if i - 1 < len(assumptions) else ""
        prompt = f"  {i}. {question}"
        if hint:
            prompt += f"\n     [{hint}] "
        else:
            prompt += "\n     > "
        try:
            value = input(prompt).strip()
        except EOFError:
            value = ""
        if not value:
            value = hint or "(unspecified)"
        answers[question] = value
    print("Continuing with those answers.\n")
    return answers


def _report_lines(report) -> str:
    stage = "inspect"
    if isinstance(report.extras, dict) and report.extras.get("stage") == "run":
        stage = "run"
    bits = [f"{stage} ok={report.ok} valid={report.valid}"]
    if report.size_mm:
        bits.append("size_mm=" + " x ".join(str(v) for v in report.size_mm))
    if report.volume_mm3 is not None:
        bits.append(f"volume_mm3={report.volume_mm3:.1f}")
    if report.errors:
        bits.append("errors: " + "; ".join(report.errors))
    if report.warnings:
        bits.append("warnings: " + "; ".join(report.warnings))
    if report.extras:
        check = report.extras.get("prompt_check") if isinstance(report.extras, dict) else None
        if check:
            bits.append(
                f"prompt_check ok={check.get('ok')} holes={check.get('hole_count')} "
                f"source={check.get('measure_source')}"
            )
            for item in check.get("mismatches") or []:
                bits.append(f"  mismatch: {item}")
            for item in check.get("notes") or []:
                bits.append(f"  note: {item}")
        else:
            bits.append("extras: " + str(report.extras))
    return "\n".join(bits)


if __name__ == "__main__":
    raise SystemExit(main())
