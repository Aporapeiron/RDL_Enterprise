from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class AuthorityContext:
    """
    権限コンテキスト
    誰が、どのロールで、どの業務範囲について、いつ指示したかを明示する
    """
    actor_id: str
    role: str                       # "admin" | "manager" | "senior" | "operator"
    scope: str = "all"              # 対象業務ドメイン (例: "all", "network", "workflow")
    actor_type: str = "unknown"     # "unknown" | "human" | "service" | "agent"
    authenticated_by: Optional[str] = None  # None | "idp_sso" | "mfa" | "console" | "passkey" | "api_key" | "delegated_agent"
    source: str = "official_system" # "official_system" | "slack" | "direct_order"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def is_authorized_for(self, target_domain: str) -> bool:
        """指定されたドメインに対して正式な変更権限を持っているかを判定"""
        if self.role in ("admin", "manager") and (self.scope == "all" or self.scope == target_domain):
            return True
        return False

    def is_human_authenticated(self) -> bool:
        """認証された真正な人間主体 (Human Actor) であるかを判定"""
        trusted_human_auth_methods = ("idp_sso", "mfa", "console", "passkey")
        return (
            self.actor_type == "human"
            and self.authenticated_by in trusted_human_auth_methods
        )

