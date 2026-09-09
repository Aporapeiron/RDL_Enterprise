"""
RDL Simulation Harness - Replay & Trace Logger
シミュレーションの決定論的トレースログ記録と反実仮想再生用モジュール。
"""

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TraceRecord:
    tick: int
    day: int
    event_type: str
    source_id: str
    target_id: str
    payload: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    mb_hash_before: Optional[str] = None
    mb_hash_after: Optional[str] = None
    heat_before: Optional[float] = None
    heat_after: Optional[float] = None
    transition_type: Optional[str] = None
    state_digest_before: Optional[str] = None
    state_digest_after: Optional[str] = None


class SimTraceLogger:
    """
    全イベントの時系列トレースを記録し、再現性および監査可能性を担保する。
    """
    def __init__(self):
        self.records: List[TraceRecord] = []

    def record(
        self,
        tick: int,
        day: int,
        event_type: str,
        source_id: str,
        target_id: str,
        payload: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        mb_hash_before: Optional[str] = None,
        mb_hash_after: Optional[str] = None,
        heat_before: Optional[float] = None,
        heat_after: Optional[float] = None,
        transition_type: Optional[str] = None,
        state_digest_before: Optional[str] = None,
        state_digest_after: Optional[str] = None,
    ) -> TraceRecord:
        rec = TraceRecord(
            tick=tick,
            day=day,
            event_type=event_type,
            source_id=source_id,
            target_id=target_id,
            payload=payload,
            result=result,
            mb_hash_before=mb_hash_before,
            mb_hash_after=mb_hash_after,
            heat_before=heat_before,
            heat_after=heat_after,
            transition_type=transition_type,
            state_digest_before=state_digest_before,
            state_digest_after=state_digest_after,
        )
        self.records.append(rec)
        return rec

    def export_jsonl(self, filepath: str) -> None:
        """トレースログを JSON Lines 形式で書き出し"""
        with open(filepath, "w", encoding="utf-8") as f:
            for rec in self.records:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")

    def __len__(self) -> int:
        return len(self.records)


class ReplayContextMismatchError(Exception):
    """Replay時に外生固定条件コンテキスト (SimulationRunContext) が不一致の場合の例外"""
    pass


class SimulationReplayer:
    """
    保存されたトレースおよび RunContext を用いてシミュレーションを再演し、
    完全な決定論的一致（Exact Determinism）を検証するリプレイヤー。
    """
    @staticmethod
    def compare_traces(
        original: List[TraceRecord],
        replayed: List[TraceRecord],
        exact: bool = True,
        ignore_keys: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        2つのトレースレコード群を逐次照合。
        - exact=True (デフォルト): 全フィールド (tick, day, event_type, source_id, target_id, payload,
          result, mb_hash_before, mb_hash_after, heat_before, heat_after, transition_type) の完全一致を検証。
        - exact=False (Semantic Replay): 特定のpayloadキーを除外した意味的等価性を検証。
        """
        if len(original) != len(replayed):
            return False, f"トレースレコード長不一致: original={len(original)} vs replayed={len(replayed)}"

        ignore = set(ignore_keys or [])
        for i, (orig, rep) in enumerate(zip(original, replayed)):
            if orig.tick != rep.tick:
                return False, f"Record[{i}] Tick 不一致: orig={orig.tick} vs rep={rep.tick}"
            if orig.day != rep.day:
                return False, f"Record[{i}] Day 不一致: orig={orig.day} vs rep={rep.day}"
            if orig.event_type != rep.event_type:
                return False, f"Record[{i}] EventType 不一致: orig={orig.event_type} vs rep={rep.event_type}"
            if orig.source_id != rep.source_id:
                return False, f"Record[{i}] SourceID 不一致: orig={orig.source_id} vs rep={rep.source_id}"
            if orig.target_id != rep.target_id:
                return False, f"Record[{i}] TargetID 不一致: orig={orig.target_id} vs rep={rep.target_id}"

            # payload 照合
            if exact and not ignore:
                if orig.payload != rep.payload:
                    return False, f"Record[{i}] Payload (Exact) 不一致: orig={orig.payload} vs rep={rep.payload}"
            else:
                orig_p = {k: v for k, v in (orig.payload or {}).items() if k not in ignore}
                rep_p = {k: v for k, v in (rep.payload or {}).items() if k not in ignore}
                if orig_p != rep_p:
                    return False, f"Record[{i}] Payload 不一致: orig={orig_p} vs rep={rep_p}"

            # result 照合
            if exact and not ignore:
                if orig.result != rep.result:
                    return False, f"Record[{i}] Result (Exact) 不一致: orig={orig.result} vs rep={rep.result}"

            # 力学状態遷移 (mb_hash / heat / transition_type) の一致照合
            if orig.mb_hash_before != rep.mb_hash_before:
                return False, f"Record[{i}] mb_hash_before 不一致: orig={orig.mb_hash_before} vs rep={rep.mb_hash_before}"
            if orig.mb_hash_after != rep.mb_hash_after:
                return False, f"Record[{i}] mb_hash_after 不一致: orig={orig.mb_hash_after} vs rep={rep.mb_hash_after}"

            if orig.heat_before is not None and rep.heat_before is not None:
                if abs(orig.heat_before - rep.heat_before) > 1e-4:
                    return False, f"Record[{i}] heat_before 不一致: orig={orig.heat_before} vs rep={rep.heat_before}"
            elif orig.heat_before != rep.heat_before:
                return False, f"Record[{i}] heat_before 不一致: orig={orig.heat_before} vs rep={rep.heat_before}"

            if orig.heat_after is not None and rep.heat_after is not None:
                if abs(orig.heat_after - rep.heat_after) > 1e-4:
                    return False, f"Record[{i}] heat_after 不一致: orig={orig.heat_after} vs rep={rep.heat_after}"
            elif orig.heat_after != rep.heat_after:
                return False, f"Record[{i}] heat_after 不一致: orig={orig.heat_after} vs rep={rep.heat_after}"

            if orig.transition_type != rep.transition_type:
                return False, f"Record[{i}] transition_type 不一致: orig={orig.transition_type} vs rep={rep.transition_type}"

            # 完全状態ダイジェストの照合
            if exact:
                if orig.state_digest_before != rep.state_digest_before:
                    return False, f"Record[{i}] state_digest_before 不一致: orig={orig.state_digest_before} vs rep={rep.state_digest_before}"
                if orig.state_digest_after != rep.state_digest_after:
                    return False, f"Record[{i}] state_digest_after 不一致: orig={orig.state_digest_after} vs rep={rep.state_digest_after}"

        return True, None

    @classmethod
    def replay_from_context(
        cls,
        context: Any,
        world_factory: Any,
        scenario: Any,
        days: int,
        raise_on_mismatch: bool = True,
        original_trace: Optional[List[TraceRecord]] = None,
        original_final_digest: Optional[str] = None,
    ) -> "ReplayResult":
        """
        SimulationRunContext を外生条件として受け取り、
        world_factory から独立した世界を再構築して完全に再演(Replay)する。
        【Fail-Closed 検証】:
        世界構築直後に、コンテキストの全ハッシュ（時計、シナリオ、エージェント、アダプター、
        ワールド設定、ランタイム設定、初期 M_B）を再構築世界と照合し、1点でも不一致があれば
        ReplayContextMismatchError を送出（または即時拒絶）して実行前に遮断する。
        【Self-Verifying ReplayResult】:
        original_trace が渡された場合、実行後に再演トレースと自動照合し、
        完全一致 (trace_exact_match) および最初の乖離 (first_divergence) を自己診断して返却。
        【True Final State Proof】:
        シミュレーション終了時点の実際のランタイム状態ダイジェスト (get_state_digest) を照合。
        """
        # 1. 独立した世界インスタンスを生成
        replayed_world = world_factory(seed=context.seed)
        replayed_world.load_scenario(scenario)

        # 2. 再構築世界の RunContext と引数 context を厳密に照合 (Fail-Closed)
        rep_ctx = replayed_world.run_context
        if rep_ctx is None:
            err = "再構築世界に SimulationRunContext が生成されていません"
            if raise_on_mismatch:
                raise ReplayContextMismatchError(err)
            return ReplayResult(
                context_verified=False,
                execution_completed=False,
                trace_exact_match=False,
                final_state_match=False,
                first_divergence={"error": err},
                world=None,
                error_message=err,
            )

        mismatches = []
        if rep_ctx.seed != context.seed:
            mismatches.append(f"seed 不一致: orig={context.seed} vs rep={rep_ctx.seed}")
        if rep_ctx.clock_start_iso != context.clock_start_iso:
            mismatches.append(f"clock_start_iso 不一致: orig={context.clock_start_iso} vs rep={rep_ctx.clock_start_iso}")
        if rep_ctx.minutes_per_tick != context.minutes_per_tick:
            mismatches.append(f"minutes_per_tick 不一致: orig={context.minutes_per_tick} vs rep={rep_ctx.minutes_per_tick}")
        if rep_ctx.scenario_name != context.scenario_name:
            mismatches.append(f"scenario_name 不一致: orig={context.scenario_name} vs rep={rep_ctx.scenario_name}")
        if rep_ctx.scenario_version != context.scenario_version:
            mismatches.append(f"scenario_version 不一致: orig={context.scenario_version} vs rep={rep_ctx.scenario_version}")
        if rep_ctx.scenario_content_hash != context.scenario_content_hash:
            mismatches.append(f"scenario_content_hash 不一致: orig={context.scenario_content_hash} vs rep={rep_ctx.scenario_content_hash}")
        if rep_ctx.agent_configs_hash != context.agent_configs_hash:
            mismatches.append(f"agent_configs_hash 不一致: orig={context.agent_configs_hash} vs rep={rep_ctx.agent_configs_hash}")
        if rep_ctx.adapter_config_hash != context.adapter_config_hash:
            mismatches.append(f"adapter_config_hash 不一致: orig={context.adapter_config_hash} vs rep={rep_ctx.adapter_config_hash}")
        if getattr(context, "world_config_hash", "none") != "none" and rep_ctx.world_config_hash != context.world_config_hash:
            mismatches.append(f"world_config_hash 不一致: orig={context.world_config_hash} vs rep={rep_ctx.world_config_hash}")
        if getattr(context, "runtime_config_hash", "none") != "none" and rep_ctx.runtime_config_hash != context.runtime_config_hash:
            mismatches.append(f"runtime_config_hash 不一致: orig={context.runtime_config_hash} vs rep={rep_ctx.runtime_config_hash}")
        if rep_ctx.initial_mb_hash != context.initial_mb_hash:
            mismatches.append(f"initial_mb_hash 不一致: orig={context.initial_mb_hash} vs rep={rep_ctx.initial_mb_hash}")

        if mismatches:
            err_msg = "ReplayContextMismatch: " + " | ".join(mismatches)
            if raise_on_mismatch:
                raise ReplayContextMismatchError(err_msg)
            return ReplayResult(
                context_verified=False,
                execution_completed=False,
                trace_exact_match=False,
                final_state_match=False,
                first_divergence={"mismatches": mismatches},
                world=None,
                error_message=err_msg,
            )

        # 3. 検証合格後に再演を実行
        replayed_world.run_days(days)

        # 4. 元トレースが与えられている場合の自動検証
        trace_exact_match = True
        first_div = None
        if original_trace is not None:
            rep_trace = replayed_world.trace_logger.records
            is_match, diff_msg = cls.compare_traces(original_trace, rep_trace, exact=True)
            trace_exact_match = is_match
            if not is_match:
                # 最初の乖離箇所の特定
                first_div = {"diff_message": diff_msg}
                for i in range(min(len(original_trace), len(rep_trace))):
                    o_rec = original_trace[i]
                    r_rec = rep_trace[i]
                    sub_match, sub_diff = cls.compare_traces([o_rec], [r_rec], exact=True)
                    if not sub_match:
                        first_div = {
                            "index": i,
                            "tick": o_rec.tick,
                            "day": o_rec.day,
                            "event_type": o_rec.event_type,
                            "diff": sub_diff,
                            "original_state_digest": o_rec.state_digest_after,
                            "replayed_state_digest": r_rec.state_digest_after,
                        }
                        break
                if len(original_trace) != len(rep_trace) and "index" not in first_div:
                    first_div = {
                        "length_mismatch": f"original={len(original_trace)} vs replayed={len(rep_trace)}"
                    }

        # 5. 最終状態ダイジェストの完全一致検証 (真の最終ランタイム状態)
        final_state_match = True
        replayed_final_digest = None
        if hasattr(replayed_world, "rdl_adapter") and hasattr(replayed_world.rdl_adapter, "get_state_digest"):
            replayed_final_digest = replayed_world.rdl_adapter.get_state_digest()

        target_orig_final = original_final_digest
        if target_orig_final is None and original_trace and len(original_trace) > 0:
            target_orig_final = original_trace[-1].state_digest_after

        if target_orig_final and replayed_final_digest:
            final_state_match = (target_orig_final == replayed_final_digest)

        return ReplayResult(
            context_verified=True,
            execution_completed=True,
            trace_exact_match=trace_exact_match,
            final_state_match=final_state_match,
            first_divergence=first_div,
            world=replayed_world,
            error_message=None,
        )


@dataclass
class ReplayResult:
    """
    再演(Replay)の実行結果と自律検証サマリー。
    タプル互換性 (ok, world, err) をサポート。
    """
    context_verified: bool
    execution_completed: bool
    trace_exact_match: bool
    final_state_match: bool
    first_divergence: Optional[Dict[str, Any]] = None
    world: Optional[Any] = None
    error_message: Optional[str] = None

    @property
    def success(self) -> bool:
        """外生条件が検証され、実行が完了し、トレースが完全一致した場合に True"""
        return self.context_verified and self.execution_completed and self.trace_exact_match

    def __iter__(self):
        """後方互換性タプルアンパック: (success, world, error_message)"""
        return iter((self.success, self.world, self.error_message))
