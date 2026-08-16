#!/usr/bin/env bash

set -euo pipefail

main() {
    curl -fsSL https://claude.ai/install.sh | bash
}

main "$@"
