"""Run the localhost read-only RDL business query API."""

from __future__ import annotations

import os

from rdl_enterprise import AtlassianJiraConnector, EnterpriseRuntime, EnterpriseService, ToolRegistry
from rdl_enterprise.http_api import create_query_server


def main() -> None:
    bearer = os.environ.get("RDL_API_BEARER_TOKEN", "")
    connector = AtlassianJiraConnector.from_environment()
    registry = ToolRegistry()
    registry.register(connector.tool_spec())
    server = create_query_server(EnterpriseService(EnterpriseRuntime()), registry, bearer)
    print("RDL read-only API listening on http://127.0.0.1:8765", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
