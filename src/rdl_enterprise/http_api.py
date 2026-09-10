"""Localhost-only read-only HTTP boundary for business queries."""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .authority import AuthorityContext
from .business_query import handle_business_query
from .service import EnterpriseService
from .tool_execution import ToolRegistry


MAX_BODY_BYTES = 16 * 1024


def _response_status(result: Dict[str, Any]) -> int:
    return {
        "NOT_EVALUATED": HTTPStatus.BAD_REQUEST,
        "AUTHORIZATION_REJECTED": HTTPStatus.FORBIDDEN,
        "PROVIDER_NOT_FOUND": HTTPStatus.NOT_FOUND,
        "PROVIDER_AUTH_ERROR": HTTPStatus.BAD_GATEWAY,
        "PROVIDER_UNAVAILABLE": HTTPStatus.SERVICE_UNAVAILABLE,
        "UNKNOWN": HTTPStatus.BAD_GATEWAY,
    }.get(result.get("routing_status"), HTTPStatus.OK)


def create_query_server(
    service: EnterpriseService,
    registry: ToolRegistry,
    bearer_token: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a server; binding beyond localhost requires an explicit caller choice."""
    if not bearer_token:
        raise ValueError("RDL_API_BEARER_TOKEN is required")

    class QueryHandler(BaseHTTPRequestHandler):
        server_version = "RDLQuery/0.1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _write(self, status: int, payload: Dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:
            self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path != "/query":
                self._write(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            authorization = self.headers.get("Authorization", "")
            prefix = "Bearer "
            supplied = authorization[len(prefix):] if authorization.startswith(prefix) else ""
            if not supplied or not hmac.compare_digest(supplied, bearer_token):
                self._write(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            length_text = self.headers.get("Content-Length")
            try:
                length = int(length_text) if length_text is not None else -1
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
                return
            try:
                raw = self.rfile.read(length)
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
                return
            if not isinstance(body, dict) or not isinstance(body.get("text"), str) or not body["text"].strip():
                self._write(HTTPStatus.BAD_REQUEST, {"error": "text_required"})
                return
            actor = AuthorityContext(
                actor_id="local-api-service",
                role="operator",
                scope="workflow",
                actor_type="service",
                authenticated_by="api_key",
                source="official_system",
            )
            result = handle_business_query(service, registry, body["text"], actor)
            self._write(_response_status(result), result)

    return ThreadingHTTPServer((host, port), QueryHandler)
