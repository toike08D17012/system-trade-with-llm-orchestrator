# Shell Script Instructions

## 1. Scope

Apply these instructions when editing:

- Shell scripts (`*.sh`, `*.bash`)
- Scripts with Bash shebangs
- Pre-commit helper scripts
- Docker or development workflow scripts written in shell

## 2. Shell Choice

- Use Bash for repository scripts unless an existing script intentionally uses another shell.
- Start Bash scripts with:

```bash
#!/usr/bin/env bash
```

- Use POSIX `sh` only when portability is explicitly required.
- Do not rewrite an existing POSIX-compatible script into Bash unless there is a clear reason.

## 3. Safety Defaults

For Bash scripts, use:

```bash
set -euo pipefail
```

Do not use these options only when there is a specific reason, and document that reason when it is non-obvious.

## 4. Script Structure

- Prefer a `main` function for non-trivial scripts.
- Call `main "$@"` at the end of the script.
- Keep scripts idempotent where practical.
- Prefer small helper functions over long inline blocks.
- Avoid unrelated rewrites or formatting-only changes.
- Preserve existing CLI behavior unless the task explicitly asks to change it.

Example structure:

```bash
#!/usr/bin/env bash

set -euo pipefail

main() {
    # Script logic here.
    :
}

main "$@"
```

## 5. Quoting and Variables

- Quote variable expansions unless word splitting is intentional.
- Prefer `"${var}"` over `$var` in non-trivial scripts.
- Use `local` for function-local variables.
- Avoid relying on unset variables.
- Use arrays when handling multiple arguments in Bash.
- Preserve arguments with `"$@"`.

## 6. Paths and Working Directories

- Resolve script-relative paths when scripts are intended to be run from different directories.
- Avoid assuming the current working directory unless the script explicitly requires it.
- Prefer repository-root detection based on the script location when practical.

Example:

```bash
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}/../.."
```

## 7. Commands and Error Handling

- Check whether required commands exist when a fallback is available.
- Use clear error messages for missing required tools.
- Avoid hiding failures with `|| true` unless the failure is expected and safe.
- When using `|| true`, add a short comment explaining why.
- Avoid printing secrets or sensitive environment variables.

## 8. Docker and Local Fallbacks

When scripts support both Docker and local execution:

- Prefer the repository-defined Docker workflow when Docker is available.
- Fall back to local commands when Docker is unavailable only if the local command is expected to work.
- Preserve arguments when forwarding commands.
- Avoid recursive Docker invocation if the script may run inside a container.

## 9. Formatting and Linting

- Run ShellCheck when available.
- Follow existing formatting style in nearby scripts.
- Do not introduce formatting-only changes unrelated to the task.

Common validation command:

```bash
./scripts/pre-commit/checks.sh
```

If a Shell-specific validation script exists, use it.

## 10. Checklist

Before finishing shell script changes, confirm:

- [ ] The relevant existing scripts were inspected.
- [ ] Existing CLI behavior was preserved unless a change was requested.
- [ ] Variables are quoted where appropriate.
- [ ] Arguments are preserved with `"$@"` when forwarded.
- [ ] Script-relative paths are handled correctly.
- [ ] Error handling is explicit.
- [ ] Secrets are not printed.
- [ ] ShellCheck or repository-defined checks were run when possible.
- [ ] Any skipped checks are explained.
