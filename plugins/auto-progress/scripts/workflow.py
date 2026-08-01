#!/usr/bin/env python3
"""Guarded v3 workflow stages for AutoProgress."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import auto_progress
import unity_mcp


RESULT_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
ADAPTER_INTERFACE_VERSION = "1"
ADAPTER_IMPLEMENTATION_VERSION = "0.2.0"
MAX_FACT_ITEMS = 100
MAX_SUMMARY = 1000
PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


@dataclass(frozen=True)
class AdapterDescriptor:
    adapter_id: str
    kind: str
    capabilities: frozenset[str]
    interface_version: str = ADAPTER_INTERFACE_VERSION
    implementation_version: str = ADAPTER_IMPLEMENTATION_VERSION
    state_schema_version: int = STATE_SCHEMA_VERSION

    def state_metadata(self) -> dict[str, Any]:
        return {
            "id": self.adapter_id,
            "interface_version": self.interface_version,
            "implementation_version": self.implementation_version,
            "state_schema_version": self.state_schema_version,
        }


ADAPTER_REGISTRY = {
    "git": AdapterDescriptor(
        "git",
        "source_control",
        frozenset(
            {
                "identify_project",
                "fetch_base_snapshot",
                "isolated_change_context",
                "atomic_workspace_restore",
                "inspect_changes",
                "content_fingerprint",
                "record_change",
                "publish_change",
            }
        ),
    ),
    "github": AdapterDescriptor(
        "github",
        "review_host",
        frozenset(
            {
                "preflight",
                "find_open_review",
                "draft_review",
                "create_review",
                "inspect_review",
                "comment_on_review",
            }
        ),
    ),
}

TASK_CAPABILITIES = {
    "implement-batch": {
        "source_control": {
            "identify_project",
            "fetch_base_snapshot",
            "isolated_change_context",
            "atomic_workspace_restore",
            "inspect_changes",
            "content_fingerprint",
            "record_change",
            "publish_change",
        },
        "review_host": {"preflight", "find_open_review", "draft_review", "create_review"},
    },
    "discover-improvements": {
        "source_control": {
            "identify_project",
            "fetch_base_snapshot",
            "isolated_change_context",
            "atomic_workspace_restore",
            "inspect_changes",
            "content_fingerprint",
            "record_change",
            "publish_change",
        },
        "review_host": {"preflight", "find_open_review", "draft_review", "create_review"},
    },
}


def stage_result(
    stage: str,
    status: str,
    reason_code: str,
    summary: str,
    *,
    facts: dict[str, Any] | None = None,
    checkpoint: str | None = None,
    recovery: str = "none",
    diagnostic_ref: str | None = None,
    retryable: bool = False,
    attempts: int = 1,
) -> dict[str, Any]:
    legacy_status = status
    if status == "recovery_required":
        status = "attention"
    elif status == "failed_restored":
        status = "failed"
        facts = {**(facts or {}), "workspace_restored": True}
    elif status not in {"completed", "attention", "failed"}:
        raise ValueError(f"unsupported stage status: {legacy_status}")
    bounded: dict[str, Any] = {}
    for key, value in (facts or {}).items():
        if isinstance(value, list) and len(value) > MAX_FACT_ITEMS:
            bounded[key] = value[:MAX_FACT_ITEMS]
            bounded[f"{key}_truncated"] = True
        elif isinstance(value, str):
            bounded[key] = value[:MAX_SUMMARY]
            if len(value) > MAX_SUMMARY:
                bounded[f"{key}_truncated"] = True
        else:
            bounded[key] = value
    return {
        "ok": status == "completed",
        "schema_version": RESULT_SCHEMA_VERSION,
        "stage": stage,
        "status": status,
        "reason_code": reason_code,
        "summary": summary[:MAX_SUMMARY],
        "facts": bounded,
        "checkpoint": checkpoint,
        "recovery": recovery,
        "diagnostic_ref": diagnostic_ref,
        "retryable": retryable,
        "attempts": attempts,
    }


def _validate_identifier(value: str, field: str, pattern: re.Pattern[str]) -> None:
    if not pattern.fullmatch(value):
        raise auto_progress.AutoProgressError(f"invalid {field}", f"invalid_{field}")


class StateStore:
    """Atomic, repository-external state separated from the append-only ledger."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def run_dir(self, project_id: str, run_id: str) -> Path:
        _validate_identifier(project_id, "project_id", PROJECT_ID_RE)
        if not auto_progress.RUN_ID_PATTERN.fullmatch(run_id):
            raise auto_progress.AutoProgressError("invalid run_id", "invalid_run_id")
        return self.root / "runs" / project_id / run_id

    def state_path(self, project_id: str, run_id: str) -> Path:
        return self.run_dir(project_id, run_id) / "state.json"

    def load(self, project_id: str, run_id: str) -> dict[str, Any]:
        path = self.state_path(project_id, run_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise auto_progress.AutoProgressError("run state not found", "run_state_not_found") from exc
        except json.JSONDecodeError as exc:
            raise auto_progress.AutoProgressError("run state is corrupt", "run_state_corrupt") from exc
        if not isinstance(value, dict) or value.get("state_schema_version") != STATE_SCHEMA_VERSION:
            raise auto_progress.AutoProgressError(
                "run state schema needs a deterministic migration", "adapter_state_migration_required"
            )
        return value

    def save(self, state: dict[str, Any]) -> Path:
        destination = self.state_path(str(state["project_id"]), str(state["run_id"]))
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def unfinished(self, project_id: str, except_run_id: str | None = None) -> list[str]:
        _validate_identifier(project_id, "project_id", PROJECT_ID_RE)
        parent = self.root / "runs" / project_id
        if not parent.exists():
            return []
        found = []
        for path in parent.glob("*/state.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                found.append(path.parent.name)
                continue
            run_id = str(value.get("run_id", path.parent.name))
            if run_id != except_run_id and value.get("terminal") is not True:
                found.append(run_id)
        return sorted(found)

    def diagnostic_path(self, project_id: str, run_id: str, name: str) -> Path:
        safe = re.sub(r"[^a-z0-9_.-]+", "-", name.lower()).strip("-") or "diagnostic"
        path = self.run_dir(project_id, run_id) / "diagnostics" / f"{safe}.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def guidance_cache_path(self, project_id: str, document_path: str) -> Path:
        _validate_identifier(project_id, "project_id", PROJECT_ID_RE)
        key = hashlib.sha256(document_path.encode("utf-8")).hexdigest()[:20]
        path = self.root / "guidance" / project_id / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def _run(
    args: list[str],
    *,
    cwd: Path,
    timeout: int = 30,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:1000]
        raise auto_progress.AutoProgressError(
            f"{args[0]} command failed: {detail}", "external_command_failed"
        )
    return result


def _git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return _run(
        ["git", "-c", f"safe.directory={repo.resolve()}", *args],
        cwd=repo,
        check=check,
        env=env,
    )


def route_adapters(config: dict[str, Any], task_type: str) -> dict[str, AdapterDescriptor]:
    requirements = TASK_CAPABILITIES.get(task_type)
    if requirements is None:
        raise auto_progress.AutoProgressError("unsupported task type", "unsupported_task_type")
    tools = config["tools"]
    routed = {
        "source_control": ADAPTER_REGISTRY.get(str(tools.get("source_control"))),
        "review_host": ADAPTER_REGISTRY.get(str(tools.get("review_host"))),
    }
    for kind, adapter in routed.items():
        if adapter is None or adapter.kind != kind:
            raise auto_progress.AutoProgressError(
                f"{kind} adapter is not registered", "adapter_unregistered"
            )
        missing = sorted(requirements[kind] - adapter.capabilities)
        if missing:
            raise auto_progress.AutoProgressError(
                f"{adapter.adapter_id} lacks capabilities: {', '.join(missing)}",
                "adapter_capability_missing",
            )
    return {key: value for key, value in routed.items() if value is not None}


def _config_from_revision(repo: Path, revision: str) -> dict[str, Any]:
    result = _git(repo, "show", f"{revision}:.codex/auto-progress.toml", check=False)
    if result.returncode != 0:
        raise auto_progress.AutoProgressError(
            "base snapshot has no .codex/auto-progress.toml", "base_config_missing"
        )
    try:
        config = tomllib.loads(result.stdout)
    except tomllib.TOMLDecodeError as exc:
        raise auto_progress.AutoProgressError("base snapshot configuration is invalid", "config_invalid") from exc
    return auto_progress.validate_config(config)


def _refresh_repository_guidance(
    workspace: Path,
    config: dict[str, Any],
    store: StateStore,
    project_id: str,
) -> dict[str, Any]:
    """Refresh only changed guidance documents and cache each one separately."""
    documents: list[dict[str, Any]] = []
    digest_parts: list[str] = []
    for configured in config.get("repository_guidance", {}).get("documents", []):
        relative = str(configured["path"]).replace("\\", "/")
        blob = _git(workspace, "rev-parse", f"HEAD:{relative}", check=False)
        blob_sha = blob.stdout.strip() if blob.returncode == 0 else ""
        cache_path = store.guidance_cache_path(project_id, relative)
        cached: dict[str, Any] = {}
        if cache_path.exists():
            try:
                value = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    cached = value
            except (OSError, json.JSONDecodeError):
                cached = {}
        changed = cached.get("blob_sha") != blob_sha
        if changed:
            content = ""
            present = bool(blob_sha)
            if present:
                document_path = (workspace / relative).resolve()
                try:
                    document_path.relative_to(workspace.resolve())
                except ValueError as exc:
                    raise auto_progress.AutoProgressError(
                        "repository guidance path escapes the workspace",
                        "repository_guidance_invalid",
                    ) from exc
                content = document_path.read_text(encoding="utf-8")
            payload = {
                "agent": configured["agent"],
                "path": relative,
                "blob_sha": blob_sha,
                "present": present,
                "content": content,
                "content_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            temporary = cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temporary, cache_path)
            cached = payload
        digest_parts.append(f"{relative}\n{blob_sha}")
        documents.append(
            {
                "agent": configured["agent"],
                "path": relative,
                "configured_blob_sha": configured.get("blob_sha", ""),
                "blob_sha": blob_sha,
                "present": bool(blob_sha),
                "changed": changed,
                "cache_path": str(cache_path),
                "content_digest": cached.get("content_digest"),
            }
        )
    return {
        "digest": hashlib.sha256("\n\n".join(digest_parts).encode("utf-8")).hexdigest(),
        "documents": documents,
    }


def _head(repo: Path) -> tuple[str | None, str]:
    branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    name = branch.stdout.strip() if branch.returncode == 0 else None
    revision = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return name, revision


def _active_operations(repo: Path) -> list[str]:
    names = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG")
    found = []
    for name in names:
        raw = _git(repo, "rev-parse", "--git-path", name).stdout.strip()
        path = Path(raw) if Path(raw).is_absolute() else repo / raw
        if path.exists():
            found.append(name)
    return found


def _parse_status(raw: str) -> list[dict[str, str]]:
    parts = raw.split("\0")
    entries: list[dict[str, str]] = []
    index = 0
    while index < len(parts) and parts[index]:
        item = parts[index]
        if len(item) < 4:
            break
        xy, path = item[:2], item[3:]
        entry = {"xy": xy, "path": path.replace("\\", "/")}
        index += 1
        if "R" in xy or "C" in xy:
            if index < len(parts):
                entry["source_path"] = parts[index].replace("\\", "/")
                index += 1
        entries.append(entry)
    return entries


def _matches_additional_ignore(path: str, patterns: Iterable[str]) -> bool:
    candidate = PurePosixPath(path)
    for raw in patterns:
        pattern = raw.replace("\\", "/").lstrip("/")
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
        if candidate.match(pattern) or ("/" not in pattern and any(fnmatch_part == pattern for fnmatch_part in candidate.parts)):
            return True
        # PurePath follows pathlib semantics; this extra branch covers root-anchored ** forms.
        if fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def inspect_workspace(repo: Path, patterns: list[str]) -> dict[str, Any]:
    operations = _active_operations(repo)
    raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    entries = _parse_status(raw)
    ignored = []
    blocking = []
    untracked = [entry["path"] for entry in entries if entry["xy"] == "??"]
    additionally_ignored: set[str] = set()
    if patterns and untracked:
        with tempfile.TemporaryDirectory(prefix="auto-progress-ignore-") as directory:
            excludes = Path(directory) / "additional-ignore"
            excludes.write_text("\n".join(patterns) + "\n", encoding="utf-8", newline="\n")
            for path in untracked:
                matched = _git(
                    repo,
                    "-c",
                    f"core.excludesFile={excludes}",
                    "check-ignore",
                    "--no-index",
                    "--quiet",
                    "--",
                    path,
                    check=False,
                )
                if matched.returncode == 0:
                    additionally_ignored.add(path)
    for entry in entries:
        if entry["xy"] == "??" and entry["path"] in additionally_ignored:
            ignored.append(entry["path"])
        else:
            blocking.append(entry)
    return {"operations": operations, "ignored_untracked": sorted(ignored), "blocking": blocking}


def _target_collisions(repo: Path, base_revision: str, ignored_paths: list[str]) -> list[str]:
    if not ignored_paths:
        return []
    tracked = set(
        item
        for item in _git(repo, "ls-tree", "-r", "--name-only", "-z", base_revision).stdout.split("\0")
        if item
    )
    return sorted(path for path in ignored_paths if path in tracked)


def _validation_steps(
    repo: Path,
    config: dict[str, Any],
    store: StateStore,
    project_id: str,
    run_id: str,
    label: str,
) -> dict[str, Any]:
    results = []
    for index, step in enumerate(config["validation"]["steps"]):
        before = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=no").stdout
        cwd = (repo / step["working_directory"]).resolve()
        try:
            completed = _run(
                [step["program"], *step["args"]],
                cwd=cwd,
                timeout=int(step["timeout_minutes"]) * 60,
                check=False,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            completed = None
            timed_out = True
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "")
        else:
            stdout, stderr = completed.stdout, completed.stderr
        diagnostic = store.diagnostic_path(project_id, run_id, f"{label}-{index + 1}")
        diagnostic.write_text((stdout or "") + ("\n" if stdout and stderr else "") + (stderr or ""), encoding="utf-8")
        after = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=no").stdout
        side_effect = before != after
        passed = (
            not timed_out
            and completed is not None
            and completed.returncode in step["success_exit_codes"]
            and not side_effect
        )
        results.append(
            {
                "name": step["name"],
                "passed": passed,
                "exit_code": None if completed is None else completed.returncode,
                "timed_out": timed_out,
                "workspace_changed": side_effect,
                "diagnostic_ref": f"diagnostics/{diagnostic.name}",
            }
        )
        if not passed:
            break
    return {"passed": all(item["passed"] for item in results), "steps": results}


def _checkpoint(state: dict[str, Any], name: str, facts: dict[str, Any]) -> None:
    state.setdefault("checkpoints", {})[name] = {
        "completed_at": datetime.now().astimezone().isoformat(),
        "facts": facts,
        "adapters": state["adapters"],
    }
    state["current_stage"] = name


def _github_preflight(repo: Path, gh_program: str) -> dict[str, Any]:
    auth = _run([gh_program, "auth", "status"], cwd=repo, check=False)
    if auth.returncode != 0:
        raise auto_progress.AutoProgressError("GitHub CLI is not authenticated", "review_host_auth_failed")
    view = _run(
        [gh_program, "repo", "view", "--json", "nameWithOwner,viewerPermission"],
        cwd=repo,
        check=False,
    )
    if view.returncode != 0:
        raise auto_progress.AutoProgressError("GitHub repository is not accessible", "review_host_preflight_failed")
    try:
        data = json.loads(view.stdout)
    except json.JSONDecodeError as exc:
        raise auto_progress.AutoProgressError("GitHub CLI returned invalid JSON", "review_host_invalid_response") from exc
    permission = data.get("viewerPermission")
    if permission not in {"ADMIN", "MAINTAIN", "WRITE"}:
        raise auto_progress.AutoProgressError("GitHub write permission is required", "review_host_permission_denied")
    return {"repository": data.get("nameWithOwner"), "permission": permission}


def _restore_original(repo: Path, original_branch: str | None, original_head: str) -> bool:
    current_branch, current_head = _head(repo)
    if current_branch == original_branch and current_head == original_head:
        return True
    target = original_branch or original_head
    restored = _git(repo, "switch", target if original_branch else "--detach", *( [] if original_branch else [original_head]), check=False)
    return restored.returncode == 0 and _head(repo) == (original_branch, original_head)


def _branch_name(run_id: str, task_type: str) -> str:
    suffix = run_id.removeprefix("RUN-").lower()
    return f"codex/auto-progress/run-{suffix}-{task_type}"


def _retire_legacy_discovery_worktree(
    repo: Path, store: StateStore, project_id: str, patterns: list[str]
) -> bool:
    """Remove the old managed discovery checkout only when Git proves it is clean."""
    workspace = (store.root / "workspaces" / project_id / "discovery").resolve()
    if not workspace.exists():
        return False
    listed = _git(repo, "worktree", "list", "--porcelain").stdout.splitlines()
    registered = {
        str(Path(line.removeprefix("worktree ")).resolve()).casefold()
        for line in listed
        if line.startswith("worktree ")
    }
    if str(workspace).casefold() not in registered:
        raise auto_progress.AutoProgressError(
            "Legacy discovery workspace exists but is not a registered Git worktree",
            "legacy_discovery_workspace_needs_attention",
        )
    status = inspect_workspace(workspace, patterns)
    if status["blocking"] or status["operations"]:
        raise auto_progress.AutoProgressError(
            "Legacy discovery worktree contains changes and was preserved",
            "legacy_discovery_workspace_needs_attention",
        )
    removed = _git(repo, "worktree", "remove", str(workspace), check=False)
    if removed.returncode != 0:
        raise auto_progress.AutoProgressError(
            "Unable to retire the legacy discovery worktree safely",
            "legacy_discovery_workspace_needs_attention",
        )
    return True


def prepare_run(
    repo: Path,
    run_id: str,
    task_type: str,
    base_branch: str,
    state_root: Path,
    *,
    gh_program: str = "gh",
    skip_github: bool = False,
    trigger_source: str,
) -> dict[str, Any]:
    """Fetch a frozen policy, route adapters, admit a change context, and validate baseline."""
    repo = repo.resolve()
    if not auto_progress.RUN_ID_PATTERN.fullmatch(run_id):
        return stage_result("prepare-run", "failed_restored", "invalid_run_id", "Run ID is invalid")
    if trigger_source not in {"manual", "scheduled"}:
        return stage_result(
            "prepare-run", "failed_restored", "invalid_trigger_source",
            "Trigger source must be manual or scheduled",
        )
    if task_type != "implement-batch" and trigger_source == "scheduled":
        return stage_result(
            "prepare-run", "failed_restored", "scheduled_task_not_allowed",
            "Only scheduled implement-batch runs are supported",
        )
    try:
        remote = auto_progress.repository_remote(repo)
        project_id = auto_progress.make_project_id(remote, base_branch)
        store = StateStore(state_root)
        existing = store.unfinished(project_id, except_run_id=run_id)
        if existing:
            return stage_result(
                "prepare-run",
                "recovery_required",
                "unfinished_run",
                "An unfinished run must be recovered first",
                facts={"run_ids": existing},
                recovery="recover-run",
            )
        state_path = store.state_path(project_id, run_id)
        if state_path.exists():
            state = store.load(project_id, run_id)
            if "baseline_validation" in state.get("checkpoints", {}):
                return stage_result(
                    "prepare-run",
                    "completed",
                    "safe_retry",
                    "Run is already prepared",
                    facts={
                        "project_id": project_id,
                        "run_id": run_id,
                        "base_revision": state["base_revision"],
                        "workspace_handle": state["workspace_handle"],
                    },
                    checkpoint="baseline_validation",
                )

        if not auto_progress.SAFE_BRANCH.fullmatch(base_branch) or ".." in base_branch:
            raise auto_progress.AutoProgressError("base branch is invalid", "invalid_base_branch")
        fetched = _git(repo, "fetch", "--no-tags", "origin", f"refs/heads/{base_branch}", check=False)
        if fetched.returncode != 0:
            raise auto_progress.AutoProgressError("unable to fetch base branch", "fetch_base_failed")
        base_revision = _git(repo, "rev-parse", "FETCH_HEAD").stdout.strip()
        config = _config_from_revision(repo, base_revision)
        if config["project"]["base_branch"] != base_branch:
            raise auto_progress.AutoProgressError(
                "fetched policy base branch does not match registered base branch",
                "base_policy_mismatch",
            )
        adapters = route_adapters(config, task_type)
        review_facts = {"skipped": True} if skip_github else _github_preflight(repo, gh_program)

        branch = _branch_name(run_id, task_type)
        original_branch, original_head = _head(repo)
        state = {
            "state_schema_version": STATE_SCHEMA_VERSION,
            "project_id": project_id,
            "run_id": run_id,
            "task_type": task_type,
            "trigger_source": trigger_source,
            "terminal": False,
            "current_stage": "fetch_base",
            "created_at": datetime.now().astimezone().isoformat(),
            "repo_path": str(repo),
            "base_branch": base_branch,
            "base_revision": base_revision,
            "config": config,
            "config_digest": hashlib.sha256(
                _git(repo, "show", f"{base_revision}:.codex/auto-progress.toml").stdout.encode("utf-8")
            ).hexdigest(),
            "adapters": {kind: adapter.state_metadata() for kind, adapter in adapters.items()},
            "original": {"branch": original_branch, "head": original_head},
            "git_identity": _identity(repo),
            "branch": branch,
            "workspace_mode": "primary",
            "workspace_path": None,
            "workspace_handle": hashlib.sha256(f"{project_id}\n{run_id}\nworkspace".encode()).hexdigest()[:20],
            "checkpoints": {},
            "recovery_obligations": [],
            "gh_program": gh_program,
            "skip_github": skip_github,
        }
        _checkpoint(
            state,
            "fetch_base",
            {"base_revision": base_revision, "config_digest": state["config_digest"]},
        )
        store.save(state)

        patterns = config["workspace"]["additional_ignore_patterns"]
        retired_legacy_workspace = _retire_legacy_discovery_worktree(
            repo, store, project_id, patterns
        )
        admission = inspect_workspace(repo, patterns)
        if admission["operations"]:
            raise auto_progress.AutoProgressError("Git operation is active", "active_git_operation")
        if admission["blocking"]:
            raise auto_progress.AutoProgressError("Workspace contains unapproved changes", "dirty_workspace")
        collisions = _target_collisions(repo, base_revision, admission["ignored_untracked"])
        if collisions:
            raise auto_progress.AutoProgressError(
                "Additional-ignore path collides with the base snapshot: " + ", ".join(collisions[:20]),
                "untracked_target_collision",
            )

        existing_branch = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
        if existing_branch.returncode == 0:
            raise auto_progress.AutoProgressError("Run branch already exists", "change_context_exists")

        if trigger_source == "scheduled" and task_type == "implement-batch":
            allowance = auto_progress.claim_daily_allowance(
                project_id,
                state_root.resolve() / "ledger",
                config["project"]["timezone"],
                run_id,
                task_type,
                trigger_source,
            )
            state["allowance"] = {key: value for key, value in allowance.items() if key != "ledger"}
            store.save(state)

        created = False
        workspace = repo
        try:
            switched = _git(repo, "switch", "-c", branch, base_revision, check=False)
            if switched.returncode != 0:
                raise auto_progress.AutoProgressError("Unable to create run branch", "admit_workspace_failed")
            created = True
            state["workspace_path"] = str(workspace.resolve())
            state["recovery_obligations"] = ["restore_workspace"]
            store.save(state)

            target = inspect_workspace(workspace, patterns)
            if target["operations"]:
                raise auto_progress.AutoProgressError("Git operation is active in change context", "active_git_operation")
            if target["blocking"]:
                raise auto_progress.AutoProgressError("Change context is not clean", "dirty_change_context")
            _checkpoint(
                state,
                "admit_workspace",
                {
                    "workspace_handle": state["workspace_handle"],
                    "branch": branch,
                    "ignored_untracked": target["ignored_untracked"],
                    "retired_legacy_discovery_worktree": retired_legacy_workspace,
                },
            )
            store.save(state)

            if task_type == "implement-batch":
                guidance = _refresh_repository_guidance(
                    workspace, config, store, project_id
                )
                state["repository_guidance"] = guidance
                _checkpoint(state, "repository_guidance", guidance)
                store.save(state)

            baseline = (
                {"passed": True, "skipped": True, "reason": "discovery_does_not_run_csharp_validation", "steps": []}
                if task_type == "discover-improvements"
                else _validation_steps(workspace, config, store, project_id, run_id, "baseline")
            )
            if not baseline["passed"]:
                last = baseline["steps"][-1] if baseline["steps"] else {}
                if not last.get("timed_out") and not last.get("workspace_changed"):
                    state["baseline_compile_repair"] = True
                    _checkpoint(state, "baseline_validation", baseline)
                    store.save(state)
                    return stage_result(
                        "prepare-run",
                        "completed",
                        "baseline_compile_repair_required",
                        "Base snapshot does not pass configured validation; run is prepared for exclusive compile repair",
                        facts={
                            "project_id": project_id,
                            "run_id": run_id,
                            "task_type": task_type,
                            "base_revision": base_revision,
                            "workspace_handle": state["workspace_handle"],
                            "branch": branch,
                            "validation": baseline,
                        },
                        checkpoint="baseline_validation",
                        diagnostic_ref=last.get("diagnostic_ref"),
                    )
                raise auto_progress.AutoProgressError(
                    "baseline validation timed out or changed tracked files",
                    "baseline_validation_invalid",
                )
            _checkpoint(state, "baseline_validation", baseline)
            store.save(state)
            return stage_result(
                "prepare-run",
                "completed",
                "prepared",
                "Run is prepared",
                facts={
                    "project_id": project_id,
                    "run_id": run_id,
                    "task_type": task_type,
                    "base_revision": base_revision,
                    "base_branch": base_branch,
                    "workspace_handle": state["workspace_handle"],
                    "branch": branch,
                    "ignored_untracked": admission["ignored_untracked"],
                    "review_host": review_facts,
                    "repository_guidance": state.get("repository_guidance"),
                },
                checkpoint="baseline_validation",
            )
        except auto_progress.AutoProgressError as exc:
            if created:
                if task_type == "discover-improvements":
                    clean = inspect_workspace(workspace, patterns) if workspace.exists() else {"blocking": []}
                    if not clean["blocking"]:
                        parked = _git(workspace, "switch", "--detach", base_revision, check=False)
                        deleted = _git(repo, "branch", "-D", branch, check=False) if parked.returncode == 0 else parked
                        exists = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
                        created = not (parked.returncode == 0 and deleted.returncode == 0 and exists.returncode != 0)
                else:
                    if _restore_original(repo, original_branch, original_head):
                        tip = _git(repo, "rev-parse", branch, check=False).stdout.strip()
                        if tip == base_revision:
                            deleted = _git(repo, "branch", "-D", branch, check=False)
                            exists = _git(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
                            created = not (deleted.returncode == 0 and exists.returncode != 0)
            if not created:
                state["recovery_obligations"] = []
                state["terminal"] = True
                state["terminal_result"] = "failed"
                store.save(state)
                return stage_result(
                    "prepare-run",
                    "failed_restored",
                    exc.reason_code,
                    str(exc),
                    facts={"project_id": project_id, "run_id": run_id},
                )
            store.save(state)
            return stage_result(
                "prepare-run",
                "recovery_required",
                exc.reason_code,
                str(exc),
                facts={"project_id": project_id, "run_id": run_id},
                checkpoint="fetch_base",
                recovery="recover-run",
            )
    except (auto_progress.AutoProgressError, OSError, subprocess.TimeoutExpired) as exc:
        reason = exc.reason_code if isinstance(exc, auto_progress.AutoProgressError) else "prepare_run_failed"
        if "state" in locals() and "store" in locals() and not state.get("recovery_obligations"):
            state["terminal"] = True
            state["terminal_result"] = "failed"
            try:
                store.save(state)
            except OSError:
                pass
        return stage_result("prepare-run", "failed_restored", reason, str(exc))


def _index_snapshot(
    repo: Path, base_revision: str, additional_ignore_patterns: list[str] | None = None
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="auto-progress-index-") as directory:
        index = Path(directory) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        _git(repo, "read-tree", base_revision, env=env)
        add_args = ["add", "-A", "--", "."]
        if additional_ignore_patterns:
            excludes = Path(directory) / "additional-ignore"
            excludes.write_text(
                "\n".join(additional_ignore_patterns) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            add_args = ["-c", f"core.excludesFile={excludes}", *add_args]
        _git(repo, *add_args, env=env)
        names_raw = _git(repo, "diff", "--cached", "--name-status", "-z", base_revision, env=env).stdout
        parts = names_raw.split("\0")
        entries: list[dict[str, Any]] = []
        cursor = 0
        while cursor < len(parts) and parts[cursor]:
            status = parts[cursor]
            cursor += 1
            if status.startswith(("R", "C")):
                old_path, path = parts[cursor], parts[cursor + 1]
                cursor += 2
            else:
                old_path, path = None, parts[cursor]
                cursor += 1
            staged = _git(repo, "ls-files", "-s", "--", path, env=env, check=False).stdout.strip()
            mode = blob = "-"
            if staged:
                fields = staged.split(maxsplit=3)
                if len(fields) >= 2:
                    mode, blob = fields[0], fields[1]
            entry = {
                "status": status,
                "path": path.replace("\\", "/"),
                "mode": mode,
                "blob": blob,
            }
            if old_path:
                entry["old_path"] = old_path.replace("\\", "/")
            entries.append(entry)
        numstat_raw = _git(repo, "diff", "--cached", "--numstat", "-z", base_revision, env=env).stdout
        numstat_parts = numstat_raw.split("\0")
        stats: list[tuple[int, int]] = []
        cursor = 0
        while cursor < len(numstat_parts) and numstat_parts[cursor]:
            fields = numstat_parts[cursor].split("\t")
            cursor += 1
            if len(fields) < 3:
                continue
            added = int(fields[0]) if fields[0].isdigit() else 0
            deleted = int(fields[1]) if fields[1].isdigit() else 0
            if not fields[2] and cursor + 1 < len(numstat_parts):
                cursor += 2
            stats.append((added, deleted))
        for entry, (added, deleted) in zip(entries, stats, strict=False):
            entry["added_lines"] = added
            entry["deleted_lines"] = deleted
        return _snapshot_from_entries(entries)


def _snapshot_from_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    changed_paths = sorted(
        {
            path
            for item in entries
            for path in (item["path"], item.get("old_path"))
            if path
        }
    )
    return {
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "entries": entries,
        "changed_paths": changed_paths,
        "changed_lines": sum(
            int(item.get("added_lines", 0)) + int(item.get("deleted_lines", 0))
            for item in entries
        ),
        "all_files": len(entries),
        "csharp_files": sum(item["path"].lower().endswith(".cs") for item in entries),
    }


def _entry_paths(entry: dict[str, Any]) -> set[str]:
    return {
        path
        for path in (entry.get("path"), entry.get("old_path"))
        if isinstance(path, str) and path
    }


def _partition_snapshot(
    snapshot: dict[str, Any], target_paths: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    target_entries: list[dict[str, Any]] = []
    outside_entries: list[dict[str, Any]] = []
    for entry in snapshot["entries"]:
        paths = _entry_paths(entry)
        overlap = paths & target_paths
        if overlap and overlap != paths:
            raise auto_progress.AutoProgressError(
                "a changed file operation crosses target and non-target paths",
                "human_change_target_overlap",
            )
        (target_entries if paths and paths <= target_paths else outside_entries).append(entry)
    return _snapshot_from_entries(target_entries), _snapshot_from_entries(outside_entries)


def _policy_path_match(path: str, patterns: list[str]) -> bool:
    candidate = PurePosixPath(path)
    for raw in patterns:
        pattern = raw.replace("\\", "/")
        if pattern.endswith("/") and (path == pattern.rstrip("/") or path.startswith(pattern)):
            return True
        if candidate.match(pattern) or fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def _validate_manifest(
    manifest: dict[str, Any],
    task_type: str,
    snapshot: dict[str, Any],
    config: dict[str, Any],
    *,
    allow_outside_changes: bool = False,
) -> dict[str, Any]:
    allowed_top = {"improvements", "items", "run_record_path", "template_id"}
    forbidden = sorted(set(manifest) - allowed_top)
    if forbidden:
        raise auto_progress.AutoProgressError(
            "delivery manifest contains fact fields: " + ", ".join(forbidden), "manifest_fact_override"
        )
    items = manifest.get("improvements", manifest.get("items"))
    if not isinstance(items, list) or not items:
        raise auto_progress.AutoProgressError("delivery manifest has no improvements", "manifest_invalid")
    normalized = []
    owned: dict[str, str] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise auto_progress.AutoProgressError("manifest item must be an object", "manifest_invalid")
        permitted = {"id", "summary", "acceptance", "design_tradeoffs", "tradeoffs", "expected_paths"}
        extra = sorted(set(item) - permitted)
        if extra:
            raise auto_progress.AutoProgressError(
                f"manifest item {index} contains fact fields: {', '.join(extra)}", "manifest_fact_override"
            )
        improvement_id = item.get("id")
        if not isinstance(improvement_id, str) or not re.fullmatch(r"IMP-\d{4}\.\d{2}\.\d{2}-[0-9a-f]{8}", improvement_id):
            raise auto_progress.AutoProgressError("manifest improvement ID is invalid", "manifest_invalid")
        expected = item.get("expected_paths")
        if not isinstance(expected, list) or not expected or not all(isinstance(path, str) for path in expected):
            raise auto_progress.AutoProgressError("manifest expected_paths is invalid", "manifest_invalid")
        for path in expected:
            auto_progress.validate_relative_path(path, "manifest.expected_paths")
            path = path.replace("\\", "/")
            if path in owned:
                raise auto_progress.AutoProgressError(
                    f"path ownership overlaps: {path}", "manifest_ownership_overlap"
                )
            owned[path] = improvement_id
        for field in ("summary", "acceptance"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise auto_progress.AutoProgressError(f"manifest {field} is required", "manifest_invalid")
        tradeoffs = item.get("design_tradeoffs", item.get("tradeoffs", ""))
        if not isinstance(tradeoffs, str):
            raise auto_progress.AutoProgressError("manifest design_tradeoffs must be text", "manifest_invalid")
        normalized.append(
            {
                "id": improvement_id,
                "summary": item["summary"].strip(),
                "acceptance": item["acceptance"].strip(),
                "design_tradeoffs": tradeoffs.strip(),
                "expected_paths": [path.replace("\\", "/") for path in expected],
                "result": "succeeded",
            }
        )
    run_record = manifest.get("run_record_path")
    if not isinstance(run_record, str):
        raise auto_progress.AutoProgressError("run_record_path is required", "manifest_invalid")
    auto_progress.validate_relative_path(run_record, "manifest.run_record_path")
    run_record = run_record.replace("\\", "/")
    if not run_record.startswith("docs/auto-progress/runs/") or not run_record.endswith(".md"):
        raise auto_progress.AutoProgressError(
            "run_record_path must be a Markdown file under docs/auto-progress/runs", "manifest_invalid"
        )
    expected_all = set(owned)
    target_snapshot, outside_snapshot = _partition_snapshot(snapshot, expected_all)
    missing = sorted(expected_all - set(target_snapshot["changed_paths"]))
    unexpected = outside_snapshot["changed_paths"]
    if missing or (unexpected and not allow_outside_changes):
        raise auto_progress.AutoProgressError(
            f"manifest path mismatch; missing={missing[:20]}, unexpected={unexpected[:20]}",
            "manifest_mismatch",
        )
    allowed = config["paths"]["allowed"]
    excluded = config["paths"]["excluded"]
    document_roots = [
        config["paths"]["ideas"].rstrip("/") + "/**",
        config["paths"]["directed"].rstrip("/") + "/**",
        config["paths"]["rejections"].rstrip("/") + "/**",
    ]
    if config["paths"].get("rejection_rules"):
        document_roots.append(config["paths"]["rejection_rules"])
    out_of_scope = [
        path
        for path in target_snapshot["changed_paths"]
        if (not _policy_path_match(path, allowed + document_roots)) or _policy_path_match(path, excluded)
    ]
    if out_of_scope:
        raise auto_progress.AutoProgressError(
            "changes are outside policy: " + ", ".join(sorted(out_of_scope)[:20]), "path_policy_violation"
        )
    budget = config["batch_budget"] if task_type == "implement-batch" else config["directed_budget"]
    limits = {
        "csharp_files": budget.get("csharp_files_hard", budget.get("csharp_files_absolute")),
        "changed_lines": budget.get("changed_lines_hard", budget.get("changed_lines_absolute")),
        "all_files": budget.get("all_files_hard", budget.get("all_files_absolute")),
    }
    exceeded = [name for name, limit in limits.items() if target_snapshot[name] > limit]
    if exceeded:
        raise auto_progress.AutoProgressError(
            "change budget exceeded: " + ", ".join(exceeded), "budget_exceeded"
        )
    return {
        "improvements": normalized,
        "run_record_path": run_record,
        "template_id": manifest.get("template_id"),
        "ownership": owned,
        "budget_limits": limits,
        "target_snapshot": target_snapshot,
        "outside_snapshot": outside_snapshot,
    }


def _capture_bypass_changes(
    state: dict[str, Any], workspace: Path, outside_snapshot: dict[str, Any]
) -> dict[str, Any]:
    paths = set(outside_snapshot["changed_paths"])
    if not paths:
        if _git(workspace, "diff", "--cached", "--quiet", check=False).returncode != 0:
            raise auto_progress.AutoProgressError(
                "target paths must not be staged before deterministic delivery",
                "staged_target_changes_present",
            )
        return {"paths": [], "fingerprint": outside_snapshot["fingerprint"], "unstaged_paths": []}
    if state["task_type"] != "implement-batch":
        raise auto_progress.AutoProgressError(
            "discovery cannot bypass source changes", "manifest_mismatch"
        )

    raw = _git(workspace, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    status_entries = _parse_status(raw)
    staged_paths: set[str] = set()
    for entry in status_entries:
        entry_paths = {entry["path"], *([entry["source_path"]] if entry.get("source_path") else [])}
        if entry_paths & paths and entry["xy"] != "??" and entry["xy"][0] != " ":
            staged_paths.update(entry_paths & paths)
    if staged_paths:
        unstaged = _git(
            workspace,
            "restore",
            "--staged",
            "--",
            *sorted(staged_paths),
            check=False,
        )
        if unstaged.returncode != 0:
            raise auto_progress.AutoProgressError(
                "unable to unstage non-target human changes",
                "bypass_unstage_failed",
            )
    if _git(workspace, "diff", "--cached", "--quiet", check=False).returncode != 0:
        raise auto_progress.AutoProgressError(
            "target paths must not be staged before deterministic delivery",
            "staged_target_changes_present",
        )

    original = state["original"]
    original_head = original["head"]
    original_branch = original.get("branch")
    if original_branch:
        current_original = _git(
            workspace, "rev-parse", f"refs/heads/{original_branch}", check=False
        )
        if current_original.returncode != 0 or current_original.stdout.strip() != original_head:
            raise auto_progress.AutoProgressError(
                "original branch moved during the workspace lease",
                "original_branch_moved",
            )
    compatible = _git(
        workspace,
        "diff",
        "--quiet",
        state["base_revision"],
        original_head,
        "--",
        *sorted(paths),
        check=False,
    )
    if compatible.returncode != 0:
        raise auto_progress.AutoProgressError(
            "non-target human changes cannot be carried safely to the original revision",
            "bypass_restore_conflict",
        )
    return {
        "paths": sorted(paths),
        "fingerprint": outside_snapshot["fingerprint"],
        "unstaged_paths": sorted(staged_paths),
    }


def _verify_frozen_bypass(
    state: dict[str, Any], workspace: Path, target_paths: set[str]
) -> dict[str, Any]:
    original = state["original"]
    if original.get("branch"):
        current_original = _git(
            workspace,
            "rev-parse",
            f"refs/heads/{original['branch']}",
            check=False,
        )
        if current_original.returncode != 0 or current_original.stdout.strip() != original["head"]:
            raise auto_progress.AutoProgressError(
                "original branch moved during the workspace lease",
                "original_branch_moved",
            )
    snapshot = _index_snapshot(
        workspace,
        state["base_revision"],
        state["config"]["workspace"]["additional_ignore_patterns"],
    )
    target_snapshot, outside_snapshot = _partition_snapshot(snapshot, target_paths)
    bypass = state.get("bypass_changes", {"paths": [], "fingerprint": _snapshot_from_entries([])["fingerprint"]})
    if (
        outside_snapshot["changed_paths"] != bypass.get("paths", [])
        or outside_snapshot["fingerprint"] != bypass.get("fingerprint")
    ):
        raise auto_progress.AutoProgressError(
            "non-target changes appeared or changed after finish-run started",
            "bypass_content_changed",
        )
    return target_snapshot


def _verify_delivery_content(
    state: dict[str, Any],
    workspace: Path,
    allowed_target_paths: set[str],
    deterministic_paths: set[str],
) -> dict[str, Any]:
    target_snapshot = _verify_frozen_bypass(state, workspace, allowed_target_paths)
    unexpected = sorted(set(target_snapshot["changed_paths"]) - allowed_target_paths)
    if unexpected:
        raise auto_progress.AutoProgressError(
            "new target-adjacent changes appeared during delivery: " + ", ".join(unexpected[:20]),
            "content_changed",
        )
    model_entries = [
        entry
        for entry in target_snapshot["entries"]
        if not (_entry_paths(entry) & deterministic_paths)
    ]
    if _snapshot_from_entries(model_entries)["fingerprint"] != state.get(
        "validated_content_fingerprint"
    ):
        raise auto_progress.AutoProgressError(
            "model content no longer matches the validated fingerprint",
            "content_changed",
        )
    return target_snapshot


def _validate_improvement_documents(
    workspace: Path,
    manifest: dict[str, Any],
    task_type: str,
    config: dict[str, Any],
) -> None:
    allowed_states = {"queued", "implemented", "cancelled"}
    for item in manifest["improvements"]:
        improvement_id = item["id"]
        if task_type == "discover-improvements":
            expected = (
                Path(config["paths"]["ideas"])
                / f"{improvement_id}--queued.md"
            ).as_posix()
            if item["expected_paths"] != [expected]:
                raise auto_progress.AutoProgressError(
                    f"discovery document must be {expected}",
                    "improvement_filename_invalid",
                )
            candidates = [workspace / expected]
        else:
            candidates = []
            for root_key in ("ideas", "directed"):
                root = workspace / config["paths"][root_key]
                candidates.extend(
                    path
                    for path in (
                        root / f"{improvement_id}.md",
                        root / f"{improvement_id}--queued.md",
                        root / f"{improvement_id}--implemented.md",
                        root / f"{improvement_id}--cancelled.md",
                    )
                    if path.exists()
                )
            if not candidates:
                continue
        if len(candidates) != 1:
            raise auto_progress.AutoProgressError(
                f"expected one improvement document for {improvement_id}",
                "improvement_document_ambiguous",
            )
        document = candidates[0]
        text = document.read_text(encoding="utf-8")
        state_match = re.search(r"(?m)^state:\s*([a-z-]+)\s*$", text)
        id_match = re.search(r"(?m)^id:\s*(IMP-[^\s]+)\s*$", text)
        if not state_match or state_match.group(1) not in allowed_states:
            raise auto_progress.AutoProgressError(
                f"{document.relative_to(workspace)} has an invalid state",
                "improvement_state_invalid",
            )
        if not id_match or id_match.group(1) != improvement_id:
            raise auto_progress.AutoProgressError(
                f"{document.relative_to(workspace)} has a mismatched improvement ID",
                "improvement_id_mismatch",
            )
        state = state_match.group(1)
        suffix = re.search(r"--([a-z-]+)\.md$", document.name)
        if suffix and suffix.group(1) != state:
            raise auto_progress.AutoProgressError(
                f"{document.relative_to(workspace)} filename and frontmatter state differ",
                "improvement_state_mismatch",
            )
        if state != "queued":
            raise auto_progress.AutoProgressError(
                f"{document.relative_to(workspace)} is not queued",
                "improvement_state_invalid",
            )


def _identity(repo: Path) -> dict[str, str]:
    result = {
        key: _git(repo, "config", "--get", key, check=False).stdout.strip()
        for key in ("user.name", "user.email")
    }
    if not result["user.name"] or not result["user.email"]:
        raise auto_progress.AutoProgressError("Git identity is missing", "git_identity_missing")
    return result


def _open_review(repo: Path, gh: str, branch: str) -> dict[str, Any] | None:
    result = _run(
        [gh, "pr", "list", "--head", branch, "--state", "open", "--json", "number,url,isDraft,headRefOid,baseRefName"],
        cwd=repo,
        check=False,
    )
    if result.returncode != 0:
        raise auto_progress.AutoProgressError("Unable to query reviews", "review_query_failed")
    try:
        values = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise auto_progress.AutoProgressError("Review host returned invalid JSON", "review_host_invalid_response") from exc
    if not isinstance(values, list):
        raise auto_progress.AutoProgressError("Review query result is invalid", "review_host_invalid_response")
    if len(values) > 1:
        raise auto_progress.AutoProgressError("Multiple open reviews use this branch", "review_state_ambiguous")
    return values[0] if values else None


def _check_review_overlap(
    repo: Path, gh: str, branch: str, task_type: str, changed_paths: list[str]
) -> None:
    listed = _run(
        [gh, "pr", "list", "--state", "open", "--json", "number,headRefName,baseRefName"],
        cwd=repo,
        check=False,
    )
    if listed.returncode != 0:
        raise auto_progress.AutoProgressError("Unable to inspect open reviews", "review_query_failed")
    try:
        reviews = json.loads(listed.stdout)
    except json.JSONDecodeError as exc:
        raise auto_progress.AutoProgressError("Review host returned invalid JSON", "review_host_invalid_response") from exc
    if not isinstance(reviews, list):
        raise auto_progress.AutoProgressError("Review list is invalid", "review_host_invalid_response")
    own_kind = "discover-improvements" if task_type == "discover-improvements" else "implement-batch"
    for review in reviews:
        if not isinstance(review, dict) or review.get("headRefName") == branch:
            continue
        head = str(review.get("headRefName", ""))
        if not head.startswith("codex/auto-progress/"):
            continue
        other_kind = "discover-improvements" if head.endswith("-discover-improvements") else "implement-batch"
        if other_kind == own_kind:
            raise auto_progress.AutoProgressError(
                f"another open {own_kind} review already exists", "review_type_already_open"
            )
        viewed = _run(
            [gh, "pr", "view", str(review.get("number")), "--json", "files"],
            cwd=repo,
            check=False,
        )
        if viewed.returncode != 0:
            raise auto_progress.AutoProgressError("Unable to inspect review paths", "review_query_failed")
        try:
            payload = json.loads(viewed.stdout)
        except json.JSONDecodeError as exc:
            raise auto_progress.AutoProgressError("Review files response is invalid", "review_host_invalid_response") from exc
        other_paths = {
            item.get("path")
            for item in payload.get("files", [])
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        overlap = sorted(set(changed_paths) & other_paths)
        if overlap:
            raise auto_progress.AutoProgressError(
                "changed paths overlap another AutoProgress review: " + ", ".join(overlap[:20]),
                "review_path_overlap",
            )


def _append_material_events(
    state: dict[str, Any],
    store: StateStore,
    revision: str,
    review: dict[str, Any] | None,
    *,
    include_commit_events: bool = True,
) -> None:
    config = state["config"]
    timezone = config["project"]["timezone"]
    local = datetime.now(ZoneInfo(timezone))
    ledger_root = store.root / "ledger"
    base = {
        "maintenance_day": local.strftime("%Y-%m-%d"),
        "timestamp": local.isoformat(),
        "run_id": state["run_id"],
        "task_type": state["task_type"],
        "trigger_source": state.get("trigger_source", "legacy"),
    }
    run_date = str(state["run_id"])[4:14]

    def event_id(event_type: str, qualifier: str = "") -> str:
        digest = hashlib.sha256(
            f"{state['project_id']}\n{state['run_id']}\n{event_type}\n{qualifier}".encode("utf-8")
        ).hexdigest()[:8]
        return f"EVT-{run_date}-{digest}"

    item_revisions = state.get("item_revisions", {})
    events = []
    if include_commit_events:
        events.extend(
            {
                **base,
                "event_id": event_id("commit_created", improvement_id),
                "event_type": "commit_created",
                "improvement_id": improvement_id,
                "revision": item_revision[:12],
            }
            for improvement_id, item_revision in sorted(item_revisions.items())
        )
        if state["task_type"] == "implement-batch":
            events.extend(
                {
                    **base,
                    "event_id": event_id("improvement_implemented", improvement_id),
                    "event_type": "improvement_implemented",
                    "improvement_id": improvement_id,
                    "revision": item_revision[:12],
                }
                for improvement_id, item_revision in sorted(item_revisions.items())
            )
    if review is not None:
        events.extend([
            {
                **base,
                "event_id": event_id("branch_pushed"),
                "event_type": "branch_pushed",
                "revision": revision[:12],
                "improvement_ids": sorted(item_revisions),
            },
            {
                **base,
                "event_id": event_id("pr_opened"),
                "event_type": "pr_opened",
                "pull_request": review.get("number"),
                "improvement_ids": sorted(item_revisions),
            },
        ])
    for event in events:
        auto_progress.append_ledger(event, state["project_id"], ledger_root, timezone)


def _logged_item_commits(state: dict[str, Any], workspace: Path) -> dict[str, str] | None:
    manifest = state.get("delivery_manifest")
    if not isinstance(manifest, dict):
        return None
    expected = [item["id"] for item in manifest.get("improvements", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
    log = _git(
        workspace,
        "log",
        "--format=%H%x09%s",
        f"{state['base_revision']}..HEAD",
        check=False,
    )
    if log.returncode != 0:
        return None
    found: dict[str, str] = {}
    recognized_revisions: set[str] = set()
    for line in log.stdout.splitlines():
        revision, separator, subject = line.partition("\t")
        if not separator:
            continue
        for improvement_id in expected:
            if subject == f"AutoProgress: {improvement_id} ({state['run_id']})":
                found[improvement_id] = revision
                recognized_revisions.add(revision)
    log_revisions = {line.partition("\t")[0] for line in log.stdout.splitlines() if "\t" in line}
    if log_revisions - recognized_revisions:
        return None
    expected_prefix = expected[: len(found)]
    if set(found) != set(expected_prefix):
        return None
    return found


def _resume_record_change(
    state: dict[str, Any], workspace: Path, store: StateStore
) -> dict[str, str] | None:
    manifest = state.get("delivery_manifest")
    facts = state.get("delivery_facts")
    if not isinstance(manifest, dict) or not isinstance(facts, dict):
        return None
    if "render_run_record" not in state.get("checkpoints", {}):
        return None
    import render_review

    record_path = workspace / manifest["run_record_path"]
    expected_record = render_review.render_document("run-record", manifest, facts)["body"]
    try:
        actual_record = record_path.read_text(encoding="utf-8")
    except OSError:
        return None
    if actual_record != expected_record:
        raise auto_progress.AutoProgressError(
            "existing run record does not match deterministic output", "run_record_content_mismatch"
        )
    revisions = _logged_item_commits(state, workspace)
    if revisions is None:
        return None
    items = manifest["improvements"]
    deterministic_paths = {manifest["run_record_path"]}
    allowed_target_paths = set(manifest["ownership"]) | deterministic_paths
    for index, item in enumerate(items):
        item_status_paths = _transition_improvement_implemented(
            state, workspace, item
        )
        deterministic_paths.update(item_status_paths)
        allowed_target_paths.update(item_status_paths)
        _verify_delivery_content(
            state, workspace, allowed_target_paths, deterministic_paths
        )
        if item["id"] in revisions:
            continue
        if _identity(workspace) != state["git_identity"]:
            raise auto_progress.AutoProgressError(
                "Git identity changed after preparation", "identity_mismatch"
            )
        paths = list(item["expected_paths"]) + item_status_paths
        if index == 0:
            paths.append(manifest["run_record_path"])
        _git(workspace, "add", "-A", "--", *paths)
        committed = _git(
            workspace,
            "commit",
            "-m",
            f"AutoProgress: {item['id']} ({state['run_id']})",
            check=False,
        )
        if committed.returncode != 0:
            raise auto_progress.AutoProgressError(
                "unable to resume delivery commit", "record_change_failed"
            )
        revisions[item["id"]] = _git(workspace, "rev-parse", "HEAD").stdout.strip()
        state["item_revisions"] = dict(revisions)
        _checkpoint(
            state,
            f"record_change_{item['id']}",
            {"revision": revisions[item["id"]], "improvement_id": item["id"], "recovered": True},
        )
        store.save(state)
    _verify_delivery_content(
        state, workspace, allowed_target_paths, deterministic_paths
    )
    if _active_operations(workspace):
        return None
    facts = dict(state["delivery_facts"])
    final_revision = _git(workspace, "rev-parse", "HEAD").stdout.strip()
    facts["revision"] = final_revision
    facts["work_branch"] = state["branch"]
    facts["improvements"] = {
        item["id"]: {"result": "implemented", "revision": revisions[item["id"]]}
        for item in items
    }
    template = (
        "discover-improvements-review"
        if state["task_type"] == "discover-improvements"
        else "implement-batch-review"
    )
    state["review_document"] = render_review.render_document(template, manifest, facts)
    store.save(state)
    return revisions


def _transition_improvement_implemented(
    state: dict[str, Any], workspace: Path, item: dict[str, Any]
) -> list[str]:
    """Prepare one deterministic queued-to-implemented transition for its item commit."""
    if state["task_type"] != "implement-batch":
        return []
    improvement_id = item["id"]
    queued_matches: list[Path] = []
    implemented_matches: list[Path] = []
    for root_key in ("ideas", "directed"):
        root = workspace / state["config"]["paths"][root_key]
        for name in (f"{improvement_id}.md", f"{improvement_id}--queued.md"):
            candidate = root / name
            if candidate.exists():
                queued_matches.append(candidate)
        implemented = root / f"{improvement_id}--implemented.md"
        if implemented.exists():
            implemented_matches.append(implemented)
    if not queued_matches and len(implemented_matches) == 1:
        destination = implemented_matches[0]
        text = destination.read_text(encoding="utf-8")
        if not re.search(r"(?m)^state:\s*implemented\s*$", text):
            raise auto_progress.AutoProgressError(
                f"{destination.relative_to(workspace)} is not implemented",
                "improvement_state_invalid",
            )
        legacy = destination.with_name(f"{improvement_id}.md")
        queued = destination.with_name(f"{improvement_id}--queued.md")
        return [
            legacy.relative_to(workspace).as_posix(),
            queued.relative_to(workspace).as_posix(),
            destination.relative_to(workspace).as_posix(),
        ]
    if len(queued_matches) != 1 or implemented_matches:
        raise auto_progress.AutoProgressError(
            f"expected one queued improvement document for {improvement_id}",
            "improvement_document_ambiguous",
        )
    source = queued_matches[0]
    text = source.read_text(encoding="utf-8")
    if not re.search(r"(?m)^state:\s*queued\s*$", text):
        raise auto_progress.AutoProgressError(
            f"{source.relative_to(workspace)} is not queued",
            "improvement_state_invalid",
        )
    destination = source.with_name(f"{improvement_id}--implemented.md")
    updated = re.sub(
        r"(?m)^state:\s*queued\s*$", "state: implemented", text, count=1
    )
    source.write_text(updated, encoding="utf-8", newline="\n")
    source.rename(destination)
    return [
        source.relative_to(workspace).as_posix(),
        destination.relative_to(workspace).as_posix(),
    ]


def _status_transition_paths(state: dict[str, Any], workspace: Path) -> list[str]:
    if state["task_type"] != "implement-batch":
        return []
    paths: set[str] = set()
    for item in state.get("delivery_manifest", {}).get("improvements", []):
        improvement_id = item["id"]
        for root_key in ("ideas", "directed"):
            root = workspace / state["config"]["paths"][root_key]
            for name in (f"{improvement_id}.md", f"{improvement_id}--queued.md"):
                candidate = root / name
                if candidate.exists():
                    paths.add(candidate.relative_to(workspace).as_posix())
                    paths.add(
                        candidate.with_name(f"{improvement_id}--implemented.md")
                        .relative_to(workspace)
                        .as_posix()
                    )
    return sorted(paths)


def finish_run(
    project_id: str,
    run_id: str,
    manifest_path: Path,
    state_root: Path,
) -> dict[str, Any]:
    store = StateStore(state_root)
    try:
        state = store.load(project_id, run_id)
        if state.get("terminal"):
            return stage_result(
                "finish-run", "completed", "safe_retry", "Run is already terminal",
                facts={"project_id": project_id, "run_id": run_id}, checkpoint=state.get("current_stage")
            )
        if "baseline_validation" not in state.get("checkpoints", {}):
            raise auto_progress.AutoProgressError("Run has not passed baseline validation", "run_not_prepared")
        for kind, metadata in state["adapters"].items():
            current = ADAPTER_REGISTRY.get(metadata["id"])
            if current is None or current.interface_version != metadata["interface_version"] or current.state_schema_version != metadata["state_schema_version"]:
                return stage_result(
                    "finish-run", "recovery_required", "adapter_state_migration_required",
                    "Adapter state is not compatible with the current implementation", recovery="recover-run"
                )
        workspace = Path(state["workspace_path"])
        config = state["config"]
        additional_ignores = config["workspace"]["additional_ignore_patterns"]
        manifest_raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest_raw, dict):
            raise auto_progress.AutoProgressError("delivery manifest must be an object", "manifest_invalid")
        snapshot = _index_snapshot(workspace, state["base_revision"], additional_ignores)
        if not snapshot["entries"]:
            raise auto_progress.AutoProgressError("there are no changes to deliver", "empty_change")
        manifest = _validate_manifest(
            manifest_raw,
            state["task_type"],
            snapshot,
            config,
            allow_outside_changes=state["task_type"] == "implement-batch",
        )
        target_snapshot = manifest.pop("target_snapshot")
        outside_snapshot = manifest.pop("outside_snapshot")
        _validate_improvement_documents(
            workspace, manifest, state["task_type"], config
        )
        state["delivery_manifest"] = manifest
        state["bypass_changes"] = _capture_bypass_changes(
            state, workspace, outside_snapshot
        )
        state["validated_content_fingerprint"] = target_snapshot["fingerprint"]
        _checkpoint(
            state,
            "freeze_bypass_changes",
            {
                "paths": state["bypass_changes"]["paths"],
                "unstaged_paths": state["bypass_changes"]["unstaged_paths"],
                "content_fingerprint": state["bypass_changes"]["fingerprint"],
            },
        )
        store.save(state)

        final_validation = (
            {"passed": True, "skipped": True, "reason": "discovery_does_not_run_csharp_validation", "steps": []}
            if state["task_type"] == "discover-improvements"
            else _validation_steps(workspace, config, store, project_id, run_id, "final")
        )
        if not final_validation["passed"]:
            _checkpoint(state, "final_validation_failed", final_validation)
            store.save(state)
            return stage_result(
                "finish-run", "recovery_required", "final_validation_failed",
                "Configured final validation failed", facts={"validation": final_validation},
                checkpoint="baseline_validation", recovery="recover-run",
                diagnostic_ref=final_validation["steps"][-1].get("diagnostic_ref") if final_validation["steps"] else None,
            )
        target_paths = set(manifest["ownership"])
        validated = _verify_frozen_bypass(state, workspace, target_paths)
        if validated["fingerprint"] != state["validated_content_fingerprint"]:
            raise auto_progress.AutoProgressError("content changed during final validation", "content_changed")
        _checkpoint(state, "final_validation", {"validation": final_validation, "content_fingerprint": validated["fingerprint"]})
        store.save(state)

        unity = (
            {
                "reason_code": "discovery_does_not_run_unity",
                "verified": False,
                "content_fingerprint": validated["fingerprint"],
            }
            if state["task_type"] == "discover-improvements"
            else unity_mcp.run_verification(config["unity_mcp"], workspace, validated["fingerprint"])
        )
        after_unity = _verify_frozen_bypass(state, workspace, target_paths)
        if after_unity["fingerprint"] != validated["fingerprint"]:
            raise auto_progress.AutoProgressError("Unity validation changed tracked content", "content_changed")
        _checkpoint(state, "unity_validation", unity)
        store.save(state)

        import render_review

        facts = {
            "run_id": run_id,
            "task_type": state["task_type"],
            "base_branch": state["base_branch"],
            "base_revision": state["base_revision"],
            "changed_paths": validated["changed_paths"],
            "budget": {key: validated[key] for key in ("csharp_files", "all_files", "changed_lines")},
            "validation": {
                "baseline": state["checkpoints"]["baseline_validation"]["facts"],
                "final": final_validation,
            },
            "unity": unity,
            "content_fingerprint": validated["fingerprint"],
            "run_record_path": manifest["run_record_path"],
            "work_branch": state["branch"],
            "rollback": "Revert the item commit identified in the review after human assessment.",
        }
        run_record = render_review.render_document("run-record", manifest, facts)
        record_path = workspace / manifest["run_record_path"]
        record_path.parent.mkdir(parents=True, exist_ok=True)
        if record_path.exists():
            raise auto_progress.AutoProgressError("run record already exists", "run_record_exists")
        record_path.write_text(run_record["body"], encoding="utf-8", newline="\n")
        deterministic_paths = {manifest["run_record_path"]}
        allowed_target_paths = target_paths | deterministic_paths
        with_record = _verify_delivery_content(
            state, workspace, allowed_target_paths, deterministic_paths
        )
        _checkpoint(state, "render_run_record", {"path": manifest["run_record_path"], "content_hash": run_record["content_hash"]})
        state["delivery_facts"] = facts
        store.save(state)

        if not state["skip_github"]:
            _check_review_overlap(
                workspace,
                state["gh_program"],
                state["branch"],
                state["task_type"],
                sorted(allowed_target_paths),
            )

        revisions: dict[str, str] = {}
        status_paths: set[str] = set()
        for index, item in enumerate(manifest["improvements"]):
            if _identity(workspace) != state["git_identity"]:
                raise auto_progress.AutoProgressError(
                    "Git identity changed after preparation", "identity_mismatch"
                )
            item_status_paths = _transition_improvement_implemented(
                state, workspace, item
            )
            status_paths.update(item_status_paths)
            deterministic_paths.update(item_status_paths)
            allowed_target_paths.update(item_status_paths)
            _verify_delivery_content(
                state, workspace, allowed_target_paths, deterministic_paths
            )
            paths = list(item["expected_paths"]) + item_status_paths
            if index == 0:
                paths.append(manifest["run_record_path"])
            _git(workspace, "add", "-A", "--", *paths)
            message = f"AutoProgress: {item['id']} ({run_id})"
            committed = _git(workspace, "commit", "-m", message, check=False)
            if committed.returncode != 0:
                raise auto_progress.AutoProgressError("unable to create delivery commit", "record_change_failed")
            revisions[item["id"]] = _git(workspace, "rev-parse", "HEAD").stdout.strip()
            state["item_revisions"] = dict(revisions)
            _checkpoint(
                state,
                f"record_change_{item['id']}",
                {"revision": revisions[item["id"]], "improvement_id": item["id"]},
            )
            store.save(state)
        _verify_delivery_content(
            state, workspace, allowed_target_paths, deterministic_paths
        )
        revision = _git(workspace, "rev-parse", "HEAD").stdout.strip()
        state["change_handle"] = revision
        state["item_revisions"] = revisions
        _checkpoint(state, "record_change", {"revision": revision, "item_revisions": revisions})
        _append_material_events(state, store, revision, None)
        _checkpoint(state, "append_commit_ledger", {"recorded": True})
        facts["revision"] = revision
        facts["work_branch"] = state["branch"]
        facts["improvements"] = {
            item["id"]: {"result": "implemented", "revision": revisions[item["id"]]}
            for item in manifest["improvements"]
        }
        template = (
            "discover-improvements-review"
            if state["task_type"] == "discover-improvements"
            else "implement-batch-review"
        )
        review_doc = render_review.render_document(template, manifest, facts)
        state["review_document"] = review_doc
        store.save(state)

        remote = _git(workspace, "ls-remote", "--heads", "origin", f"refs/heads/{state['branch']}", check=False)
        remote_sha = remote.stdout.split()[0] if remote.stdout.strip() else None
        if remote_sha != revision:
            pushed = _git(workspace, "push", "origin", f"HEAD:refs/heads/{state['branch']}", check=False)
            if pushed.returncode != 0:
                remote = _git(workspace, "ls-remote", "--heads", "origin", f"refs/heads/{state['branch']}", check=False)
                remote_sha = remote.stdout.split()[0] if remote.stdout.strip() else None
                if remote_sha != revision:
                    store.save(state)
                    return stage_result(
                        "finish-run", "recovery_required", "publish_change_failed",
                        "Commit exists but branch publication failed", facts={"revision": revision},
                        checkpoint="record_change", recovery="recover-run"
                    )
        _checkpoint(state, "publish_change", {"revision": revision})
        store.save(state)

        if not state["skip_github"]:
            _check_review_overlap(
                workspace,
                state["gh_program"],
                state["branch"],
                state["task_type"],
                sorted(allowed_target_paths),
            )
        review = None if state["skip_github"] else _open_review(workspace, state["gh_program"], state["branch"])
        if review is None and state["skip_github"]:
            review = {"number": None, "url": None, "isDraft": True, "skipped": True}
        elif review is None:
            body_file = store.run_dir(project_id, run_id) / "review-body.md"
            body_file.write_text(review_doc["body"], encoding="utf-8", newline="\n")
            created = _run(
                [state["gh_program"], "pr", "create", "--draft", "--base", state["base_branch"], "--head", state["branch"], "--title", review_doc["title"], "--body-file", str(body_file)],
                cwd=workspace,
                check=False,
            )
            review = _open_review(workspace, state["gh_program"], state["branch"])
            if created.returncode != 0 and review is None:
                state["review_document"] = review_doc
                store.save(state)
                return stage_result(
                    "finish-run", "recovery_required", "create_review_failed",
                    "Branch is published but Draft review creation failed", facts={"revision": revision},
                    checkpoint="publish_change", recovery="recover-run"
                )
        if review and review.get("baseRefName") not in {None, state["base_branch"]}:
            raise auto_progress.AutoProgressError("review target branch is incorrect", "review_target_mismatch")
        state["review_handle"] = review
        state["review_document"] = review_doc
        _checkpoint(state, "create_review", {"number": review.get("number"), "url": review.get("url"), "content_hash": review_doc["content_hash"]})
        store.save(state)

        if unity.get("verified") and not state["skip_github"] and review.get("isDraft"):
            ready = _run(
                [state["gh_program"], "pr", "ready", str(review["number"])],
                cwd=workspace,
                check=False,
            )
            viewed = _run(
                [state["gh_program"], "pr", "view", str(review["number"]), "--json", "isDraft,url,number"],
                cwd=workspace,
                check=False,
            )
            if viewed.returncode == 0:
                try:
                    refreshed = json.loads(viewed.stdout)
                except json.JSONDecodeError:
                    refreshed = {}
                if isinstance(refreshed, dict):
                    review.update(refreshed)
            if ready.returncode != 0 and review.get("isDraft"):
                state["review_handle"] = review
                state["review_document"] = review_doc
                _checkpoint(
                    state,
                    "create_review",
                    {"number": review.get("number"), "url": review.get("url"), "content_hash": review_doc["content_hash"]},
                )
                store.save(state)
                return stage_result(
                    "finish-run",
                    "recovery_required",
                    "mark_review_ready_failed",
                    "Unity verification passed but the review could not be marked Ready",
                    facts={"revision": revision, "review": review},
                    checkpoint="create_review",
                    recovery="recover-run",
                )
        _checkpoint(state, "create_review", {"number": review.get("number"), "url": review.get("url"), "content_hash": review_doc["content_hash"]})
        store.save(state)

        _append_material_events(
            state, store, revision, review, include_commit_events=False
        )
        _checkpoint(state, "append_ledger", {"recorded": True})
        store.save(state)

        restored = restore_workspace(state, store)
        if not restored:
            store.save(state)
            return stage_result(
                "finish-run", "recovery_required", "workspace_restore_failed",
                "Delivery succeeded but workspace restoration failed",
                facts={"revision": revision, "review": review}, checkpoint="create_review", recovery="recover-run"
            )
        state["terminal"] = True
        state["terminal_result"] = "succeeded"
        state["recovery_obligations"] = []
        _checkpoint(state, "completed", {"revision": revision, "review": review})
        store.save(state)
        return stage_result(
            "finish-run", "completed", "succeeded", "Change was committed, published, and submitted for review",
            facts={"project_id": project_id, "run_id": run_id, "revision": revision, "review": review, "content_hash": review_doc["content_hash"], "unity": unity},
            checkpoint="completed"
        )
    except unity_mcp.UnityMcpError as exc:
        return stage_result(
            "finish-run", "recovery_required", exc.reason_code, exc.summary,
            facts={"project_id": project_id, "run_id": run_id}, checkpoint="final_validation", recovery="recover-run"
        )
    except (auto_progress.AutoProgressError, OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        reason = exc.reason_code if isinstance(exc, auto_progress.AutoProgressError) else "finish_run_failed"
        return stage_result(
            "finish-run", "recovery_required", reason, str(exc),
            facts={"project_id": project_id, "run_id": run_id}, recovery="recover-run"
        )


def restore_workspace(state: dict[str, Any], store: StateStore) -> bool:
    repo = Path(state["repo_path"])
    original = state["original"]
    if not _restore_original(repo, original.get("branch"), original["head"]):
        return False
    bypass = state.get("bypass_changes", {})
    if bypass.get("paths"):
        restored_snapshot = _index_snapshot(
            repo,
            original["head"],
            state["config"]["workspace"]["additional_ignore_patterns"],
        )
        if (
            restored_snapshot["changed_paths"] != bypass.get("paths")
            or restored_snapshot["fingerprint"] != bypass.get("fingerprint")
        ):
            return False
        if _git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0:
            return False
    return True


def recover_run(project_id: str, run_id: str, state_root: Path) -> dict[str, Any]:
    store = StateStore(state_root)
    try:
        state = store.load(project_id, run_id)
        if state.get("terminal"):
            return stage_result(
                "recover-run", "completed", "already_terminal", "Run is already terminal",
                facts={"project_id": project_id, "run_id": run_id}, checkpoint=state.get("current_stage")
            )
        for metadata in state.get("adapters", {}).values():
            adapter = ADAPTER_REGISTRY.get(metadata.get("id"))
            if adapter is None or adapter.interface_version != metadata.get("interface_version") or adapter.state_schema_version != metadata.get("state_schema_version"):
                return stage_result(
                    "recover-run", "recovery_required", "adapter_state_migration_required",
                    "No compatible deterministic adapter state migrator is available", recovery="manual"
                )
        workspace = Path(state["workspace_path"] or state["repo_path"])
        checkpoints = state.get("checkpoints", {})
        if "record_change" not in checkpoints:
            reconciled = (
                _resume_record_change(state, workspace, store) if workspace.exists() else None
            )
            if reconciled:
                revision = _git(workspace, "rev-parse", "HEAD").stdout.strip()
                state["item_revisions"] = reconciled
                state["change_handle"] = revision
                _checkpoint(
                    state,
                    "record_change",
                    {"revision": revision, "item_revisions": reconciled, "reconciled": True},
                )
                _append_material_events(state, store, revision, None)
                _checkpoint(state, "append_commit_ledger", {"recorded": True, "reconciled": True})
                store.save(state)
                checkpoints = state["checkpoints"]
            else:
                snapshot = (
                    _index_snapshot(
                        workspace,
                        state["base_revision"],
                        state["config"]["workspace"]["additional_ignore_patterns"],
                    )
                    if workspace.exists()
                    else {"entries": []}
                )
                if snapshot["entries"]:
                    return stage_result(
                        "recover-run", "recovery_required", "uncommitted_work_preserved",
                        "Uncommitted or partially committed work is preserved for continuation",
                        facts={"changed_paths": snapshot["changed_paths"]}, checkpoint=state.get("current_stage"), recovery="continue-run"
                    )
                if restore_workspace(state, store):
                    state["terminal"] = True
                    state["terminal_result"] = "failed"
                    state["recovery_obligations"] = []
                    _checkpoint(state, "recovered_without_delivery", {})
                    store.save(state)
                    return stage_result(
                        "recover-run", "completed", "workspace_restored", "Workspace was restored without delivery",
                        facts={"project_id": project_id, "run_id": run_id}, checkpoint="recovered_without_delivery"
                    )
                return stage_result(
                    "recover-run", "recovery_required", "workspace_restore_failed",
                    "Workspace could not be restored safely", recovery="manual"
                )

        revision = state["change_handle"]
        if "publish_change" not in checkpoints:
            remote = _git(workspace, "ls-remote", "--heads", "origin", f"refs/heads/{state['branch']}", check=False)
            remote_sha = remote.stdout.split()[0] if remote.stdout.strip() else None
            if remote_sha != revision:
                push = _git(workspace, "push", "origin", f"{revision}:refs/heads/{state['branch']}", check=False)
                if push.returncode != 0:
                    return stage_result(
                        "recover-run", "recovery_required", "publish_change_failed",
                        "Unable to reconcile or publish the recorded change", checkpoint="record_change", recovery="continue-recovery"
                    )
            _checkpoint(state, "publish_change", {"revision": revision})
            store.save(state)

        if "create_review" not in checkpoints and not state.get("skip_github"):
            review = _open_review(workspace, state["gh_program"], state["branch"])
            document = state.get("review_document")
            if review is None and isinstance(document, dict):
                body_file = store.run_dir(project_id, run_id) / "review-body.md"
                body_file.write_text(document["body"], encoding="utf-8", newline="\n")
                _run(
                    [state["gh_program"], "pr", "create", "--draft", "--base", state["base_branch"], "--head", state["branch"], "--title", document["title"], "--body-file", str(body_file)],
                    cwd=workspace,
                    check=False,
                )
                review = _open_review(workspace, state["gh_program"], state["branch"])
            if review is None:
                return stage_result(
                    "recover-run", "recovery_required", "create_review_failed",
                    "Published change has no recoverable Draft review", checkpoint="publish_change", recovery="continue-recovery"
                )
            state["review_handle"] = review
            _checkpoint(state, "create_review", {"number": review.get("number"), "url": review.get("url")})
            store.save(state)

        if "append_ledger" not in state.get("checkpoints", {}):
            review = state.get("review_handle")
            if not isinstance(review, dict):
                review = {"number": None, "url": None, "isDraft": True, "skipped": True}
            _append_material_events(
                state, store, revision, review, include_commit_events=False
            )
            _checkpoint(state, "append_ledger", {"recorded": True, "reconciled": True})
            store.save(state)

        if not restore_workspace(state, store):
            return stage_result(
                "recover-run", "recovery_required", "workspace_restore_failed",
                "External effects were reconciled but workspace restoration failed",
                checkpoint=state.get("current_stage"), recovery="manual"
            )
        state["terminal"] = True
        state["terminal_result"] = "succeeded"
        state["recovery_obligations"] = []
        _checkpoint(state, "recovered", {"revision": revision, "review": state.get("review_handle")})
        store.save(state)
        return stage_result(
            "recover-run", "completed", "recovered", "Run side effects were reconciled and workspace restored",
            facts={"project_id": project_id, "run_id": run_id, "revision": revision, "review": state.get("review_handle")},
            checkpoint="recovered"
        )
    except (auto_progress.AutoProgressError, OSError, subprocess.TimeoutExpired) as exc:
        reason = exc.reason_code if isinstance(exc, auto_progress.AutoProgressError) else "recover_run_failed"
        return stage_result("recover-run", "recovery_required", reason, str(exc), recovery="manual")
