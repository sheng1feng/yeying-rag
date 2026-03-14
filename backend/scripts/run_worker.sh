#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

INSTANCE_ID="${1:-${WORKER_INSTANCE_ID:-1}}"
ENV_FILE="${WORKER_ENV_FILE:-${BACKEND_DIR}/.env}"
PYTHON_BIN="${WORKER_PYTHON_BIN:-${BACKEND_DIR}/.venv/bin/python}"
WORKER_NAME_PREFIX="${WORKER_NAME_PREFIX:-knowledge-worker}"
WORKER_NAME_OVERRIDE="${WORKER_NAME_OVERRIDE:-}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "worker python interpreter not found: ${PYTHON_BIN}" >&2
  exit 1
fi

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
if [[ -n "${WORKER_NAME_OVERRIDE}" ]]; then
  export WORKER_NAME="${WORKER_NAME_OVERRIDE}"
else
  export WORKER_NAME="${WORKER_NAME_PREFIX}-${INSTANCE_ID}"
fi

cd "${BACKEND_DIR}"
exec "${PYTHON_BIN}" -m knowledge.workers.runner
