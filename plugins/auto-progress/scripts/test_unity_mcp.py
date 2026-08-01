from __future__ import annotations

import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import unity_mcp


class FakeMcpHandler(BaseHTTPRequestHandler):
    project_root = "."
    response_kind = "json"
    missing_tools = False

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        request_id = payload.get("id")
        method = payload.get("method")
        if request_id is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "fake-unity", "version": "1"},
            }
        elif method == "tools/list":
            names = ["refresh_unity"] if self.missing_tools else ["refresh_unity", "read_console"]
            schemas = {
                "refresh_unity": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["if_dirty", "force"]},
                        "scope": {"type": "string", "enum": ["assets", "scripts", "all"]},
                        "compile": {"type": "string", "enum": ["none", "request"]},
                        "wait_for_ready": {"type": "boolean"},
                    },
                },
                "read_console": {"type": "object", "properties": {}},
            }
            result = {
                "tools": [
                    {"name": name, "description": name, "inputSchema": schemas[name]}
                    for name in names
                ]
            }
        elif method == "resources/list":
            result = {
                "resources": [
                    {"uri": "mcpforunity://project/info", "name": "project_info"},
                    {"uri": "mcpforunity://editor/state", "name": "editor_state"},
                    {"uri": "mcpforunity://instances", "name": "instances"},
                ]
            }
        elif method == "resources/read":
            uri = payload.get("params", {}).get("uri")
            if uri == "mcpforunity://project/info":
                value = {"projectRoot": self.project_root}
            elif uri == "mcpforunity://editor/state":
                value = {
                    "isPlaying": False,
                    "isCompiling": False,
                    "readyForTools": True,
                    "blockingReasons": [],
                }
            else:
                value = {}
            result = {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": json.dumps(value)}
                ]
            }
        elif method == "tools/call":
            name = payload.get("params", {}).get("name")
            value = {"messages": [], "count": 0} if name == "read_console" else {"ready": True}
            result = {
                "content": [{"type": "text", "text": json.dumps(value)}],
                "isError": False,
            }
        else:
            self._respond(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "method not found"},
                }
            )
            return
        self._respond({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _respond(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self.response_kind == "sse":
            encoded = b"event: message\n" + b"data: " + encoded + b"\n\n"
            content_type = "text/event-stream"
        else:
            content_type = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Mcp-Session-Id", "fake-session")
        self.end_headers()
        self.wfile.write(encoded)


@contextmanager
def fake_server(
    project_root: Path, *, response_kind: str = "json", missing_tools: bool = False
) -> Iterator[str]:
    handler = type(
        "ConfiguredFakeMcpHandler",
        (FakeMcpHandler,),
        {
            "project_root": str(project_root.resolve()),
            "response_kind": response_kind,
            "missing_tools": missing_tools,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/mcp"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def unity_config(url: str, mode: str = "optional") -> dict[str, object]:
    return {
        "mode": mode,
        "adapter": "coplaydev-unity-mcp",
        "transport": "streamable_http",
        "url": url,
        "expected_project_root": ".",
        "connect_timeout_seconds": 2,
        "operation_timeout_minutes": 1,
    }


class UnityMcpTransportTests(unittest.TestCase):
    def test_json_streamable_http_verifies_matching_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with fake_server(project) as url:
                result = unity_mcp.run_verification(
                    unity_config(url), project, "fingerprint-json"
                )
        self.assertTrue(result["verified"])
        self.assertNotIn("status", result)
        self.assertEqual("fingerprint-json", result["content_fingerprint"])
        self.assertEqual("2025-06-18", result["protocol_version"])

    def test_sse_streamable_http_is_decoded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with fake_server(project, response_kind="sse") as url:
                result = unity_mcp.run_verification(
                    unity_config(url), project, "fingerprint-sse"
                )
        self.assertTrue(result["verified"])
        self.assertEqual("fingerprint-sse", result["content_fingerprint"])

    def test_optional_contract_failure_falls_back_to_unverified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with fake_server(project, missing_tools=True) as url:
                result = unity_mcp.run_verification(
                    unity_config(url, "optional"), project, "fingerprint-optional"
                )
        self.assertFalse(result["verified"])
        self.assertNotIn("status", result)
        self.assertEqual("unity_mcp_tool_mismatch", result["reason_code"])

    def test_required_contract_failure_blocks_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            with fake_server(project, missing_tools=True) as url:
                with self.assertRaises(unity_mcp.UnityMcpError) as captured:
                    unity_mcp.run_verification(
                        unity_config(url, "required"), project, "fingerprint-required"
                    )
        self.assertEqual("unity_mcp_tool_mismatch", captured.exception.reason_code)


if __name__ == "__main__":
    unittest.main()
