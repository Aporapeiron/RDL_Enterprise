import json
import os
import sys
import unittest
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from rdl_query import main
from rdl_enterprise.canary import ActionCapability


class TestReadOnlyQueryCLI(unittest.TestCase):
    def test_cli_prints_bounded_result_without_provider_secret(self):
        with patch("rdl_query.AtlassianJiraConnector.from_environment") as factory:
            connector = factory.return_value
            connector.tool_spec.return_value = type("Spec", (), {
                "tool_id": "atlassian.jira.issue.lookup",
                "domain": "workflow",
                "capability": ActionCapability.DRY_RUN_ONLY,
                "handler": lambda payload: {"case_id": "IT-3"},
            })()
            with patch("rdl_query.handle_business_query", return_value={
                "routing_status": "RESOLVED", "case_id": "IT-3", "status": "Open",
                "owner": None, "summary": "VPN issue", "source": "atlassian_jira",
            }), patch("sys.stdout", new_callable=StringIO) as stdout:
                self.assertEqual(main(["IT-3って今どうなってる？", "--actor-id", "cli-user", "--json"]), 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["case_id"], "IT-3")
        self.assertNotIn("token", stdout.getvalue().lower())
