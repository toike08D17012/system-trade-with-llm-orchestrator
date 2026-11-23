#!/usr/bin/env bash

set -euo pipefail

main() {
    local effort="high"
    local effort_seen=false
    local -a arguments=(--effort high)

    while (($# > 0)); do
        case "$1" in
            --effort | --effort=*)
                if [[ "${effort_seen}" == true ]]; then
                    echo "Error: specify --effort only once." >&2
                    return 2
                fi
                effort_seen=true
                if [[ "$1" == --effort ]]; then
                    if (($# < 2)); then
                        echo "Error: --effort requires a value." >&2
                        return 2
                    fi
                    effort="$2"
                    shift 2
                else
                    effort="${1#--effort=}"
                    shift
                fi
                case "${effort}" in
                    high | xhigh | max) ;;
                    *)
                        echo "Error: --effort must be high, xhigh, or max; the model must support it." >&2
                        return 2
                        ;;
                esac
                ;;
            --)
                arguments+=("$@")
                break
                ;;
            -p | --print | --prompt | --model | --conversation | --output-format | --input-format | --json-schema | --agent | --add-dir | --mode | --print-timeout)
                # Preserve option values even when they resemble our --effort option.
                arguments+=("$1")
                shift
                if (($# > 0)); then
                    arguments+=("$1")
                    shift
                fi
                ;;
            *)
                arguments+=("$1")
                shift
                ;;
        esac
    done

    if ! command -v agy >/dev/null 2>&1; then
        echo "Error: agy is not installed or is not on PATH. Run scripts/install-antigravity-cli.sh first." >&2
        return 127
    fi

    arguments[1]="${effort}"
    exec agy "${arguments[@]}"
}

main "$@"
