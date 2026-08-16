#!/usr/bin/env bash

set -euo pipefail

main() {
    curl -fsSL https://chatgpt.com/codex/install.sh | sh
}

main "$@"
