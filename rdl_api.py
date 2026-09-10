"""Run the localhost read-only RDL business query API."""

from __future__ import annotations

import os

from rdl_enterprise import AtlassianJiraConnector, EnterpriseRuntime, EnterpriseService, ToolRegistry
from rdl_enterprise.http_api import create_query_server


def build_server():
    bearer = os.environ.get("RDL_API_BEARER_TOKEN", "")
    store_path = os.environ.get("RDL_API_STORE_PATH", "")
    if not store_path.strip():
        raise ValueError("RDL_API_STORE_PATH is required")
    connector = AtlassianJiraConnector.from_environment()
    registry = ToolRegistry()
    registry.register(connector.tool_spec())
    return create_query_server(
        EnterpriseService(EnterpriseRuntime(store_path=store_path)), registry, bearer,
    )


def main() -> None:
    server = build_server()
    print("RDL read-only API listening on http://127.0.0.1:8765", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
