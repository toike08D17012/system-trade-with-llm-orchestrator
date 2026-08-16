#!/usr/bin/env bash

set -euo pipefail

main() {
    curl -fsSL https://antigravity.google/cli/install.sh | bash -s -- "$@"
}

main "$@"
