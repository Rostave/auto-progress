"""Deterministically render trusted AutoProgress review documents.

The library API is deliberately free of repository, Git, review-host, network,
clock, and filesystem access.  The CLI only adds JSON input for human preview.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable


IMPLEMENT_BATCH_REVIEW = "implement-batch-review"
DISCOVER_IMPROVEMENTS_REVIEW = "discover-improvements-review"
RUN_RECORD = "run-record"

RUN_ID = re.compile(r"^RUN-\d{4}\.\d{2}\.\d{2}-[0-9a-f]{8}$")
IMPROVEMENT_ID = re.compile(r"^IMP-\d{4}\.\d{2}\.\d{2}-[0-9a-f]{8}$")
WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")


class RenderError(ValueError):
    """Raised when structured renderer input violates the template contract."""


def _validate_json(value: Any, name: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise RenderError(f"{name} must not contain NaN or infinity")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RenderError(f"{name} keys must be strings")
            _validate_json(item, f"{name}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json(item, f"{name}[{index}]")
        return
    raise RenderError(f"{name} must contain only JSON values")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RenderError(f"{name} must be a JSON object")
    return value


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise RenderError(f"{name} must be a string")
    result = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not result and not allow_empty:
        raise RenderError(f"{name} must not be empty")
    return result


def _optional_text(container: Mapping[str, Any], key: str, default: str = "not provided") -> str:
    value = container.get(key)
    return default if value is None else _text(value, key, allow_empty=True) or default


def _integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RenderError(f"{name} must be a non-negative integer")
    return value


def _sequence(value: Any, name: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise RenderError(f"{name} must be a JSON array")
    return list(value)


def _strings(value: Any, name: str) -> list[str]:
    return [_text(item, f"{name}[{index}]") for index, item in enumerate(_sequence(value, name))]


def _run_id(manifest: Mapping[str, Any], facts: Mapping[str, Any]) -> str:
    value = _text(manifest.get("run_id", facts.get("run_id")), "run_id")
    if not RUN_ID.fullmatch(value):
        raise RenderError("manifest.run_id must use RUN-YYYY.MM.DD-xxxxxxxx")
    return value


def _relative_path(value: Any, name: str) -> str:
    path = _text(value, name).replace("\\", "/")
    if path.startswith("/") or WINDOWS_ABSOLUTE.match(path):
        raise RenderError(f"{name} must be repository-relative")
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RenderError(f"{name} must be a normalized repository-relative path")
    return path


def _items(manifest: Mapping[str, Any], *, candidates: bool = False) -> list[Mapping[str, Any]]:
    raw = manifest.get("candidates", manifest.get("improvements")) if candidates else manifest.get("improvements")
    name = "manifest.candidates" if candidates else "manifest.improvements"
    result = [_mapping(item, f"{name}[{index}]") for index, item in enumerate(_sequence(raw, name))]
    for index, item in enumerate(result):
        identity = _text(item.get("id"), f"{name}[{index}].id")
        if not IMPROVEMENT_ID.fullmatch(identity):
            raise RenderError(f"{name}[{index}].id must use IMP-YYYY.MM.DD-xxxxxxxx")
    return result


def _task(manifest: Mapping[str, Any], facts: Mapping[str, Any], expected: str) -> None:
    task_type = manifest.get("task_type", facts.get("task_type", expected))
    if _text(task_type, "task_type") != expected:
        raise RenderError(f"task_type must be {expected!r}")


def _inline(value: Any, name: str) -> str:
    text = html.escape(_text(value, name, allow_empty=True), quote=False)
    return text.replace("|", "\\|").replace("\n", "<br>")


def _bullet_lines(value: Any, name: str, *, empty: str = "None.") -> list[str]:
    if value is None:
        return [f"- {empty}"]
    if isinstance(value, str):
        values = [_text(value, name)]
    else:
        values = _strings(value, name)
    return (
        [f"- {_inline(item, name)}" for item in values]
        if values
        else [f"- {empty}"]
    )


def _fact_lines(value: Any, name: str) -> list[str]:
    """Render a bounded JSON-shaped fact without depending on mapping insertion order."""
    if value is None:
        return ["- not provided"]
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key in sorted(value):
            if not isinstance(key, str):
                raise RenderError(f"{name} keys must be strings")
            item = value[key]
            if isinstance(item, (Mapping, list, tuple)):
                encoded = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            elif isinstance(item, (str, int, float, bool)) or item is None:
                encoded = str(item).lower() if isinstance(item, bool) else str(item)
            else:
                raise RenderError(f"{name}.{key} must be JSON-compatible")
            lines.append(f"- {key}: {_inline(encoded, f'{name}.{key}')}")
        return lines or ["- none"]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return _bullet_lines(value, name)
    if isinstance(value, (str, int, float, bool)):
        return [f"- {value}"]
    raise RenderError(f"{name} must be JSON-compatible")


def _validation_lines(facts: Mapping[str, Any]) -> list[str]:
    validation = _mapping(facts.get("validation", {}), "facts.validation")
    return _fact_lines(validation, "facts.validation")


def _unity_notice(facts: Mapping[str, Any]) -> str:
    validation = _mapping(facts.get("validation", {}), "facts.validation")
    unity_fact = facts.get("unity", validation.get("unity", validation.get("unity_status", "not_run")))
    if isinstance(unity_fact, Mapping):
        unity_fact = unity_fact.get("status", unity_fact.get("result", "not_run"))
    unity = str(unity_fact).lower()
    if unity in {"passed", "compiled", "success", "verified"}:
        return "> [!NOTE]\n> Unity Editor 脚本编译已通过；Play Mode、场景和资源行为仍未验证"
    return "> [!WARNING]\n> ⚠ 未经过 Unity Editor、Play Mode、场景或资源验证"


def _changed_path_lines(facts: Mapping[str, Any]) -> list[str]:
    values = _sequence(facts.get("changed_paths", []), "facts.changed_paths")
    result: list[str] = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            result.append(f"- `{_relative_path(value, f'facts.changed_paths[{index}]')}`")
            continue
        item = _mapping(value, f"facts.changed_paths[{index}]")
        path = _relative_path(item.get("path"), f"facts.changed_paths[{index}].path")
        status = _optional_text(item, "status", "changed")
        mode = item.get("mode")
        suffix = f"; mode {mode}" if mode is not None else ""
        result.append(f"- `{path}` ({status}{suffix})")
    return result or ["- None."]


def _improvement_fact(facts: Mapping[str, Any], identity: str) -> Mapping[str, Any]:
    raw = facts.get(
        "improvements",
        facts.get("improvement_results", facts.get("results", {})),
    )
    if isinstance(raw, Mapping):
        value = raw.get(identity, {})
        if isinstance(value, str):
            return {"result": value}
        return _mapping(value, f"facts.improvements.{identity}")
    if isinstance(raw, list):
        matches: list[Mapping[str, Any]] = []
        for index, value in enumerate(raw):
            item = _mapping(value, f"facts.improvements[{index}]")
            if item.get("id") == identity:
                matches.append(item)
        if len(matches) > 1:
            raise RenderError(f"facts.improvements contains duplicate {identity}")
        return matches[0] if matches else {}
    raise RenderError("facts.improvements must be an object or array")


def _render_implement(manifest: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[str, str]:
    _task(manifest, facts, "implement-batch")
    run_id = _run_id(manifest, facts)
    improvements = _items(manifest)
    if not improvements:
        raise RenderError("implement-batch review requires at least one improvement")
    title = f"[AutoProgress][{run_id}] Implement {len(improvements)} improvements"
    lines = [
        _unity_notice(facts),
        "",
        "## AutoProgress",
        "",
        f"- Run: `{run_id}`",
        "- Task type: `implement-batch`",
        f"- Base branch: `{_optional_text(facts, 'base_branch')}`",
        f"- Work branch: `{_optional_text(facts, 'work_branch')}`",
        f"- Base revision: `{_optional_text(facts, 'base_revision')}`",
        f"- Change revision: `{_optional_text(facts, 'revision')}`",
        f"- Verified content fingerprint: `{_optional_text(facts, 'content_fingerprint')}`",
    ]
    record = manifest.get("run_record_path", facts.get("run_record_path"))
    if record is not None:
        record_path = _relative_path(record, "manifest.run_record_path")
        lines.append(f"- Run record: [`{record_path}`]({record_path})")
    lines.extend([
        "",
        "## Improvements",
        "",
        "| Improvement | Result | Reason | Commit | Source | Summary |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for index, item in enumerate(improvements):
        item_id = _inline(item["id"], f"improvements[{index}].id")
        actual = _improvement_fact(facts, _text(item["id"], "improvement.id"))
        result = _inline(actual.get("result", "delivered"), "fact.result")
        reason = _inline(actual.get("reason", ""), "fact.reason")
        revision = _inline(actual.get("revision", "pending"), "fact.revision")
        source = _inline(item.get("source", "automatic"), f"improvements[{index}].source")
        summary = _inline(item.get("summary", ""), f"improvements[{index}].summary")
        lines.append(f"| `{item_id}` | {result} | {reason} | `{revision}` | {source} | {summary} |")

    lines.extend(["", "## Scope and acceptance criteria", ""])
    for index, item in enumerate(improvements):
        item_id = _text(item["id"], f"improvements[{index}].id")
        lines.append(f"### `{item_id}`")
        lines.append("")
        lines.append(_inline(item.get("summary", "not provided"), "summary"))
        lines.append("")
        lines.extend(_bullet_lines(item.get("acceptance"), f"improvements[{index}].acceptance", empty="No acceptance statement supplied."))
        lines.extend(["", "Design tradeoffs:"])
        lines.extend(_bullet_lines(item.get("design_tradeoffs"), f"improvements[{index}].design_tradeoffs"))

    lines.extend(["", "## Changed paths", ""])
    lines.extend(_changed_path_lines(facts))
    lines.extend(["", "## Commits", ""])
    lines.extend(_fact_lines(facts.get("commits"), "facts.commits"))
    lines.extend(["", "## Review delivery", ""])
    lines.extend(_fact_lines(facts.get("review"), "facts.review"))
    lines.extend(["", "## Validation", ""])
    lines.extend(_validation_lines(facts))
    lines.extend(["", "### Unity", ""])
    lines.extend(_fact_lines(facts.get("unity"), "facts.unity"))
    lines.extend(["", "## Budget", ""])
    lines.extend(_fact_lines(facts.get("budget"), "facts.budget"))
    lines.extend(["", "## Exemptions and rejection matches", ""])
    lines.extend(_fact_lines({
        "exemptions": manifest.get("exemptions", []),
        "rejection_hits": facts.get("rejection_hits", []),
    }, "disclosures"))
    lines.extend(["", "## Rollback", ""])
    lines.extend(_bullet_lines(facts.get("rollback"), "facts.rollback", empty="Revert the delivered improvement commits after human review."))
    lines.extend([
        "",
        "---",
        "",
        "若不想后续出现某项或实质相似方案，请按改进项 ID 将方案写入拒绝清单，并描述排除规则与适用范围。",
    ])
    return title, "\n".join(lines) + "\n"


def _render_discovery(manifest: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[str, str]:
    _task(manifest, facts, "discover-improvements")
    run_id = _run_id(manifest, facts)
    candidates = _items(manifest, candidates=True)
    title = f"[AutoProgress][Discovery][{run_id}] Add {len(candidates)} candidates"
    lines = [
        "## AutoProgress discovery",
        "",
        f"- Run: `{run_id}`",
        "- Task type: `discover-improvements`",
        f"- Base branch: `{_optional_text(facts, 'base_branch')}`",
        f"- Work branch: `{_optional_text(facts, 'work_branch')}`",
        f"- Base revision reviewed: `{_optional_text(facts, 'base_revision')}`",
        f"- Change revision: `{_optional_text(facts, 'revision')}`",
        f"- Review focus: {_inline(manifest.get('review_focus', 'not provided'), 'review_focus')}",
        f"- Files reviewed: {_integer(facts.get('reviewed_files', 0), 'facts.reviewed_files')}",
        f"- Source lines reviewed: {_integer(facts.get('reviewed_source_lines', 0), 'facts.reviewed_source_lines')}",
        f"- Candidates proposed: {len(candidates)}",
        "",
        "## Candidates",
        "",
        "| Candidate | Source | Summary | Evidence |",
        "| --- | --- | --- | --- |",
    ]
    for index, item in enumerate(candidates):
        identity = _inline(item["id"], f"candidates[{index}].id")
        source = _inline(item.get("source", "automatic-discovery"), f"candidates[{index}].source")
        summary = _inline(item.get("summary", ""), f"candidates[{index}].summary")
        evidence = item.get("evidence_paths", [])
        checked = [_relative_path(path, f"candidates[{index}].evidence_paths[{position}]") for position, path in enumerate(_sequence(evidence, f"candidates[{index}].evidence_paths"))]
        lines.append(f"| `{identity}` | {source} | {summary} | {_inline(', '.join(checked), 'evidence')} |")
    lines.extend([
        "",
        "## Boundaries",
        "",
        "- Documentation-only discovery; no candidate implementation is included.",
        "- C# validation was not run.",
        "- Unity MCP, Unity refresh, and Unity compilation were not run.",
        "- Candidates become authoritative `queued` improvements only after this review is merged into the configured base branch.",
        "",
        "---",
        "",
        "若不想后续出现某项或实质相似方案，请使用对应改进项 ID，由人工将方案及排除范围写入拒绝清单。",
    ])
    return title, "\n".join(lines) + "\n"


def _render_run_record(manifest: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[str, str]:
    task_type = _optional_text(facts, "task_type")
    if task_type not in {"implement-batch", "discover-improvements"}:
        raise RenderError("facts.task_type is not supported")
    run_id = _run_id(manifest, facts)
    improvements = _items(manifest)
    if not improvements:
        raise RenderError("run record requires at least one improvement")
    title = f"AutoProgress run {run_id}"
    lines = [
        f"# {title}",
        "",
        "## Run",
        "",
        f"- Run ID: `{run_id}`",
        f"- Task type: `{task_type}`",
        f"- Base branch: `{_optional_text(facts, 'base_branch')}`",
        f"- Work branch: `{_optional_text(facts, 'work_branch')}`",
        f"- Base revision: `{_optional_text(facts, 'base_revision')}`",
        f"- Verified content fingerprint: `{_optional_text(facts, 'content_fingerprint')}`",
        "",
        "## Improvement results",
        "",
        "| Improvement | Source | Result | Result reason | Selection reason | Summary |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(improvements):
        identity = _text(item["id"], f"improvements[{index}].id")
        actual = _improvement_fact(facts, identity)
        cells = [
            f"`{_inline(identity, f'improvements[{index}].id')}`",
            _inline(item.get("source", "automatic"), f"improvements[{index}].source"),
            _inline(actual.get("result", "delivered"), "fact.result"),
            _inline(actual.get("reason", ""), "fact.reason"),
            _inline(item.get("selection_reason", ""), f"improvements[{index}].selection_reason"),
            _inline(item.get("summary", ""), f"improvements[{index}].summary"),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.extend(["", "## Changed paths", ""])
    lines.extend(_changed_path_lines(facts))
    lines.extend(["", "## Budget", ""])
    lines.extend(_fact_lines(facts.get("budget"), "facts.budget"))
    lines.extend(["", "## Validation", "", "### Baseline and final validation", ""])
    lines.extend(_validation_lines(facts))
    lines.extend(["", "### Unity", ""])
    lines.extend(_fact_lines(facts.get("unity"), "facts.unity"))
    lines.extend(["", "## Rejection matches and exemptions", ""])
    lines.extend(_fact_lines({
        "exemptions": manifest.get("exemptions", []),
        "rejection_hits": facts.get("rejection_hits", []),
    }, "disclosures"))
    lines.extend(["", "## Rollback", ""])
    lines.extend(_bullet_lines(facts.get("rollback"), "facts.rollback"))
    lines.extend(["", "## Timeline", ""])
    lines.extend(_fact_lines(facts.get("timeline"), "facts.timeline"))
    return title, "\n".join(lines) + "\n"


Renderer = Callable[[Mapping[str, Any], Mapping[str, Any]], tuple[str, str]]
TEMPLATE_REGISTRY: Mapping[str, Renderer] = MappingProxyType({
    IMPLEMENT_BATCH_REVIEW: _render_implement,
    DISCOVER_IMPROVEMENTS_REVIEW: _render_discovery,
    RUN_RECORD: _render_run_record,
})


def calculate_content_hash(title: str, body: str) -> str:
    """Hash the complete review content with an unambiguous UTF-8 separator."""
    return hashlib.sha256(title.encode("utf-8") + b"\0" + body.encode("utf-8")).hexdigest()


def render_document(
    template_id: str,
    manifest: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> dict[str, str]:
    """Render a registered template from JSON-shaped semantic and fact inputs."""
    if not isinstance(template_id, str) or template_id not in TEMPLATE_REGISTRY:
        allowed = ", ".join(sorted(TEMPLATE_REGISTRY))
        raise RenderError(f"unknown template_id; allowed values: {allowed}")
    checked_manifest = _mapping(manifest, "manifest")
    checked_facts = _mapping(facts, "facts")
    _validate_json(checked_manifest, "manifest")
    _validate_json(checked_facts, "facts")
    title, body = TEMPLATE_REGISTRY[template_id](checked_manifest, checked_facts)
    return {
        "title": title,
        "body": body,
        "content_hash": calculate_content_hash(title, body),
    }


def render_review(
    template_id: str,
    manifest: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> dict[str, str]:
    """Compatibility name used by finish-run review creation."""
    return render_document(template_id, manifest, facts)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    preview = commands.add_parser("preview", help="render one JSON document to stdout")
    preview.add_argument(
        "--input",
        type=Path,
        required=True,
        help="JSON file, or - to read JSON from stdin",
    )
    return result


def _read_preview_input(path: Path) -> Mapping[str, Any]:
    if str(path) == "-":
        value = json.load(sys.stdin)
    else:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    return _mapping(value, "input")


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        payload = _read_preview_input(arguments.input)
        rendered = render_document(
            _text(payload.get("template_id"), "input.template_id"),
            _mapping(payload.get("manifest"), "input.manifest"),
            _mapping(payload.get("facts"), "input.facts"),
        )
        sys.stdout.write(json.dumps(rendered, ensure_ascii=False, indent=2) + "\n")
        return 0
    except (RenderError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
