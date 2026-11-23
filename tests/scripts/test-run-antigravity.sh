#!/usr/bin/env bash

set -euo pipefail

main() {
    local repository_root
    local test_root
    local bash_binary
    local status
    local index
    local -a actual=()
    local -a expected=()
    local argument

    repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
    bash_binary="$(command -v bash)"
    test_root="$(mktemp -d)"
    # Only the directory created for this test is removed.
    export TEST_CAPTURE="${test_root}"
    trap 'rm -rf -- "${TEST_CAPTURE}"' EXIT
    mkdir -p "${test_root}/bin" "${test_root}/empty" "${test_root}/workspace with spaces"
    cat >"${test_root}/bin/agy" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\0' "$@" >"${TEST_CAPTURE}/args"
printf '%s' "${PWD}" >"${TEST_CAPTURE}/cwd"
cat >"${TEST_CAPTURE}/stdin"
exit "${TEST_EXIT:-0}"
FAKE
    chmod +x "${test_root}/bin/agy"
    export PATH="${test_root}/bin:${PATH}"

    # Check exact argument boundaries, stdin and cwd, not just a textual approximation.
    cd "${test_root}/workspace with spaces"
    printf 'private test input\n' | "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" -p 'A prompt with spaces' --conversation 'test-session'
    while IFS= read -r -d '' argument; do actual+=("${argument}"); done <"${test_root}/args"
    expected=(--effort high -p 'A prompt with spaces' --conversation test-session)
    [[ "${#actual[@]}" == "${#expected[@]}" ]]
    for ((index = 0; index < ${#expected[@]}; index++)); do
        [[ "${actual[index]}" == "${expected[index]}" ]]
    done
    [[ "$(cat "${test_root}/stdin")" == 'private test input' ]]
    [[ "$(cat "${test_root}/cwd")" == "${PWD}" ]]

    local effort
    for effort in high xhigh max; do
        "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" "--effort=${effort}" </dev/null
        printf '%s\0' --effort "${effort}" >"${test_root}/expected"
        cmp "${test_root}/expected" "${test_root}/args"
        "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" --effort "${effort}" </dev/null
        cmp "${test_root}/expected" "${test_root}/args"
    done

    "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" -p --effort </dev/null
    printf '%s\0' --effort high -p --effort >"${test_root}/expected"
    cmp "${test_root}/expected" "${test_root}/args"
    "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" -- --effort low '' </dev/null
    printf '%s\0' --effort high -- --effort low '' >"${test_root}/expected"
    cmp "${test_root}/expected" "${test_root}/args"

    local invalid
    for invalid in low medium unknown ''; do
        printf 'not invoked' >"${test_root}/args"
        status=0
        "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" "--effort=${invalid}" 2>"${test_root}/error" || status=$?
        [[ "${status}" == 2 ]]
        [[ "$(cat "${test_root}/args")" == 'not invoked' ]]
    done
    status=0
    "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" --effort 2>"${test_root}/error" || status=$?
    [[ "${status}" == 2 ]]
    status=0
    "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" --effort high --effort max 2>"${test_root}/error" || status=$?
    [[ "${status}" == 2 ]]
    [[ "$(cat "${test_root}/args")" == 'not invoked' ]]

    status=0
    TEST_EXIT=37 "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" </dev/null || status=$?
    [[ "${status}" == 37 ]]
    status=0
    PATH="${test_root}/empty" "${bash_binary}" "${repository_root}/scripts/run-antigravity.sh" 2>"${test_root}/error" || status=$?
    [[ "${status}" == 127 ]]
    echo "Antigravity launcher tests passed."
}

main "$@"
