#!/bin/bash
# Code checks, the same as the "lint" job of CI: ruff on the Python code (rules and formatting in ruff.toml), shellcheck on the shell scripts,
# and no file over 5 MB in git (sound banks, samples, archives and videos belong to build/, dist/ or a release, not to the repository).
# Needs ruff (pip install ruff) and shellcheck (apt install shellcheck / brew install shellcheck).
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
status=0

echo "lint: ruff"
ruff check . || status=1
ruff format --check . || { echo "lint: run   ruff format .   to fix the formatting" >&2; status=1; }

echo "lint: shellcheck"
shell_files=()
while IFS= read -r f; do
  case "$f" in
    *.sh) shell_files+=("$f") ;;
    *) head -n 1 "$f" 2>/dev/null | grep -qE '^#!.*\b(ba|da)?sh\b' && shell_files+=("$f") ;;
  esac
done < <(git ls-files)
shellcheck -S warning "${shell_files[@]}" || status=1

echo "lint: file sizes"
limit=$((5 * 1024 * 1024))
while IFS= read -r f; do
  size=$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f")
  if [ "$size" -gt "$limit" ]; then
    echo "lint: $f is $((size / 1024 / 1024)) MB; files over 5 MB must not be committed (see .gitignore)" >&2
    status=1
  fi
done < <(git ls-files)

[ "$status" -eq 0 ] && echo "lint: ok"
exit "$status"
