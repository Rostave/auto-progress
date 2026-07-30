#!/usr/bin/env python3
"""Deterministic helpers for the AutoProgress Codex plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import tomllib
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ID_KINDS = {"improvement": "IMP", "run": "RUN", "event": "EVT"}
EVENT_COMPLETION = {"commit_created", "branch_pushed"}
MAX_EVENT_BYTES = 64 * 1024
MAX_EVENT_STRING = 4096
SENSITIVE_KEY = re.compile(r"(token|password|secret|credential|private[_-]?key)", re.I)
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
SAFE_BRANCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RUN_ID_PATTERN = re.compile(r"^RUN-\d{4}\.\d{2}\.\d{2}-[0-9a-f]{8}$")
TASK_TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


class AutoProgressError(RuntimeError):
    """Expected, user-actionable command failure."""


def load_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise AutoProgressError(f"configuration not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise AutoProgressError(f"invalid TOML: {exc}") from exc


def require_table(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    if not isinstance(value, dict):
        raise AutoProgressError(f"missing table [{name}]")
    return value


def require_positive(table: dict[str, Any], key: str, table_name: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise AutoProgressError(f"{table_name}.{key} must be a positive integer")
    return value


def validate_relative_path(value: Any, field: str, *, allow_dot: bool = False) -> None:
    if not isinstance(value, str) or not value:
        raise AutoProgressError(f"{field} must be a non-empty repository-relative path")
    if allow_dot and value == ".":
        return
    if Path(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise AutoProgressError(f"{field} must not be absolute")
    if ".." in Path(value).parts or ".." in PureWindowsPath(value).parts:
        raise AutoProgressError(f"{field} must not escape the repository")


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    schema_version = config.get("schema_version")
    if schema_version == 1:
        raise AutoProgressError(
            "schema_version 1 requires a human-invoked "
            "$configure-auto-progress migrate before automatic tasks may run"
        )
    if schema_version != 2:
        raise AutoProgressError("schema_version must be 2")

    project = require_table(config, "project")
    base = project.get("base_branch")
    if not isinstance(base, str) or not SAFE_BRANCH.fullmatch(base) or ".." in base:
        raise AutoProgressError("project.base_branch is invalid")
    timezone = project.get("timezone")
    if not isinstance(timezone, str):
        raise AutoProgressError("project.timezone must be an IANA timezone")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise AutoProgressError(f"unknown timezone: {timezone}") from exc
    if not isinstance(project.get("paused"), bool):
        raise AutoProgressError("project.paused must be true or false")
    require_positive(project, "max_run_minutes", "project")

    schedule = require_table(config, "schedule")
    for key in ("window_start", "window_end"):
        if not isinstance(schedule.get(key), str) or not TIME_PATTERN.fullmatch(schedule[key]):
            raise AutoProgressError(f"schedule.{key} must use HH:MM")
    require_positive(schedule, "defer_minutes", "schedule")

    batch = require_table(config, "batch")
    require_positive(batch, "max_improvements", "batch")

    discovery = require_table(config, "discovery")
    for key in (
        "initial_files",
        "expansion_files",
        "max_files",
        "max_source_lines",
        "target_queued_automatic_improvements",
        "max_new_improvements",
        "revisit_after_maintenance_days",
        "closed_pr_cooldown_maintenance_days",
    ):
        require_positive(discovery, key, "discovery")
    if discovery["initial_files"] > discovery["max_files"]:
        raise AutoProgressError(
            "discovery.initial_files must be <= discovery.max_files"
        )
    if discovery["expansion_files"] > discovery["max_files"]:
        raise AutoProgressError(
            "discovery.expansion_files must be <= discovery.max_files"
        )

    change = require_table(config, "change_budget")
    batch_budget = require_table(config, "batch_budget")
    directed = require_table(config, "directed_budget")
    csharp_suggested = require_positive(change, "csharp_files_suggested", "change_budget")
    csharp_hard = require_positive(change, "csharp_files_hard", "change_budget")
    lines_suggested = require_positive(change, "changed_lines_suggested", "change_budget")
    lines_hard = require_positive(change, "changed_lines_hard", "change_budget")
    all_hard = require_positive(change, "all_files_hard", "change_budget")
    batch_csharp_suggested = require_positive(
        batch_budget, "csharp_files_suggested", "batch_budget"
    )
    batch_csharp_hard = require_positive(
        batch_budget, "csharp_files_hard", "batch_budget"
    )
    batch_lines_suggested = require_positive(
        batch_budget, "changed_lines_suggested", "batch_budget"
    )
    batch_lines_hard = require_positive(
        batch_budget, "changed_lines_hard", "batch_budget"
    )
    batch_all_hard = require_positive(
        batch_budget, "all_files_hard", "batch_budget"
    )
    csharp_absolute = require_positive(directed, "csharp_files_absolute", "directed_budget")
    lines_absolute = require_positive(directed, "changed_lines_absolute", "directed_budget")
    all_absolute = require_positive(directed, "all_files_absolute", "directed_budget")
    if not csharp_suggested <= csharp_hard <= csharp_absolute:
        raise AutoProgressError(
            "C# file budgets must satisfy suggested <= hard <= directed absolute"
        )
    if not lines_suggested <= lines_hard <= lines_absolute:
        raise AutoProgressError(
            "line budgets must satisfy suggested <= hard <= directed absolute"
        )
    if not all_hard <= all_absolute:
        raise AutoProgressError(
            "all-file budgets must satisfy hard <= directed absolute"
        )
    if not csharp_hard <= batch_csharp_suggested <= batch_csharp_hard:
        raise AutoProgressError(
            "C# batch budgets must satisfy item hard <= batch suggested <= batch hard"
        )
    if not lines_hard <= batch_lines_suggested <= batch_lines_hard:
        raise AutoProgressError(
            "line batch budgets must satisfy item hard <= batch suggested <= batch hard"
        )
    if not all_hard <= batch_all_hard:
        raise AutoProgressError(
            "all-file batch hard must be >= item all-file hard"
        )

    retry = require_table(config, "retry")
    require_positive(retry, "cooldown_maintenance_days", "retry")

    paths = require_table(config, "paths")
    for key in ("allowed", "excluded"):
        values = paths.get(key)
        if not isinstance(values, list) or not values:
            raise AutoProgressError(f"paths.{key} must be a non-empty array")
        for index, value in enumerate(values):
            validate_relative_path(value, f"paths.{key}[{index}]")
    for key in ("ideas", "directed", "rejections", "status"):
        validate_relative_path(paths.get(key), f"paths.{key}")

    validation = require_table(config, "validation")
    steps = validation.get("steps")
    if not isinstance(steps, list) or not steps:
        raise AutoProgressError("at least one [[validation.steps]] entry is required")
    for index, step in enumerate(steps):
        field = f"validation.steps[{index}]"
        if not isinstance(step, dict):
            raise AutoProgressError(f"{field} must be a table")
        for key in ("name", "program"):
            if not isinstance(step.get(key), str) or not step[key].strip():
                raise AutoProgressError(f"{field}.{key} must be a non-empty string")
        program = step["program"]
        if any(char in program for char in "|&;<>()`$"):
            raise AutoProgressError(f"{field}.program must not be a shell expression")
        args = step.get("args")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise AutoProgressError(f"{field}.args must be an array of strings")
        validate_relative_path(
            step.get("working_directory"), f"{field}.working_directory", allow_dot=True
        )
        require_positive(step, "timeout_minutes", field)
        codes = step.get("success_exit_codes")
        if not isinstance(codes, list) or not codes or not all(
            isinstance(code, int) and not isinstance(code, bool) for code in codes
        ):
            raise AutoProgressError(
                f"{field}.success_exit_codes must be a non-empty integer array"
            )

    unity = require_table(config, "unity_mcp")
    if not isinstance(unity.get("enabled"), bool):
        raise AutoProgressError("unity_mcp.enabled must be true or false")
    if unity["enabled"]:
        if not isinstance(unity.get("provider"), str) or not unity["provider"].strip():
            raise AutoProgressError("unity_mcp.provider is required when enabled")
        validate_relative_path(
            unity.get("expected_project_root"),
            "unity_mcp.expected_project_root",
            allow_dot=True,
        )
        if not isinstance(unity.get("refresh_after_checkout"), bool):
            raise AutoProgressError(
                "unity_mcp.refresh_after_checkout must be true or false"
            )

    return config


def normalize_remote(remote: str) -> str:
    value = remote.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    match = re.fullmatch(r"git@([^:]+):(.+)", value)
    if match:
        return f"{match.group(1).lower()}/{match.group(2).strip('/')}".lower()
    parsed = urlparse(value)
    if parsed.scheme and parsed.hostname:
        path = parsed.path.strip("/")
        return f"{parsed.hostname.lower()}/{path}".lower()
    return value.replace("\\", "/").lower()


def make_project_id(remote: str, base_branch: str) -> str:
    normalized = normalize_remote(remote)
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.split("/")[-1]).strip("-")
    slug = slug[:32] or "repository"
    digest = hashlib.sha256(f"{normalized}\n{base_branch}".encode()).hexdigest()[:16]
    return f"{slug}-{digest}"


def make_id(kind: str, timezone: str, now: datetime | None = None) -> str:
    prefix = ID_KINDS[kind]
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone) if now else datetime.now(zone)
    return f"{prefix}-{local:%Y.%m.%d}-{secrets.token_hex(4)}"


def run(
    args: list[str], *, cwd: Path, timeout: int = 30, check: bool = True
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
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise AutoProgressError(f"{' '.join(args[:3])} failed: {detail[:1000]}")
    return result


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(
        ["git", "-c", f"safe.directory={repo.resolve()}", *args],
        cwd=repo,
        check=check,
    )


def git_path(repo: Path, name: str) -> Path:
    value = git(repo, "rev-parse", "--git-path", name).stdout.strip()
    path = Path(value)
    return path if path.is_absolute() else repo / path


def repository_remote(repo: Path) -> str:
    return git(repo, "remote", "get-url", "origin").stdout.strip()


def check_preflight(
    repo: Path,
    config_path: Path,
    gh_program: str | None,
    skip_github: bool,
    mode: str = "maintenance",
) -> dict[str, Any]:
    repo = repo.resolve()
    config = validate_config(load_config(config_path.resolve()))
    base = config["project"]["base_branch"]
    checks: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    inside = git(repo, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    record("git_repository", inside, str(repo))

    if mode == "maintenance":
        status = git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout
        record("clean_worktree", not status, "clean" if not status else status[:1000])

        operations = []
        for name in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "BISECT_LOG",
        ):
            if git_path(repo, name).exists():
                operations.append(name)
        record(
            "no_active_git_operation",
            not operations,
            "none" if not operations else ", ".join(operations),
        )

    identity = {
        key: git(repo, "config", "--get", key, check=False).stdout.strip()
        for key in ("user.name", "user.email")
    }
    record(
        "git_identity",
        bool(identity["user.name"] and identity["user.email"]),
        f"{identity['user.name']} <{identity['user.email']}>",
    )

    remote = repository_remote(repo)
    record("origin_remote", bool(remote), normalize_remote(remote))
    remote_base = git(
        repo,
        "ls-remote",
        "--exit-code",
        "--heads",
        "origin",
        f"refs/heads/{base}",
        check=False,
    )
    record(
        "remote_base",
        remote_base.returncode == 0 and bool(remote_base.stdout.strip()),
        base,
    )

    if not skip_github:
        gh = gh_program or "gh"
        auth = run([gh, "auth", "status"], cwd=repo, check=False)
        record("github_auth", auth.returncode == 0, "authenticated" if auth.returncode == 0 else (auth.stderr or auth.stdout)[:1000])
        view = run(
            [gh, "repo", "view", "--json", "nameWithOwner,viewerPermission"],
            cwd=repo,
            check=False,
        )
        permission = ""
        if view.returncode == 0:
            try:
                permission = json.loads(view.stdout).get("viewerPermission", "")
            except json.JSONDecodeError:
                permission = ""
        record(
            "github_push_permission",
            view.returncode == 0 and permission in {"ADMIN", "MAINTAIN", "WRITE"},
            permission or (view.stderr or view.stdout)[:1000],
        )

    passed = all(check["passed"] for check in checks)
    return {
        "ok": passed,
        "project_id": make_project_id(remote, base),
        "base_branch": base,
        "mode": mode,
        "checks": checks,
    }


def default_state_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    root = Path(codex_home) if codex_home else Path.home() / ".codex"
    return root / "auto-progress" / "state"


def contains_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or PureWindowsPath(value).is_absolute()


def validate_event_value(value: Any, trail: tuple[str, ...] = ()) -> None:
    if trail and SENSITIVE_KEY.search(trail[-1]):
        raise AutoProgressError(f"sensitive ledger key is forbidden: {'.'.join(trail)}")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise AutoProgressError("ledger object keys must be strings")
            validate_event_value(child, (*trail, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            validate_event_value(child, (*trail, str(index)))
    elif isinstance(value, str):
        if len(value) > MAX_EVENT_STRING:
            raise AutoProgressError(f"ledger string is too long: {'.'.join(trail)}")
        if contains_absolute_path(value):
            raise AutoProgressError(
                f"machine-specific absolute path is forbidden: {'.'.join(trail)}"
            )
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise AutoProgressError(f"unsupported ledger value at {'.'.join(trail)}")


def ledger_files(state_root: Path, project_id: str) -> list[Path]:
    return sorted(state_root.glob(f"{project_id}-????-??.jsonl"))


def append_ledger(
    event: dict[str, Any], project_id: str, state_root: Path, timezone: str
) -> Path:
    required = {"event_id", "maintenance_day", "event_type", "timestamp"}
    missing = sorted(required - event.keys())
    if missing:
        raise AutoProgressError(f"ledger event missing: {', '.join(missing)}")
    validate_event_value(event)
    encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_EVENT_BYTES:
        raise AutoProgressError("ledger event exceeds 64 KiB")
    try:
        timestamp = datetime.fromisoformat(str(event["timestamp"]))
    except ValueError as exc:
        raise AutoProgressError("event timestamp must be ISO 8601") from exc
    local = timestamp.astimezone(ZoneInfo(timezone))
    expected_day = local.strftime("%Y-%m-%d")
    if event["maintenance_day"] != expected_day:
        raise AutoProgressError(
            f"maintenance_day must be {expected_day} in {timezone}"
        )

    state_root.mkdir(parents=True, exist_ok=True)
    for path in ledger_files(state_root, project_id):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    prior = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if prior.get("event_id") == event["event_id"]:
                    raise AutoProgressError(f"duplicate event_id: {event['event_id']}")

    destination = state_root / f"{project_id}-{local:%Y-%m}.jsonl"
    lock = state_root / f"{project_id}.lock"
    deadline = time.monotonic() + 5
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AutoProgressError(f"ledger lock is busy: {lock}")
            time.sleep(0.05)
    try:
        with destination.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)
    return destination


def claim_daily_allowance(
    project_id: str,
    state_root: Path,
    timezone: str,
    run_id: str,
    task_type: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically claim the single daily activity allowance for one task instance."""
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise AutoProgressError("run_id must use RUN-YYYY.MM.DD-xxxxxxxx")
    if not TASK_TYPE_PATTERN.fullmatch(task_type):
        raise AutoProgressError("task_type must be lowercase kebab-case")

    zone = ZoneInfo(timezone)
    local = now.astimezone(zone) if now else datetime.now(zone)
    maintenance_day = local.strftime("%Y-%m-%d")
    event = {
        "event_id": make_id("event", timezone, local),
        "maintenance_day": maintenance_day,
        "event_type": "daily_allowance_claimed",
        "timestamp": local.isoformat(),
        "run_id": run_id,
        "task_type": task_type,
    }
    validate_event_value(event)

    state_root.mkdir(parents=True, exist_ok=True)
    lock = state_root / f"{project_id}.lock"
    deadline = time.monotonic() + 5
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise AutoProgressError(f"ledger lock is busy: {lock}")
            time.sleep(0.05)

    try:
        for prior in read_events(ledger_files(state_root, project_id)):
            if (
                prior.get("event_type") == "daily_allowance_claimed"
                and prior.get("maintenance_day") == maintenance_day
            ):
                if (
                    prior.get("run_id") == run_id
                    and prior.get("task_type") == task_type
                ):
                    return {
                        "claimed": False,
                        "safe_retry": True,
                        "maintenance_day": maintenance_day,
                        "run_id": run_id,
                        "task_type": task_type,
                        "event_id": prior.get("event_id"),
                    }
                raise AutoProgressError(
                    "daily activity allowance already claimed by "
                    f"{prior.get('task_type')} ({prior.get('run_id')})"
                )

        destination = state_root / f"{project_id}-{local:%Y-%m}.jsonl"
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with destination.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)

    return {
        "claimed": True,
        "safe_retry": False,
        "maintenance_day": maintenance_day,
        "run_id": run_id,
        "task_type": task_type,
        "event_id": event["event_id"],
        "ledger": str(destination),
    }


def read_events(paths: Iterable[Path]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AutoProgressError(f"invalid JSONL at {path}:{number}") from exc
                if isinstance(event, dict):
                    events.append(event)
    return events


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    def is_implementation_completion(event: dict[str, Any]) -> bool:
        return (
            event.get("event_type") in EVENT_COMPLETION
            and event.get("task_type") != "discover-improvements"
        )

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        day = event.get("maintenance_day")
        if isinstance(day, str):
            by_day[day].append(event)

    completed_days: set[str] = set()
    pushed_days: set[str] = set()
    skipped_days: set[str] = set()
    failed_days: set[str] = set()
    prs: set[str] = set()
    committed_items: set[str] = set()
    pr_items: set[str] = set()
    directed_queued: set[str] = set()
    directed_terminal: set[str] = set()
    allowance_days_by_task: dict[str, set[str]] = defaultdict(set)
    task_runs: dict[str, set[str]] = defaultdict(set)
    task_results: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    task_durations: dict[str, list[float]] = defaultdict(list)
    implementation_counts: dict[str, int] = defaultdict(int)
    discovery_prs: set[str] = set()
    discovery_counts: dict[str, int] = defaultdict(int)

    for day, day_events in by_day.items():
        types = {str(event.get("event_type")) for event in day_events}
        if any(is_implementation_completion(event) for event in day_events):
            completed_days.add(day)
        elif "run_failed" in types:
            failed_days.add(day)
        elif "run_skipped" in types:
            skipped_days.add(day)
        if any(
            event.get("event_type") == "branch_pushed"
            and event.get("task_type") != "discover-improvements"
            for event in day_events
        ):
            pushed_days.add(day)

    for event in events:
        event_type = str(event.get("event_type"))
        task_type = event.get("task_type")
        run_id = event.get("run_id")
        pr = event.get("pull_request")
        item = event.get("improvement_id")
        if event_type == "daily_allowance_claimed" and isinstance(task_type, str):
            allowance_days_by_task[task_type].add(str(event.get("maintenance_day")))
        if isinstance(task_type, str) and isinstance(run_id, str):
            task_runs[task_type].add(run_id)
        result_name = {
            "run_succeeded": "success",
            "run_skipped": "skipped",
            "run_failed": "failed",
            "run_timed_out": "timed_out",
        }.get(event_type)
        if result_name and isinstance(task_type, str):
            task_results[task_type][result_name] += 1
        duration = event.get("duration_seconds")
        if (
            isinstance(task_type, str)
            and isinstance(duration, (int, float))
            and not isinstance(duration, bool)
            and duration >= 0
        ):
            task_durations[task_type].append(float(duration))
        if event_type == "pr_opened" and isinstance(pr, (str, int)):
            prs.add(str(pr))
        if is_implementation_completion(event) and isinstance(item, str):
            committed_items.add(item)
        if event_type == "pr_opened" and isinstance(item, str):
            pr_items.add(item)
        if event_type == "directed_queued" and isinstance(item, str):
            directed_queued.add(item)
        if event_type in {"directed_completed", "directed_cancelled", "directed_rejected"} and isinstance(item, str):
            directed_terminal.add(item)
        implementation_key = {
            "maintenance_batch_started": "batches",
            "improvement_delivered": "delivered",
            "improvement_deferred": "deferred",
            "improvement_reverted": "reverted",
            "candidate_stale": "candidate_stale",
        }.get(event_type)
        if implementation_key:
            implementation_counts[implementation_key] += 1
        if event_type == "discovery_completed":
            reviewed_files = event.get("reviewed_files", 0)
            reviewed_lines = event.get("reviewed_source_lines", 0)
            candidate_count = event.get("candidate_count")
            if not isinstance(candidate_count, int):
                ids = event.get("improvement_ids")
                candidate_count = len(ids) if isinstance(ids, list) else 0
            if isinstance(reviewed_files, int) and reviewed_files >= 0:
                discovery_counts["reviewed_files"] += reviewed_files
            if isinstance(reviewed_lines, int) and reviewed_lines >= 0:
                discovery_counts["reviewed_source_lines"] += reviewed_lines
            discovery_counts["candidates_proposed"] += max(candidate_count, 0)
            if candidate_count == 0:
                discovery_counts["zero_candidate_sessions"] += 1
        if (
            event_type in {"discovery_pr_opened", "pr_opened"}
            and task_type == "discover-improvements"
            and isinstance(pr, (str, int))
        ):
            discovery_prs.add(str(pr))

    all_allowance_days = set().union(*allowance_days_by_task.values()) if allowance_days_by_task else set()
    task_type_stats: dict[str, Any] = {}
    for task_type in sorted(set(task_runs) | set(task_results) | set(task_durations)):
        durations = task_durations.get(task_type, [])
        task_type_stats[task_type] = {
            "runs": len(task_runs.get(task_type, set())),
            "results": dict(sorted(task_results.get(task_type, {}).items())),
            "average_duration_seconds": (
                round(sum(durations) / len(durations), 2) if durations else None
            ),
        }

    return {
        "completed_days": len(completed_days),
        "pushed_days": len(pushed_days),
        "pr_opened": len(prs),
        "skipped_days": len(skipped_days),
        "failed_days": len(failed_days),
        "pending_recovery": len(committed_items - pr_items),
        "directed_pending": len(directed_queued - directed_terminal),
        "allowance_days": len(all_allowance_days),
        "allowance_days_by_task_type": {
            key: len(value) for key, value in sorted(allowance_days_by_task.items())
        },
        "implementation": {
            "runs": len(task_runs.get("implement-batch", set())),
            "batches": implementation_counts["batches"],
            "delivered": implementation_counts["delivered"],
            "deferred": implementation_counts["deferred"],
            "reverted": implementation_counts["reverted"],
            "candidate_stale": implementation_counts["candidate_stale"],
        },
        "discovery": {
            "sessions": len(task_runs.get("discover-improvements", set())),
            "reviewed_files": discovery_counts["reviewed_files"],
            "reviewed_source_lines": discovery_counts["reviewed_source_lines"],
            "candidates_proposed": discovery_counts["candidates_proposed"],
            "zero_candidate_sessions": discovery_counts["zero_candidate_sessions"],
            "pull_requests": len(discovery_prs),
        },
        "task_types": task_type_stats,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subcommands = result.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser("validate-config")
    validate.add_argument("--config", type=Path, required=True)

    new_id = subcommands.add_parser("new-id")
    new_id.add_argument(
        "--kind", choices=sorted(ID_KINDS), default="improvement"
    )
    new_id.add_argument("--timezone", default="Asia/Shanghai")

    project = subcommands.add_parser("project-id")
    project.add_argument("--remote", required=True)
    project.add_argument("--base-branch", required=True)

    preflight = subcommands.add_parser("preflight")
    preflight.add_argument("--repo", type=Path, required=True)
    preflight.add_argument("--config", type=Path, required=True)
    preflight.add_argument("--gh")
    preflight.add_argument("--skip-github", action="store_true")
    preflight.add_argument(
        "--mode", choices=("maintenance", "discovery"), default="maintenance"
    )

    append = subcommands.add_parser("append-ledger")
    append.add_argument("--event-file", type=Path, required=True)
    append.add_argument("--project-id", required=True)
    append.add_argument("--timezone", default="Asia/Shanghai")
    append.add_argument("--state-root", type=Path, default=default_state_root())

    claim = subcommands.add_parser("claim-allowance")
    claim.add_argument("--project-id", required=True)
    claim.add_argument("--timezone", default="Asia/Shanghai")
    claim.add_argument("--run-id", required=True)
    claim.add_argument("--task-type", required=True)
    claim.add_argument("--state-root", type=Path, default=default_state_root())

    summary = subcommands.add_parser("status")
    summary.add_argument("--project-id", required=True)
    summary.add_argument("--state-root", type=Path, default=default_state_root())
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "validate-config":
            validate_config(load_config(arguments.config.resolve()))
            output: Any = {"ok": True, "config": str(arguments.config)}
        elif arguments.command == "new-id":
            output = {"id": make_id(arguments.kind, arguments.timezone)}
        elif arguments.command == "project-id":
            output = {
                "project_id": make_project_id(
                    arguments.remote, arguments.base_branch
                )
            }
        elif arguments.command == "preflight":
            output = check_preflight(
                arguments.repo,
                arguments.config,
                arguments.gh,
                arguments.skip_github,
                arguments.mode,
            )
        elif arguments.command == "append-ledger":
            with arguments.event_file.open("r", encoding="utf-8") as handle:
                event = json.load(handle)
            if not isinstance(event, dict):
                raise AutoProgressError("event file must contain a JSON object")
            path = append_ledger(
                event,
                arguments.project_id,
                arguments.state_root.resolve(),
                arguments.timezone,
            )
            output = {"ok": True, "ledger": str(path)}
        elif arguments.command == "claim-allowance":
            output = {
                "ok": True,
                **claim_daily_allowance(
                    arguments.project_id,
                    arguments.state_root.resolve(),
                    arguments.timezone,
                    arguments.run_id,
                    arguments.task_type,
                ),
            }
        elif arguments.command == "status":
            paths = ledger_files(arguments.state_root.resolve(), arguments.project_id)
            output = {
                "ok": True,
                "project_id": arguments.project_id,
                "files": len(paths),
                **summarize_events(read_events(paths)),
            }
        else:
            raise AssertionError(arguments.command)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if not isinstance(output, dict) or output.get("ok", True) else 2
    except (AutoProgressError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
