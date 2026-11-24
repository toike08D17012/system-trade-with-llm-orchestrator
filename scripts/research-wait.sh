#!/usr/bin/env bash
# Block until a job started by scripts/research-run-bg.sh finishes, then print its outcome once.
# Usage: scripts/research-wait.sh JOB_DIR [--timeout SECONDS]
# Exit status: 0 succeeded, 1 failed, 124 still running at timeout (prints the latest stage).

set -euo pipefail

state_of() {
    sed -n 's/.*"state": "\([a-z]*\)".*/\1/p' "$1/status.json"
}

main() {
    if (($# < 1)); then
        echo "Usage: $0 JOB_DIR [--timeout SECONDS]" >&2
        return 2
    fi
    local job_dir="$1" timeout=540
    shift
    if [[ "${1:-}" == --timeout ]]; then
        timeout="${2:?--timeout requires seconds}"
    fi
    local deadline=$((SECONDS + timeout)) state pid
    while state="$(state_of "${job_dir}")" && [[ "${state}" == running ]]; do
        pid="$(sed -n 's/.*"pid": \([0-9]*\).*/\1/p' "${job_dir}/status.json")"
        if [[ -n "${pid}" ]] && ! kill -0 "${pid}" 2>/dev/null && [[ "$(state_of "${job_dir}")" == running ]]; then
            echo "state=failed reason=worker_exited_without_status"
            return 1
        fi
        if ((SECONDS >= deadline)); then
            echo "state=running latest=$(grep -o 'stage=[^ ]* status=[a-z]*' "${job_dir}/log" 2>/dev/null | tail -n 1)"
            return 124
        fi
        sleep 5
    done
    cat "${job_dir}/status.json"
    tail -n 5 "${job_dir}/log" 2>/dev/null || true
    [[ "${state}" == succeeded ]]
}

main "$@"
