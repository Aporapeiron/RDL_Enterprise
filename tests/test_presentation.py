import unittest

from rdl_enterprise.presentation import format_business_query_result


class TestBusinessQueryPresentation(unittest.TestCase):
    def test_resolved_owner_is_human_readable(self):
        text = format_business_query_result({
            "routing_status": "RESOLVED", "case_id": "IT-3",
            "summary": "VPNに接続できない", "status": "サポートからの連絡待ち",
            "owner": "ヘルマン・デグナー", "source": "atlassian_jira",
        })
        self.assertIn("IT-3", text)
        self.assertIn("ヘルマン・デグナー", text)
        self.assertIn("Jira上の現在の観測では", text)

    def test_resolved_unassigned_owner_is_not_stringified(self):
        text = format_business_query_result({
            "routing_status": "RESOLVED", "case_id": "IT-4",
            "summary": "社内Wi-Fiに接続できない", "status": "サポートからの連絡待ち",
            "owner": None,
        })
        self.assertIn("担当者はまだ割り当てられていません", text)
        self.assertNotIn("None", text)

    def test_status_categories_remain_distinct(self):
        self.assertIn("一意に特定", format_business_query_result({"routing_status": "UNRESOLVED"}))
        self.assertIn("評価対象", format_business_query_result({"routing_status": "NOT_EVALUATED"}))
        self.assertIn("認証", format_business_query_result({"routing_status": "PROVIDER_AUTH_ERROR"}))
        self.assertIn("取得できません", format_business_query_result({"routing_status": "PROVIDER_NOT_FOUND"}))
