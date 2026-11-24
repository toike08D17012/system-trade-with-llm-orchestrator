#!/usr/bin/env bash
# Start `stock-research research run` detached and record its outcome in a job directory.
# Usage: scripts/research-run-bg.sh [--on-exit COMMAND] [--] RESEARCH_RUN_ARGS...
# Prints the job directory. Wait with scripts/research-wait.sh JOB_DIR instead of polling.

set -euo pipefail

write_status() {
    local job_dir="$1" state="$2" exit_code="$3" bundle="$4" second_opinion="$5" pid="$6"
    local execution_record="${7:-}" failed_stage="${8:-}"
    python3 - "${state}" "${exit_code}" "${bundle}" "${second_opinion}" "${pid}" \
        "${execution_record}" "${failed_stage}" >"${job_dir}/status.json.tmp" <<'PYTHON'
import json
import sys
from datetime import datetime, timezone

state, exit_code, bundle, second_opinion, pid, execution_record, failed_stage = sys.argv[1:]
print(json.dumps({
    "state": state,
    "exit_code": json.loads(exit_code),
    "bundle": bundle,
    "second_opinion": second_opinion,
    "pid": json.loads(pid),
    "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "execution_record": execution_record,
    "failed_stage": failed_stage,
}))
PYTHON
    mv "${job_dir}/status.json.tmp" "${job_dir}/status.json"
}

worker() {
    local job_dir="$1" on_exit="$2"
    shift 2
    local exit_code=0 bundle="" second_opinion="" state="succeeded" execution_record="" failed_stage=""
    write_status "${job_dir}" running null "" "" "$$"
    "${STOCK_RESEARCH:-stock-research}" research run "$@" >"${job_dir}/log" 2>&1 || exit_code=$?
    bundle="$(sed -n 's/.*bundle=\([^;]*\);.*/\1/p' "${job_dir}/log" | tail -n 1)"
    second_opinion="$(sed -n 's/^research: second_opinion=//p' "${job_dir}/log" | tail -n 1)"
    execution_record="$(sed -n 's/^research: running; execution_record=//p' "${job_dir}/log" | tail -n 1)"
    if ((exit_code != 0)); then
        state="failed"
        failed_stage="$(sed -n 's/^research: stage=\([^ ]*\) status=failed$/\1/p' "${job_dir}/log" | tail -n 1)"
    fi
    write_status "${job_dir}" "${state}" "${exit_code}" "${bundle}" "${second_opinion}" "$$" \
        "${execution_record}" "${failed_stage}"
    if [[ -n "${on_exit}" ]]; then
        RESEARCH_JOB_DIR="${job_dir}" bash -c "${on_exit}" >>"${job_dir}/on-exit.log" 2>&1 || true
    fi
}

main() {
    if [[ "${1:-}" == --worker ]]; then
        shift
        worker "$@"
        return
    fi
    local on_exit=""
    while (($# > 0)); do
        case "$1" in
            --on-exit)
                if (($# < 2)); then
                    echo "Error: --on-exit requires a command." >&2
                    return 2
                fi
                on_exit="$2"
                shift 2
                ;;
            --)
                shift
                break
                ;;
            *) break ;;
        esac
    done
    local job_dir
    job_dir="${RESEARCH_JOBS_DIR:-runtime/research-jobs}/$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p -- "${job_dir%/*}"
    mkdir -m 700 -- "${job_dir}"
    write_status "${job_dir}" running null "" "" null
    nohup "$0" --worker "${job_dir}" "${on_exit}" "$@" >/dev/null 2>&1 &
    echo "${job_dir}"
}

main "$@"
