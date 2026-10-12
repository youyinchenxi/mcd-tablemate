"""Local protocol fixtures only: these tests do not call McDonald's MCP."""
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from maimai_team.mcp import MCPClient, MCPError, PROTOCOL_VERSION


SCHEMA = {"type": "object", "properties": {"store": {"type": "string"},
          "cart": {"type": "array", "items": {"type": "object", "properties": {
              "quantity": {"type": "integer", "minimum": 1}}, "required": ["quantity"],
              "additionalProperties": False}}}, "required": ["store", "cart"], "additionalProperties": False}


class MockMCPServer:
    def __init__(self, sse=False, error_status=None, expired=False, rpc_error=False, tool_error=False):
        self.calls = []
        self.sse = sse
        self.error_status = error_status
        self.expired = expired
        self.rpc_error = rpc_error
        self.tool_error = tool_error
        self.server = None

    def __enter__(self):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def respond(self, status, payload=None, session=False):
                body = b"" if payload is None else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if session:
                    self.send_header("Mcp-Session-Id", "fixture-session")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fixture.calls.append((msg, dict(self.headers)))
                if fixture.error_status:
                    self.respond(fixture.error_status, {"secret": "fixture-token NEVER ECHO"})
                    return
                method = msg["method"]
                if method == "initialize":
                    self.respond(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {
                        "protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                        "serverInfo": {"name": "LOCAL MOCK", "version": "1"}}}, session=True)
                elif method == "notifications/initialized":
                    self.respond(202)
                elif fixture.expired:
                    fixture.expired = False
                    self.respond(404)
                elif method == "tools/list":
                    if msg.get("params", {}).get("cursor"):
                        result = {"tools": [{"name": "create-order", "inputSchema": {"type": "object"}}]}
                    else:
                        result = {"tools": [{"name": "calculate-price", "inputSchema": SCHEMA}], "nextCursor": "page-2"}
                    self.respond(200, {"jsonrpc": "2.0", "id": msg["id"], "result": result})
                elif method == "tools/call":
                    if fixture.rpc_error:
                        envelope = {"jsonrpc": "2.0", "id": msg["id"], "error": {
                            "code": -32603, "message": "fixture-token NEVER ECHO"}}
                    else:
                        envelope = {"jsonrpc": "2.0", "id": msg["id"], "result": {
                            "isError": fixture.tool_error, "structuredContent": {"total": 1234},
                            "content": [{"type": "text", "text": "fixture-token NEVER ECHO"}] if fixture.tool_error else []}}
                    if fixture.sse:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.end_headers()
                        self.wfile.write(b": keep alive\n\n")
                        self.wfile.write(b'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n')
                        self.wfile.write(("event: message\ndata: " + json.dumps(envelope) + "\n\n").encode())
                        self.wfile.flush()
                        # Keep connection open longer than client timeout. Client
                        # must stop reading as soon as matching response arrives.
                        time.sleep(1)
                        self.close_connection = True
                    else:
                        self.respond(200, envelope)

            def do_DELETE(self):
                fixture.calls.append(({"method": "DELETE"}, dict(self.headers)))
                self.respond(405)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port) + "/mcp"
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class MCPTests(unittest.TestCase):
    def test_local_endpoint_never_implicitly_loads_official_token(self):
        with patch('maimai_team.mcp.load_token') as loader:
            with self.assertRaises(MCPError) as error:
                MCPClient(endpoint='http://127.0.0.1:8123')
            self.assertEqual(error.exception.code, 'local_token_required')
            loader.assert_not_called()

    def test_lifecycle_pagination_headers_json_and_close(self):
        with MockMCPServer() as fixture:
            with MCPClient("fixture-token", endpoint=fixture.url) as client:
                tools = client.list_tools()
                self.assertEqual([t["name"] for t in tools], ["calculate-price", "create-order"])
                response = client.call_tool("calculate-price", {"store": "mock-store", "cart": [{"quantity": 1}]})
                self.assertEqual(response["structuredContent"]["total"], 1234)
            methods = [msg["method"] for msg, headers in fixture.calls]
            self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list", "tools/list", "tools/call", "DELETE"])
            for msg, headers in fixture.calls[1:]:
                normalized = {k.lower(): v for k, v in headers.items()}
                self.assertEqual(normalized["mcp-session-id"], "fixture-session")
                self.assertEqual(normalized["mcp-protocol-version"], PROTOCOL_VERSION)
                self.assertEqual(normalized["authorization"], "Bearer fixture-token")

    def test_sse_returns_without_waiting_for_socket_close(self):
        with MockMCPServer(sse=True) as fixture:
            with MCPClient("fixture-token", endpoint=fixture.url, timeout=.35) as client:
                started = time.monotonic()
                response = client.call_tool("calculate-price", {"store": "mock", "cart": []})
                self.assertEqual(response["structuredContent"]["total"], 1234)
                self.assertLess(time.monotonic() - started, .7)

    def test_write_tool_blocked_before_network(self):
        with MockMCPServer() as fixture:
            client = MCPClient("fixture-token", endpoint=fixture.url)
            for tool in ("create-order", "redeem-coupon", "claim-coupon", "exchange-points"):
                with self.assertRaises(MCPError) as caught:
                    client.call_tool(tool, {})
                self.assertEqual(caught.exception.code, "tool_not_allowed")
            self.assertEqual(fixture.calls, [])

    def test_arguments_required_nested_schema_validated(self):
        with MockMCPServer() as fixture:
            with MCPClient("fixture-token", endpoint=fixture.url) as client:
                for args in ({}, {"store": "x", "cart": [{"quantity": 0}]},
                             {"store": "x", "cart": [{"quantity": True}]},
                             {"store": "x", "cart": [], "guess": 5}):
                    with self.assertRaises(MCPError) as caught:
                        client.call_tool("calculate-price", args)
                    self.assertEqual(caught.exception.code, "invalid_arguments")
                self.assertFalse(any(m["method"] == "tools/call" for m, h in fixture.calls))

    def test_http_errors_are_sanitized(self):
        for status in (401, 403, 429):
            with self.subTest(status=status), MockMCPServer(error_status=status) as fixture:
                with MCPClient("fixture-token", endpoint=fixture.url) as client:
                    with self.assertRaises(MCPError) as caught:
                        client.list_tools()
                    self.assertEqual(caught.exception.code, "http_" + str(status))
                    self.assertNotIn("fixture-token", str(caught.exception))
                    self.assertNotIn("NEVER ECHO", str(caught.exception))

    def test_rpc_and_tool_errors_are_sanitized(self):
        for flag in ("rpc_error", "tool_error"):
            with self.subTest(flag=flag), MockMCPServer(**{flag: True}) as fixture:
                with MCPClient("fixture-token", endpoint=fixture.url) as client:
                    with self.assertRaises(MCPError) as caught:
                        client.call_tool("calculate-price", {"store": "x", "cart": []})
                    self.assertEqual(caught.exception.code, "rpc_error" if flag == "rpc_error" else "tool_error")
                    self.assertNotIn("fixture-token", str(caught.exception))
                    self.assertNotIn("NEVER ECHO", str(caught.exception))

    def test_expired_session_reinitializes_once(self):
        with MockMCPServer(expired=True) as fixture:
            with MCPClient("fixture-token", endpoint=fixture.url) as client:
                client.list_tools()
            self.assertEqual(sum(m["method"] == "initialize" for m, h in fixture.calls), 2)

    def test_token_and_endpoint_requirements(self):
        with patch.dict(os.environ, {}, clear=True), patch("maimai_team.mcp.load_token", return_value=None):
            with self.assertRaises(MCPError) as caught:
                MCPClient()
            self.assertEqual(caught.exception.code, "missing_token")
        for endpoint in ("https://example.com", "https://mcp.mcd.cn.evil", "https://mcp.mcd.cn/other", "http://mcp.mcd.cn"):
            with self.assertRaises(MCPError):
                MCPClient("fixture-token", endpoint=endpoint)


if __name__ == "__main__":
    unittest.main()
