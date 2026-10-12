"""Read-only Streamable HTTP MCP client (protocol 2025-06-18).

No third-party dependencies. Tool arguments are based on discovered schemas,
never on guessed McDonald's field names. HTTP redirects are refused so that a
Bearer credential cannot be forwarded to a different service.
"""
from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
READ_ONLY_TOOLS = frozenset({
    "query-nearby-stores", "query-meals", "query-meal-detail",
    "query-store-coupons", "calculate-price", "now-time-info",
})
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class MCPError(RuntimeError):
    """An actionable local error; never includes arbitrary server error text."""

    def __init__(self, message: str, code: str = "mcp_error"):
        super().__init__(message)
        self.code = code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_token(env_file: str | Path = ".env") -> str | None:
    """Read only MCD_MCP_TOKEN, without loading unrelated environment values."""
    value = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if value:
        return value
    try:
        for line in Path(env_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            if separator and key.strip() == "MCD_MCP_TOKEN":
                return value.strip().strip("\"'") or None
    except (OSError, UnicodeError):
        pass
    return None


def _schema_matches(value, schema, root=None):
    """Validate common JSON Schema assertions, failing closed on unsupported ones.

    This deliberately small validator covers MCP tool schemas with objects,
    arrays, scalar types, bounds, enum, local refs, and alternatives. Schema
    forms it cannot validate produce a local error, rather than sending an
    unchecked request.
    """
    root = schema if root is None else root
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        raise MCPError("工具参数 schema 无效。", "invalid_schema")
    supported = {
        "$ref", "$defs", "definitions", "$schema", "$id", "title",
        "description", "default", "examples", "deprecated", "readOnly",
        "writeOnly", "type", "properties", "required", "additionalProperties",
        "items", "minItems", "maxItems", "uniqueItems", "enum", "const",
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        "minLength", "maxLength", "pattern", "format", "allOf", "anyOf",
        "oneOf", "not", "minProperties", "maxProperties", "multipleOf",
    }
    if set(schema) - supported:
        raise MCPError("工具 schema 包含当前客户端不支持的校验规则。", "unsupported_schema")
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise MCPError("工具 schema 的外部引用不受支持。", "unsupported_schema")
        target = root
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise MCPError("工具 schema 引用无效。", "invalid_schema") from None
        if not _schema_matches(value, target, root):
            return False
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword in schema:
            matches = [_schema_matches(value, s, root) for s in schema[keyword]]
            if keyword == "allOf" and not all(matches):
                return False
            if keyword == "anyOf" and not any(matches):
                return False
            if keyword == "oneOf" and sum(matches) != 1:
                return False
    if "not" in schema and _schema_matches(value, schema["not"], root):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "const" in schema and value != schema["const"]:
        return False
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        checks = {
            "null": value is None, "boolean": isinstance(value, bool),
            "object": isinstance(value, dict), "array": isinstance(value, list),
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        }
        if any(t not in checks for t in types):
            raise MCPError("工具 schema 类型不受支持。", "unsupported_schema")
        if not any(checks[t] for t in types):
            return False
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            return False
        if len(value) < schema.get("minProperties", 0) or len(value) > schema.get("maxProperties", float("inf")):
            return False
        props = schema.get("properties", {})
        for key, item in value.items():
            if key in props:
                if not _schema_matches(item, props[key], root):
                    return False
            elif schema.get("additionalProperties", True) is False:
                return False
            elif isinstance(schema.get("additionalProperties"), dict):
                if not _schema_matches(item, schema["additionalProperties"], root):
                    return False
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", float("inf")):
            return False
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            return False
        if "items" in schema and not all(_schema_matches(v, schema["items"], root) for v in value):
            return False
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", float("inf")):
            return False
        if "pattern" in schema:
            import re
            if not re.search(schema["pattern"], value):
                return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", -float("inf")) or value > schema.get("maximum", float("inf")):
            return False
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            return False
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            return False
        if "multipleOf" in schema and abs(value / schema["multipleOf"] - round(value / schema["multipleOf"])) > 1e-9:
            return False
    return True


class MCPClient:
    def __init__(self, token=None, endpoint=DEFAULT_ENDPOINT, timeout=30):
        parsed = urlsplit(endpoint)
        official = endpoint.rstrip("/") == DEFAULT_ENDPOINT
        local = parsed.hostname in {"localhost", "127.0.0.1", "::1"} and parsed.scheme in {"http", "https"}
        if not (official or local) or parsed.username or parsed.password or parsed.fragment:
            raise MCPError("仅允许官方 MCP 地址或本机测试地址。", "invalid_endpoint")
        self.endpoint = endpoint
        if local and token is None:
            raise MCPError('本机测试端点必须显式提供测试Token，不能读取正式凭证。', 'local_token_required')
        self.token = token if token is not None else load_token(Path(__file__).resolve().parent.parent / '.env')
        if not self.token or not isinstance(self.token, str) or any(c in self.token for c in "\r\n"):
            raise MCPError("缺少 MCD_MCP_TOKEN；请在环境变量或本机 .env 中配置。", "missing_token")
        self.timeout = float(timeout)
        if not 0 < self.timeout <= 300:
            raise MCPError("MCP 超时时间必须在 0 到 300 秒之间。", "invalid_timeout")
        # Local test fixtures must not route through a system HTTP proxy.
        self._opener = build_opener(_NoRedirect(), *([ProxyHandler({})] if local else []))
        self._session_id = None
        self._protocol = None
        self._counter = 0
        self._tools = None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _headers(self):
        result = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                  "Accept": "application/json, text/event-stream"}
        if self._protocol:
            result["MCP-Protocol-Version"] = self._protocol
        if self._session_id:
            result["Mcp-Session-Id"] = self._session_id
        return result

    def _post(self, message):
        try:
            request = Request(self.endpoint, data=json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                              headers=self._headers(), method="POST")
            with self._opener.open(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if message["method"] == "initialize" and session:
                    if not all(0x21 <= ord(c) <= 0x7e for c in session):
                        raise MCPError("MCP 会话标识无效。", "invalid_session")
                    self._session_id = session
                if "id" not in message:
                    if response.status != 202:
                        raise MCPError("MCP 初始化通知未被接受。", "invalid_notification_response")
                    return None
                content_type = response.headers.get_content_type()
                if content_type == "application/json":
                    body = response.read(MAX_RESPONSE_BYTES + 1)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise MCPError("MCP 响应过大。", "response_too_large")
                    envelope = json.loads(body)
                elif content_type == "text/event-stream":
                    envelope = self._read_sse(response, message["id"])
                else:
                    raise MCPError("MCP 返回了不支持的响应格式。", "invalid_response")
                return self._unpack(envelope, message["id"])
        except HTTPError as exc:
            exc.close()
            messages = {401: "MCP 凭证无效或已过期。", 403: "MCP 拒绝访问，请检查账号权限。",
                        429: "MCP 请求过于频繁，请稍后重试。", 404: "MCP 会话失效或接口不存在。"}
            raise MCPError(messages.get(exc.code, "MCP HTTP 请求失败。"), "http_" + str(exc.code)) from None
        except (URLError, OSError, socket.timeout):
            raise MCPError("MCP 网络请求失败或超时。", "network_error") from None
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise MCPError("MCP 响应或参数格式无效。", "invalid_response") from None

    @staticmethod
    def _unpack(envelope, request_id):
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0" or envelope.get("id") != request_id:
            raise MCPError("MCP 响应与请求不匹配。", "invalid_response")
        if "error" in envelope:
            raise MCPError("MCP 服务端报告调用失败。", "rpc_error")
        if not isinstance(envelope.get("result"), dict):
            raise MCPError("MCP 返回结果格式无效。", "invalid_response")
        return envelope["result"]

    def _read_sse(self, response, request_id):
        deadline = time.monotonic() + self.timeout
        total = 0
        data = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError("MCP SSE 响应超时。", "network_error")
            # urllib's HTTPResponse uses a socket-backed buffered reader.
            try:
                response.fp.raw._sock.settimeout(remaining)
            except AttributeError:
                pass
            line = response.readline(MAX_RESPONSE_BYTES + 1)
            total += len(line)
            if total > MAX_RESPONSE_BYTES:
                raise MCPError("MCP 响应过大。", "response_too_large")
            if not line:
                raise MCPError("MCP SSE 在返回结果前断开。", "invalid_response")
            line = line.decode("utf-8").rstrip("\r\n")
            if not line:
                if data:
                    envelope = json.loads("\n".join(data))
                    data = []
                    if isinstance(envelope, dict) and envelope.get("id") == request_id and ("result" in envelope or "error" in envelope):
                        return envelope
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))

    def _request(self, method, params=None, retry_session=True):
        if self._closed:
            raise MCPError("MCP 客户端已关闭。", "closed")
        self._counter += 1
        message = {"jsonrpc": "2.0", "id": self._counter, "method": method}
        if params is not None:
            message["params"] = params
        try:
            return self._post(message)
        except MCPError as exc:
            if exc.code == "http_404" and self._session_id and method != "initialize" and retry_session:
                self._session_id = None
                self._protocol = None
                self._initialize()
                return self._request(method, params, retry_session=False)
            raise

    def _initialize(self):
        if self._protocol:
            return
        result = self._request("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                               "clientInfo": {"name": "maimai-team", "version": "0.1.0"}})
        if result.get("protocolVersion") not in {PROTOCOL_VERSION, "2025-03-26"}:
            raise MCPError("MCP 协议版本不受支持。", "unsupported_version")
        if not isinstance(result.get("capabilities"), dict) or "tools" not in result["capabilities"]:
            raise MCPError("MCP 服务端未声明工具能力。", "missing_tools_capability")
        self._protocol = result["protocolVersion"]
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except MCPError:
            self._protocol = None
            raise

    def list_tools(self):
        self._initialize()
        found = {}
        cursor = None
        seen = set()
        for _ in range(100):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(result.get("tools"), list):
                raise MCPError("MCP 工具列表无效。", "invalid_response")
            for tool in result["tools"]:
                if not isinstance(tool, dict) or not isinstance(tool.get("name"), str) or not isinstance(tool.get("inputSchema"), dict):
                    raise MCPError("MCP 工具定义无效。", "invalid_schema")
                if tool["name"] in found:
                    raise MCPError("MCP 工具名称重复。", "invalid_schema")
                found[tool["name"]] = tool
            cursor = result.get("nextCursor")
            if not cursor:
                self._tools = found
                return list(found.values())
            if not isinstance(cursor, str) or cursor in seen:
                raise MCPError("MCP 工具分页标识无效。", "invalid_response")
            seen.add(cursor)
        raise MCPError("MCP 工具分页过多。", "invalid_response")

    def call_tool(self, name, args):
        if name not in READ_ONLY_TOOLS:
            raise MCPError("该工具不在只读调用白名单中。", "tool_not_allowed")
        if not isinstance(args, dict):
            raise MCPError("工具参数必须是 JSON 对象。", "invalid_arguments")
        if self._tools is None:
            self.list_tools()
        tool = self._tools.get(name)
        if not tool:
            raise MCPError("服务端未提供所需工具。", "tool_unavailable")
        try:
            valid = _schema_matches(args, tool["inputSchema"])
        except (TypeError, ValueError, KeyError, ZeroDivisionError, RecursionError):
            raise MCPError("工具参数 schema 无效。", "invalid_schema") from None
        if not valid:
            raise MCPError("参数不符合服务端发现的工具 schema；请检查必填字段和类型。", "invalid_arguments")
        result = self._request("tools/call", {"name": name, "arguments": args})
        if result.get("isError"):
            raise MCPError("MCP 工具执行失败。", "tool_error")
        return result

    def close(self):
        if self._closed:
            return
        if self._session_id:
            try:
                with self._opener.open(Request(self.endpoint, headers=self._headers(), method="DELETE"), timeout=min(self.timeout, 5)):
                    pass
            except HTTPError as exc:
                exc.close()
            except (URLError, OSError):
                pass
        self._closed = True
        self._session_id = None
        self._protocol = None
