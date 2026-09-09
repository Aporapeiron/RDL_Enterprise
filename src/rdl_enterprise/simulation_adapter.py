"""
RDL Enterprise - Simulation Adapter
RDL Simulation World と EnterpriseRuntime を接続し、
離散時間イベント駆動で業務AIの自律代謝を駆動するアダプター。
"""

from typing import Any, Dict, List, Optional
from datetime import datetime

from rdl_enterprise.runtime import EnterpriseRuntime
from rdl_enterprise.snapshot import BusinessInput, FeedbackResult
from rdl_enterprise.authority import AuthorityContext
from rdl_simulation.events import SimEvent, EventType
from rdl_simulation.world import SimulationWorld
from rdl_simulation.agent import UserAgent, AuthorityAgent


class EnterpriseSimAdapter:
    """
    SimulationWorld と EnterpriseRuntime の双方向ブリッジ。
    """
    def __init__(
        self,
        runtime: EnterpriseRuntime,
        oracle_answers: Optional[Dict[str, str]] = None,
        timeout_interval_ticks: int = 16,  # 4時間ごと (15分*16 = 4h) にタイムアウトチェック
    ):
        self.runtime = runtime
        self.oracle_answers = oracle_answers or {}
        self.timeout_interval_ticks = timeout_interval_ticks
        self._ticket_cohort_map: Dict[str, str] = {}
        self._ticket_query_map: Dict[str, str] = {}

    def handle_event(self, ev: SimEvent, world: SimulationWorld) -> None:
        """離散イベントを EnterpriseRuntime のライフサイクルにマッピング"""
        if ev.event_type == EventType.USER_TICKET.value:
            self._handle_user_ticket(ev, world)
        elif ev.event_type == EventType.USER_FEEDBACK.value:
            self._handle_user_feedback(ev, world)
        elif ev.event_type == EventType.AUTHORITY_DIRECTIVE.value:
            self._handle_authority_directive(ev, world)
        elif ev.event_type == EventType.TIMEOUT_TRIGGER.value:
            self._handle_timeout_trigger(ev, world)

    def on_tick(self, world: SimulationWorld, current_tick: int, current_day: int) -> None:
        """毎Tickの定期処理 (タイムアウト検査・散逸など)"""
        # 定期的なタイムアウト検査バッチ (保留中の古いスナップショットを失効)
        if current_tick % self.timeout_interval_ticks == 0 and self.runtime.pending_snapshots:
            # 24時間 (96 ticks) 以上放置された案件を失効
            current_dt = world.clock.current_time
            to_expire = []
            for tid, snap in list(self.runtime.pending_snapshots.items()):
                if hasattr(snap, "created_at") and snap.created_at:
                    try:
                        c_dt = datetime.fromisoformat(snap.created_at)
                        if (current_dt - c_dt).total_seconds() >= 24 * 3600:
                            to_expire.append(tid)
                    except Exception:
                        pass
            if to_expire:
                expired_results = self.runtime.expire_pending_tickets(to_expire, at=world.clock.iso_time)
                for res in expired_results:
                    cohort = self._ticket_cohort_map.get(res.ticket_id, "general")
                    world.metrics.record_feedback(
                        ticket_id=res.ticket_id,
                        cohort=cohort,
                        user_resolved=None,
                        complaint=False,
                    )

    def _handle_user_ticket(self, ev: SimEvent, world: SimulationWorld) -> None:
        """利用者の問い合わせを受信し、推論を実行"""
        payload = ev.payload
        ticket_id = payload.get("ticket_id", f"TICK-{world.clock.current_tick}-{ev.sequence_id}")
        user_id = ev.source_id
        cohort = payload.get("cohort", "general")
        query_text = payload.get("query_text", "")
        category = payload.get("category", "general")

        self._ticket_cohort_map[ticket_id] = cohort
        self._ticket_query_map[ticket_id] = query_text

        efp = BusinessInput(
            ticket_id=ticket_id,
            user_id=user_id,
            category=category,
            query_text=query_text,
            metadata={"cohort": cohort},
            created_at=world.clock.iso_time,
        )

        resp = self.runtime.dispatch_ticket(efp)

        # メトリクス記録
        world.metrics.record_ticket(
            ticket_id=ticket_id,
            cost_tier=resp.cost_tier,
            cohort=cohort,
            hitl_triggered=resp.hitl_required,
        )

        # 送信元 UserAgent の特定
        user_agent = world.get_agent(user_id)
        if isinstance(user_agent, UserAgent):
            oracle_truth = self.oracle_answers.get(category)
            eval_res = user_agent.generate_feedback(
                query=query_text,
                response_text=resp.final_output,
                oracle_truth=oracle_truth,
            )

            # 放置（abandoned）でなければ、フィードバック返信イベントを未来のTickにスケジュール
            if not eval_res.get("abandoned", False):
                delay = eval_res.get("delay_ticks", 2)
                world.event_queue.push(
                    scheduled_tick=world.clock.current_tick + delay,
                    event_type=EventType.USER_FEEDBACK.value,
                    source_id=user_id,
                    target_id="enterprise_ai",
                    payload={
                        "ticket_id": ticket_id,
                        "user_resolved": eval_res.get("user_resolved"),
                        "complaint": eval_res.get("complaint", False),
                        "feedback_text": eval_res.get("feedback_text", ""),
                        "cohort": cohort,
                    },
                    priority=10,
                )

    def _handle_user_feedback(self, ev: SimEvent, world: SimulationWorld) -> None:
        """事後フィードバックを受信し、代謝（沈澱または発熱・破断）を実行"""
        payload = ev.payload
        ticket_id = payload.get("ticket_id")
        if not ticket_id:
            return

        user_resolved = payload.get("user_resolved")
        complaint = payload.get("complaint", False)
        feedback_text = payload.get("feedback_text", "")
        cohort = payload.get("cohort", "general")

        feedback = FeedbackResult(
            user_resolved=bool(user_resolved),
            human_rejected=bool(complaint),
            feedback_comment=feedback_text,
            observed_at=world.clock.iso_time,
        )

        prev_proposals = len(self.runtime.pending_reorganizations)
        res = self.runtime.resolve_ticket_feedback(
            ticket_id=ticket_id,
            feedback=feedback,
            at=world.clock.iso_time,
        )

        # メトリクス記録
        world.metrics.record_feedback(
            ticket_id=ticket_id,
            cohort=cohort,
            user_resolved=user_resolved,
            complaint=complaint,
        )

        # M_Δ 発行検知
        if (res and res.transition_to_m_delta) or len(self.runtime.pending_reorganizations) > prev_proposals:
            world.metrics.record_m_delta_transition()

    def _handle_authority_directive(self, ev: SimEvent, world: SimulationWorld) -> None:
        """正式な組織方針の注入（Authority Commitment）"""
        payload = ev.payload
        verifier_id = payload.get("verifier_id", ev.source_id)
        role = payload.get("role", "admin")
        scope = payload.get("scope", payload.get("domain", "security"))

        auth_ctx = AuthorityContext(
            actor_id=verifier_id,
            role=role,
            scope=scope,
            actor_type="human",
            authenticated_by="idp_sso",
            timestamp=world.clock.iso_time,
        )

        domain = payload.get("domain", "security")
        trigger_keys = payload.get("trigger_pattern", {}).get("exact_keys", ["VPN新方式"])
        trigger_query = trigger_keys[0] if trigger_keys else "VPN新方式"
        action_payload = payload.get("action_template", {}).get("payload", "セキュリティ部公認の新方式案内")

        efp = BusinessInput(
            ticket_id=f"TICK-AUTH-{world.clock.current_tick}",
            user_id=verifier_id,
            category=domain,
            query_text=trigger_query,
            metadata={"cohort": "authority"},
            created_at=world.clock.iso_time,
        )

        try:
            self.runtime.cascade.inject_authoritative_rule(
                efp=efp,
                policy_text=action_payload,
                category=domain,
                authority=auth_ctx,
            )
            print(f"\n[方針注入成功] {verifier_id} ({role}) がドメイン '{domain}' に正式方針をコミットしました。")
        except PermissionError as pe:
            print(f"\n[越境介入遮断] {verifier_id} ({role}) によるドメイン '{domain}' への介入がフェイルクローズ遮断されました: {pe}")

    def _handle_timeout_trigger(self, ev: SimEvent, world: SimulationWorld) -> None:
        """タイムアウトバッチ明示発火"""
        payload = ev.payload
        target_ids = payload.get("ticket_ids")
        if target_ids is None:
            target_ids = list(self.runtime.pending_snapshots.keys())
        timed_out = self.runtime.expire_pending_tickets(target_ids, at=world.clock.iso_time)
        for res in timed_out:
            cohort = self._ticket_cohort_map.get(res.ticket_id, "general")
            world.metrics.record_feedback(
                ticket_id=res.ticket_id,
                cohort=cohort,
                user_resolved=None,
                complaint=False,
            )

    def get_dynamics_state(self) -> Dict[str, Any]:
        """力学状態の現在値を取得"""
        h_state = self.runtime.h_state
        return {
            "heat": h_state.version_total_heat("prod"),
            "theta_eff": h_state.theta_eff("prod"),
            "average_kappa": self.runtime.mb_graph.average_kappa(),
            "total_inertia": self.runtime.mb_graph.total_inertia(),
            "version": self.runtime.mb_graph.version,
            "active_nodes_count": len(self.runtime.mb_graph.nodes),
        }
