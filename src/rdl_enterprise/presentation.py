"""Human-readable presentation for bounded business-query results."""

from __future__ import annotations

from typing import Any, Mapping


def format_business_query_result(result: Mapping[str, Any]) -> str:
    """Format an existing result without routing, execution, or inference."""
    status = result.get("routing_status")
    if status == "RESOLVED":
        case_id = result.get("case_id", "")
        summary = result.get("summary", "")
        provider_status = result.get("status", "")
        owner = result.get("owner")
        lines = [
            f'{case_id}「{summary}」は、',
            f'Jira上の現在の観測では「{provider_status}」です。',
        ]
        if owner is None:
            lines.append("担当者はまだ割り当てられていません。")
        elif isinstance(owner, str):
            lines.append(f"担当者は{owner}です。")
        else:
            lines.append("担当者は確認できませんでした。")
        return "\n".join(lines)
    if status == "UNRESOLVED":
        return "対象チケットを一意に特定できませんでした。\nIT-3 のようなチケット番号を指定してください。"
    if status == "NOT_EVALUATED":
        return "評価対象となる問い合わせがありませんでした。"
    if status == "UNKNOWN":
        return "問い合わせ結果を確定できませんでした。追加の観測が必要です。"
    if status == "AUTHORIZATION_REJECTED":
        return "この問い合わせを実行する権限が確認できませんでした。"
    if status == "PROVIDER_AUTH_ERROR":
        return "Jira接続側の認証を確認できませんでした。"
    if status == "PROVIDER_NOT_FOUND":
        return "指定された対象を、今回のJira観測では取得できませんでした。"
    if status == "PROVIDER_UNAVAILABLE":
        return "Jiraを現在観測できませんでした。時間を置いて再試行してください。"
    return "問い合わせ結果を確定できませんでした。"
