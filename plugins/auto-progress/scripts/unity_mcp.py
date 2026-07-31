#!/usr/bin/env python3
"""Deterministic Streamable HTTP client for trusted Unity MCP adapters."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class UnityMcpError(RuntimeError):
    """A stable, user-actionable Unity MCP failure."""

    def __init__(self, reason_code: str, summary: str) -> None:
        super().__init__(summary)
        self.reason_code = reason_code
        self.summary = summary


@dataclass(frozen=True)
class UnityAdapter:
    adapter_id: str
    version: str
    required_tools: tuple[str, ...]
    project_resource: str
    editor_state_resource: str


ADAPTERS = {
    "coplaydev-unity-mcp": UnityAdapter(
        adapter_id="coplaydev-unity-mcp",
        version="1",
        required_tools=("refresh_unity", "read_console"),
        project_resource="mcpforunity://project/info",
        editor_state_resource="mcpforunity://editor/state",
    )
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


NO_REDIRECT_OPENER = build_opener(_NoRedirect)


def validate_endpoint(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise UnityMcpError("invalid_unity_mcp_url", "Unity MCP URL must be complete")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise UnityMcpError(
            "invalid_unity_mcp_url",
            "Unity MCP URL cannot contain credentials, query, or fragment",
        )
    host = (parsed.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise UnityMcpError(
            "unity_mcp_non_loopback", "Unity MCP endpoint must use a loopback host"
        )
    if host == "0.0.0.0":
        raise UnityMcpError(
            "unity_mcp_non_loopback", "Unity MCP endpoint cannot use 0.0.0.0"
        )
    return url


def _decode_response(data: bytes, content_type: str) -> dict[str, Any]:
    if len(data) > MAX_RESPONSE_BYTES:
        raise UnityMcpError("unity_mcp_response_too_large", "Unity MCP response is too large")
    try:
        text = data.decode("utf-8", errors="strict")
        if "text/event-stream" in content_type:
            payloads = []
            for block in text.replace("\r\n", "\n").split("\n\n"):
                lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
                if lines:
                    payloads.append("\n".join(lines))
            if not payloads:
                raise UnityMcpError("unity_mcp_invalid_response", "Unity MCP SSE response has no data")
            text = payloads[-1]
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UnityMcpError("unity_mcp_invalid_response", "Unity MCP returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise UnityMcpError("unity_mcp_invalid_response", "Unity MCP response must be an object")
    return value


class StreamableHttpClient:
    def __init__(self, url: str, connect_timeout: int, operation_timeout: int) -> None:
        self.url = validate_endpoint(url)
        self.connect_timeout = connect_timeout
        self.operation_timeout = operation_timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self._next_id = 1

    def _post(self, payload: dict[str, Any], *, notification: bool = False) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        request = Request(
            self.url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            timeout = self.connect_timeout if self.protocol_version is None else self.operation_timeout
            with NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
                session_id = response.headers.get("Mcp-Session-Id")
                if session_id:
                    self.session_id = session_id
                data = response.read(MAX_RESPONSE_BYTES + 1)
                if notification and not data:
                    return {}
                return _decode_response(data, response.headers.get("Content-Type", ""))
        except HTTPError as exc:
            raise UnityMcpError(
                "unity_mcp_http_error", f"Unity MCP returned HTTP {exc.code}"
            ) from exc
        except (URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise UnityMcpError("unity_mcp_unavailable", f"Unity MCP unavailable: {exc}") from exc

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        response = self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        if response.get("id") != request_id:
            raise UnityMcpError("unity_mcp_invalid_response", "Unity MCP response ID mismatch")
        error = response.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "unknown MCP error"))[:500]
            raise UnityMcpError("unity_mcp_request_failed", message)
        if "result" not in response:
            raise UnityMcpError("unity_mcp_invalid_response", "Unity MCP response has no result")
        return response["result"]

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._post(
            {"jsonrpc": "2.0", "method": method, "params": params or {}},
            notification=True,
        )

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": SUPPORTED_PROTOCOL_VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": "auto-progress", "version": "0.2.0"},
            },
        )
        if not isinstance(result, dict):
            raise UnityMcpError("unity_mcp_invalid_response", "Invalid initialize result")
        version = result.get("protocolVersion")
        if version not in SUPPORTED_PROTOCOL_VERSIONS:
            raise UnityMcpError(
                "unity_mcp_protocol_unsupported", f"Unsupported MCP protocol version: {version}"
            )
        self.protocol_version = str(version)
        self.notify("notifications/initialized")
        return result


def _content_json(result: Any) -> Any:
    if isinstance(result, dict) and "contents" in result:
        contents = result.get("contents")
        if isinstance(contents, list) and contents:
            first = contents[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return text
    if isinstance(result, dict) and "content" in result:
        content = result.get("content")
        if isinstance(content, list):
            texts = [item.get("text") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)]
            for text in texts:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
            return texts
    return result


def _normalize_path(value: str) -> str:
    return str(Path(value).resolve()).replace("\\", "/").rstrip("/").casefold()


def verify_unity(
    config: dict[str, Any], project_root: Path, content_fingerprint: str
) -> dict[str, Any]:
    mode = config.get("mode")
    if mode == "disabled":
        return {
            "status": "unity_unverified",
            "reason_code": "unity_mcp_disabled",
            "verified": False,
            "content_fingerprint": content_fingerprint,
        }
    adapter_id = config.get("adapter")
    adapter = ADAPTERS.get(str(adapter_id))
    if adapter is None:
        raise UnityMcpError("unity_adapter_unregistered", f"Unregistered Unity adapter: {adapter_id}")
    client = StreamableHttpClient(
        str(config["url"]),
        int(config["connect_timeout_seconds"]),
        int(config["operation_timeout_minutes"]) * 60,
    )
    initialized = client.initialize()
    capabilities = initialized.get("capabilities")
    if not isinstance(capabilities, dict) or "resources" not in capabilities or "tools" not in capabilities:
        raise UnityMcpError("unity_mcp_capability_mismatch", "Unity MCP must support resources and tools")
    listed = client.request("tools/list")
    tools = listed.get("tools") if isinstance(listed, dict) else None
    names = {item.get("name") for item in tools or [] if isinstance(item, dict)}
    missing = sorted(set(adapter.required_tools) - names)
    if missing:
        raise UnityMcpError("unity_mcp_tool_mismatch", f"Unity MCP tools missing: {', '.join(missing)}")
    tool_map = {item.get("name"): item for item in tools or [] if isinstance(item, dict)}
    refresh_schema = tool_map["refresh_unity"].get("inputSchema")
    console_schema = tool_map["read_console"].get("inputSchema")
    if not isinstance(refresh_schema, dict) or not isinstance(console_schema, dict):
        raise UnityMcpError("unity_mcp_tool_mismatch", "Unity MCP tool schemas are missing")
    refresh_properties = refresh_schema.get("properties")
    if not isinstance(refresh_properties, dict) or not {"mode", "scope", "compile", "wait_for_ready"}.issubset(refresh_properties):
        raise UnityMcpError("unity_mcp_tool_mismatch", "refresh_unity schema is incompatible")
    resources = client.request("resources/list")
    resource_values = resources.get("resources") if isinstance(resources, dict) else None
    resource_uris = {item.get("uri") for item in resource_values or [] if isinstance(item, dict)}
    required_resources = {adapter.project_resource, adapter.editor_state_resource, "mcpforunity://instances"}
    if not required_resources.issubset(resource_uris):
        raise UnityMcpError("unity_mcp_resource_mismatch", "Unity MCP resources are incompatible")
    project_info = _content_json(client.request("resources/read", {"uri": adapter.project_resource}))
    if not isinstance(project_info, dict) or not isinstance(project_info.get("projectRoot"), str):
        raise UnityMcpError("unity_project_identity_missing", "Unity MCP did not return projectRoot")
    expected = (project_root / str(config["expected_project_root"])).resolve()
    if _normalize_path(project_info["projectRoot"]) != _normalize_path(str(expected)):
        raise UnityMcpError("unity_project_mismatch", "Unity MCP is connected to a different project")
    editor_before = _content_json(client.request("resources/read", {"uri": adapter.editor_state_resource}))
    if not isinstance(editor_before, dict):
        raise UnityMcpError("unity_mcp_resource_mismatch", "Unity editor state is not an object")
    editor_before = editor_before.get("data", editor_before)
    if not isinstance(editor_before, dict):
        raise UnityMcpError("unity_mcp_resource_mismatch", "Unity editor state data is invalid")
    if editor_before.get("is_playing") or editor_before.get("isPlaying"):
        raise UnityMcpError("unity_editor_busy", "Unity Editor is in Play Mode")
    if editor_before.get("is_compiling") or editor_before.get("isCompiling"):
        raise UnityMcpError("unity_editor_busy", "Unity Editor is compiling")
    blocking = editor_before.get("blocking_reasons") or editor_before.get("blockingReasons")
    advice = editor_before.get("advice")
    if isinstance(advice, dict):
        blocking = blocking or advice.get("blocking_reasons") or advice.get("blockingReasons")
        ready = advice.get("ready_for_tools", advice.get("readyForTools"))
        if ready is False:
            blocking = blocking or ["not ready for tools"]
    if blocking:
        raise UnityMcpError("unity_editor_busy", "Unity Editor reports blocking state")
    refresh = client.request(
        "tools/call",
        {
            "name": "refresh_unity",
            "arguments": {
                "mode": "force",
                "scope": "scripts",
                "compile": "request",
                "wait_for_ready": True,
            },
        },
    )
    if isinstance(refresh, dict) and refresh.get("isError"):
        raise UnityMcpError("unity_refresh_failed", "Unity refresh or compilation failed")
    editor_after = _content_json(client.request("resources/read", {"uri": adapter.editor_state_resource}))
    if not isinstance(editor_after, dict):
        raise UnityMcpError("unity_mcp_resource_mismatch", "Unity editor state is not an object")
    editor_after = editor_after.get("data", editor_after)
    if not isinstance(editor_after, dict):
        raise UnityMcpError("unity_mcp_resource_mismatch", "Unity editor state data is invalid")
    if editor_after.get("is_compiling") or editor_after.get("isCompiling"):
        raise UnityMcpError("unity_compile_incomplete", "Unity compilation did not finish")
    after_advice = editor_after.get("advice") if isinstance(editor_after.get("advice"), dict) else {}
    ready = editor_after.get("ready_for_tools", editor_after.get("readyForTools", after_advice.get("ready_for_tools", after_advice.get("readyForTools"))))
    if ready is not True:
        raise UnityMcpError("unity_editor_not_ready", "Unity Editor did not confirm readiness")
    console = client.request(
        "tools/call",
        {
            "name": "read_console",
            "arguments": {
                "action": "get",
                "types": ["error"],
                "count": 20,
                "format": "json",
                "include_stacktrace": False,
            },
        },
    )
    if isinstance(console, dict) and console.get("isError"):
        raise UnityMcpError("unity_console_failed", "Unable to inspect Unity console")
    console_value = _content_json(console)
    errors: list[Any] = []
    if isinstance(console_value, list):
        errors = console_value
    elif isinstance(console_value, dict):
        candidate = console_value.get("messages") or console_value.get("items") or []
        if isinstance(candidate, list):
            errors = candidate
        count = console_value.get("count") or console_value.get("total")
        if isinstance(count, int) and count > 0 and not errors:
            errors = [count]
    if errors:
        raise UnityMcpError("unity_compile_errors", f"Unity reported {len(errors)} error entries")
    return {
        "status": "verified",
        "reason_code": "unity_compilation_passed",
        "verified": True,
        "adapter": adapter.adapter_id,
        "adapter_version": adapter.version,
        "protocol_version": client.protocol_version,
        "content_fingerprint": content_fingerprint,
    }


def run_verification(config: dict[str, Any], project_root: Path, fingerprint: str) -> dict[str, Any]:
    mode = config.get("mode")
    try:
        return verify_unity(config, project_root, fingerprint)
    except UnityMcpError as exc:
        if mode == "optional":
            return {
                "status": "unity_unverified",
                "reason_code": exc.reason_code,
                "summary": exc.summary,
                "verified": False,
                "content_fingerprint": fingerprint,
            }
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-json", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--content-fingerprint", required=True)
    args = parser.parse_args(argv)
    try:
        value = json.loads(args.config_json.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise UnityMcpError("invalid_unity_mcp_config", "Config must be an object")
        print(json.dumps(run_verification(value, args.project_root, args.content_fingerprint), ensure_ascii=False, indent=2))
        return 0
    except (UnityMcpError, OSError, json.JSONDecodeError) as exc:
        reason = exc.reason_code if isinstance(exc, UnityMcpError) else "unity_mcp_error"
        print(json.dumps({"ok": False, "reason_code": reason, "summary": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
