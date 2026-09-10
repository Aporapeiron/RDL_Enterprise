"""Human-facing read-only business query CLI."""

from __future__ import annotations

import argparse
import json
import sys

from rdl_enterprise import (
    AtlassianJiraConnector,
    AuthorityContext,
    EnterpriseRuntime,
    EnterpriseService,
    ToolRegistry,
    handle_business_query,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Ask the read-only RDL business AI.")
    parser.add_argument("text", help="自然文の業務問い合わせ")
    parser.add_argument("--actor-id", required=True, help="認証済み業務actorのID")
    parser.add_argument("--scope", default="workflow", help="業務domain scope")
    parser.add_argument("--ticket-id", default="cli-business-query", help="監査用の問い合わせID")
    args = parser.parse_args(argv)

    connector = AtlassianJiraConnector.from_environment()
    registry = ToolRegistry()
    registry.register(connector.tool_spec())
    service = EnterpriseService(EnterpriseRuntime())
    actor = AuthorityContext(
        actor_id=args.actor_id,
        role="operator",
        scope=args.scope,
        actor_type="human",
        authenticated_by="console",
    )
    result = handle_business_query(service, registry, args.text, actor, ticket_id=args.ticket_id)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
