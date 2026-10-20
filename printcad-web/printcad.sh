#!/usr/bin/env bash
# Repeatable entry: the prompt and nothing else.
#   ./printcad.sh "FDM L-bracket 80x60 mm, 5 mm thick, two M4 clearance holes per leg"
# Subcommands still work (doctor, status, rebuild, preview, new, edit).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export PRINTCAD_CONFIG="${PRINTCAD_CONFIG:-$ROOT/printcad.toml}"
PY="${PRINTCAD_PYTHON:-${CONDA_PREFIX:+$CONDA_PREFIX/bin/python}}"
if [[ -z "${PY}" || ! -x "${PY}" ]]; then
  PY="$(command -v python3 || command -v python)"
fi
if [[ $# -lt 1 ]]; then
  echo "usage: ./printcad.sh \"part description\"" >&2
  echo "config: ${PRINTCAD_CONFIG}" >&2
  exit 2
fi
case "$1" in
  init|new|edit|rebuild|status|revert|preview|providers|doctor|run|--version|--help|-h)
    exec "${PY}" -m printcad "$@"
    ;;
esac
exec "${PY}" -m printcad run "$*"
