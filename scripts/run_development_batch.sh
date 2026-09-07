#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: scripts/run_development_batch.sh [--max N | --all] [--list]"
  echo
  echo "Runs automation-ready tasks from docs/development-plan.md sequentially."
  echo "The default is one task. Completed tasks are committed locally but not pushed."
}

maximum_tasks=1
list_only=0

while (($#)); do
  case "$1" in
    --max)
      if (($# < 2)) || [[ ! "$2" =~ ^[1-9][0-9]*$ ]]; then
        echo "error: --max requires a positive integer" >&2
        exit 2
      fi
      maximum_tasks="$2"
      shift 2
      ;;
    --all)
      maximum_tasks=2147483647
      shift
      ;;
    --list)
      list_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
  echo "error: run this script inside the VitaMine Git repository" >&2
  exit 1
}
plan_path="$repo_root/docs/development-plan.md"

if [[ ! -f "$plan_path" ]]; then
  echo "error: development plan not found at $plan_path" >&2
  exit 1
fi

pending_lines() {
  grep -E '^### AUTO-[0-9]{3} \[ \] - ' "$plan_path" || true
}

if ((list_only)); then
  listed_tasks="$(pending_lines)"
  if [[ -z "$listed_tasks" ]]; then
    echo "No automation-ready tasks remain."
    exit 0
  fi
  printf '%s\n' "$listed_tasks"
  exit 0
fi

resolve_codex_binary() {
  if [[ -n "${VITAMINE_CODEX_BIN:-}" ]]; then
    if [[ -x "$VITAMINE_CODEX_BIN" ]]; then
      printf '%s\n' "$VITAMINE_CODEX_BIN"
      return 0
    fi
    echo "error: VITAMINE_CODEX_BIN is not executable: $VITAMINE_CODEX_BIN" >&2
    return 1
  fi

  if command -v codex >/dev/null 2>&1; then
    command -v codex
    return 0
  fi

  local candidate
  local newest=""
  for candidate in \
    "$HOME"/.vscode/extensions/openai.chatgpt-*/bin/macos-*/codex \
    "$HOME"/.vscode-insiders/extensions/openai.chatgpt-*/bin/macos-*/codex \
    "$HOME"/.cursor/extensions/openai.chatgpt-*/bin/macos-*/codex \
    "/Applications/Codex.app/Contents/Resources/codex"; do
    if [[ -x "$candidate" ]] && { [[ -z "$newest" ]] || [[ "$candidate" -nt "$newest" ]]; }; then
      newest="$candidate"
    fi
  done
  if [[ -n "$newest" ]]; then
    printf '%s\n' "$newest"
    return 0
  fi

  echo "error: Codex CLI was not found on PATH or in a known app/extension location" >&2
  echo "Set VITAMINE_CODEX_BIN to the absolute path of an installed codex binary." >&2
  return 1
}

codex_binary="$(resolve_codex_binary)" || exit 1

current_branch="$(git -C "$repo_root" branch --show-current)"
if [[ -z "$current_branch" ]]; then
  echo "error: detached HEAD is not supported" >&2
  exit 1
fi
case "$current_branch" in
  main|master)
    echo "error: refuse to run automation directly on $current_branch" >&2
    echo "Create or switch to an agent/* batch branch first." >&2
    exit 1
    ;;
esac

if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  echo "error: the worktree must be clean before starting a batch" >&2
  git -C "$repo_root" status --short >&2
  exit 1
fi

log_directory="$repo_root/output/automation"
mkdir -p "$log_directory"
completed=0

while ((completed < maximum_tasks)); do
  task_line="$(pending_lines | sed -n '1p')"
  if [[ -z "$task_line" ]]; then
    echo "No automation-ready tasks remain."
    break
  fi
  task_id="$(sed -E 's/^### (AUTO-[0-9]{3}).*/\1/' <<<"$task_line")"
  before_head="$(git -C "$repo_root" rev-parse HEAD)"
  before_branch="$(git -C "$repo_root" branch --show-current)"
  result_path="$log_directory/$task_id.txt"

  read -r -d '' prompt <<PROMPT || true
Implement exactly task $task_id from docs/development-plan.md.

Read AGENTS.md first, then read the complete $task_id task block and any files
it directly references. Work only within the task's stated scope.

Safety requirements:
- Do not deploy, SSH, push, purchase or configure services, access production
  secrets or data, browse private user data, or make any external state change.
- Do not change branches, amend existing commits, or use git reset/checkout to
  discard changes.
- If a stop condition is reached or any acceptance criterion cannot be met,
  leave the task unchecked, do not commit a partial implementation, explain the
  blocker, and stop.
- Do not award a globe/internet-scale marker.

Completion requirements:
1. Implement every acceptance criterion with focused tests.
2. Run every validation listed in the task plus the relevant existing tests.
3. Audit the diff for secrets, private data, unrelated changes, and generated
   artifacts.
4. Only after all checks pass, change "$task_id [ ]" to "$task_id [x]" and
   replace its "Evidence: pending" line with a concise list of tests and files.
5. Run git diff --check.
6. Commit the complete task locally with a subject beginning "$task_id: ".
7. Do not push.

Finish with either "COMPLETED $task_id" or "BLOCKED $task_id" and a concise
summary. The wrapper will independently verify the checkbox, commit, branch,
and clean worktree.
PROMPT

  echo "Starting $task_id on $before_branch"
  if ! "$codex_binary" \
    --ask-for-approval never \
    exec \
    --ephemeral \
    --ignore-user-config \
    --sandbox workspace-write \
    --cd "$repo_root" \
    --output-last-message "$result_path" \
    "$prompt"; then
    echo "error: Codex failed while running $task_id" >&2
    echo "Review $result_path and the worktree before continuing." >&2
    exit 1
  fi

  after_branch="$(git -C "$repo_root" branch --show-current)"
  after_head="$(git -C "$repo_root" rev-parse HEAD)"
  if [[ "$after_branch" != "$before_branch" ]]; then
    echo "error: $task_id changed branches; stopping for review" >&2
    exit 1
  fi
  if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
    echo "error: $task_id left a dirty worktree; stopping for review" >&2
    git -C "$repo_root" status --short >&2
    exit 1
  fi
  if [[ "$after_head" == "$before_head" ]]; then
    echo "error: $task_id did not create the required commit" >&2
    exit 1
  fi
  if ! grep -Fq "### $task_id [x] - " "$plan_path"; then
    echo "error: $task_id was not checked off after validation" >&2
    exit 1
  fi
  commit_subject="$(git -C "$repo_root" log -1 --format=%s)"
  if [[ "$commit_subject" != "$task_id: "* ]]; then
    echo "error: unexpected commit subject for $task_id: $commit_subject" >&2
    exit 1
  fi
  if ! git -C "$repo_root" merge-base --is-ancestor "$before_head" "$after_head"; then
    echo "error: $task_id rewrote existing history" >&2
    exit 1
  fi

  completed=$((completed + 1))
  echo "Completed $task_id ($completed/$maximum_tasks): $after_head"
done

echo "Batch finished with $completed completed task(s)."
echo "Commits remain local for review; this script never pushes."
