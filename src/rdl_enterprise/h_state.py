import math
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, field

@dataclass
class HeatVector:
    prediction: float = 0.0  # SPEC本来の予測誤差熱 (重み 1.0)
    input_err: float = 0.0   # 入力補助熱 (重み 0.4)

    def total(self, w_pred: float = 1.0, w_input: float = 0.4) -> float:
        return w_pred * self.prediction + w_input * self.input_err


class HState:
    def __init__(
        self,
        theta_0: float = 2.0,
        gamma: float = 0.05,
        w_pred: float = 1.0,
        w_input: float = 0.4,
    ):
        self.theta_0 = theta_0
        self.gamma = gamma
        self.w_pred = w_pred
        self.w_input = w_input

        # ノード別またはドメイン別の熱管理: node_id -> HeatVector (本番用)
        self.node_heats: Dict[str, HeatVector] = {}
        # バージョン別複合キー熱管理: (mb_version, node_id) -> HeatVector
        self.versioned_heats: Dict[Tuple[str, str], HeatVector] = {}
        # バージョン別観測統計プール (カナリアでの観測統計混入防止)
        self.versioned_observations: Dict[str, Dict[str, int]] = {}
        # 全体グローバル熱 (本番用)
        self.global_heat = HeatVector()
        # 観測可能な残存指標プール (本番用)
        self.unclassified_count = 0
        self.missing_info_count = 0
        self.unknown_input_count = 0
        self.rejection_events_count = 0
        self.total_tickets = 0

    def add_heat(
        self,
        node_id: Optional[str],
        pred_err: float = 0.0,
        input_err: float = 0.0,
        mb_version: str = "prod",
        is_canary: bool = False,
    ):
        """
        誤差 E を熱として蓄積。
        is_canary=True の場合は本番の node_heats / global_heat を汚染せず、
        versioned_heats[(mb_version, node_id)] にのみ隔離蓄積する。
        """
        target_nid = node_id or "__unmatched__"
        v_key = (mb_version, target_nid)
        if v_key not in self.versioned_heats:
            self.versioned_heats[v_key] = HeatVector()
        self.versioned_heats[v_key].prediction += pred_err
        self.versioned_heats[v_key].input_err += input_err

        # カナリア案件は本番熱状態を汚染させない
        if not is_canary:
            if node_id:
                if node_id not in self.node_heats:
                    self.node_heats[node_id] = HeatVector()
                self.node_heats[node_id].prediction += pred_err
                self.node_heats[node_id].input_err += input_err

            self.global_heat.prediction += pred_err
            self.global_heat.input_err += input_err

    def get_heat_for_version(self, node_id: str, mb_version: str = "prod") -> HeatVector:
        """特定バージョンのノード熱を取得"""
        return self.versioned_heats.get((mb_version, node_id), HeatVector())

    def clear_version_heat(self, mb_version: str):
        """ロールバック時などに特定バージョンの熱および観測統計を全消去"""
        keys_to_del = [k for k in self.versioned_heats if k[0] == mb_version]
        for k in keys_to_del:
            del self.versioned_heats[k]
        if mb_version in self.versioned_observations:
            del self.versioned_observations[mb_version]

    def record_observation(
        self,
        unclassified: bool = False,
        missing_info: bool = False,
        unknown_input: bool = False,
        rejected: bool = False,
        mb_version: str = "prod",
        is_canary: bool = False,
    ):
        """ξ_obs（観測可能な残存指標）の統計を更新 (is_canary=True時は本番統計を汚染しない)"""
        if is_canary:
            if mb_version not in self.versioned_observations:
                self.versioned_observations[mb_version] = {
                    "unclassified_count": 0,
                    "missing_info_count": 0,
                    "unknown_input_count": 0,
                    "rejection_events_count": 0,
                    "total_tickets": 0,
                }
            pool = self.versioned_observations[mb_version]
            pool["total_tickets"] += 1
            if unclassified:
                pool["unclassified_count"] += 1
            if missing_info:
                pool["missing_info_count"] += 1
            if unknown_input:
                pool["unknown_input_count"] += 1
            if rejected:
                pool["rejection_events_count"] += 1
            return

        # 本番統計
        self.total_tickets += 1
        if unclassified:
            self.unclassified_count += 1
        if missing_info:
            self.missing_info_count += 1
        if unknown_input:
            self.unknown_input_count += 1
        if rejected:
            self.rejection_events_count += 1

    def xi_obs(self, mb_version: str = "prod") -> float:
        """
        観測可能残存指標 ξ_obs ∈ [0.0, 1.0]
        未分類率、情報欠落率、未知率、差し戻し率の加重平均
        """
        if mb_version != "prod" and mb_version in self.versioned_observations:
            pool = self.versioned_observations[mb_version]
            total = pool["total_tickets"]
            if total == 0:
                return 0.0
            r_unclass = pool["unclassified_count"] / total
            r_miss = pool["missing_info_count"] / total
            r_unknown = pool["unknown_input_count"] / total
            r_reject = pool["rejection_events_count"] / total
            return min(1.0, 0.3 * r_unclass + 0.2 * r_miss + 0.3 * r_unknown + 0.2 * r_reject)

        if self.total_tickets == 0:
            return 0.0
        r_unclass = self.unclassified_count / self.total_tickets
        r_miss = self.missing_info_count / self.total_tickets
        r_unknown = self.unknown_input_count / self.total_tickets
        r_reject = self.rejection_events_count / self.total_tickets

        # 加重平均
        return min(1.0, 0.3 * r_unclass + 0.2 * r_miss + 0.3 * r_unknown + 0.2 * r_reject)

    def theta_eff(self, mb_version: str = "prod") -> float:
        """
        有効判定境界 θ_eff = θ0 - g(ξ_obs)
        g(ξ_obs) = 0.8 * ξ_obs (最大0.8引き下げ、下限0.5ガード)
        """
        xi = self.xi_obs(mb_version=mb_version)
        g_xi = 0.8 * xi
        return max(0.5, self.theta_0 - g_xi)

    def dissipate(self, node_inertias: Dict[str, float]):
        """
        熱の受動的自然散逸（冷却）
        dH/dt = - A * H,  A = diag(γ * ||M_B||)
        """
        for nid, heat in list(self.node_heats.items()):
            inertia = node_inertias.get(nid, 0.5)
            cooling_rate = min(0.5, self.gamma * (1.0 + inertia))
            heat.prediction *= (1.0 - cooling_rate)
            heat.input_err *= (1.0 - cooling_rate)

        # グローバル熱もわずかに散逸
        self.global_heat.prediction *= (1.0 - self.gamma)
        self.global_heat.input_err *= (1.0 - self.gamma)

    def should_leap(self, node_id: Optional[str] = None) -> Tuple[bool, str, float]:
        """
        H >= θ_eff の判定
        特定のノード、または全体の中で最も熱いノードが閾値を超えたかを返す
        Returns: (should_leap, hot_node_id, current_heat)
        """
        threshold = self.theta_eff()

        if node_id and node_id in self.node_heats:
            h = self.node_heats[node_id].total(self.w_pred, self.w_input)
            if h >= threshold:
                return True, node_id, h

        # 全ノードから最大熱を探す
        max_nid = None
        max_h = 0.0
        for nid, heat in self.node_heats.items():
            h_val = heat.total(self.w_pred, self.w_input)
            if h_val > max_h:
                max_h = h_val
                max_nid = nid

        if max_nid and max_h >= threshold:
            return True, max_nid, max_h

        # グローバル熱が閾値を超えた場合
        g_h = self.global_heat.total(self.w_pred, self.w_input)
        if g_h >= threshold:
            return True, "__global__", g_h

        return False, max_nid or "", max_h

    def apply_remaining_heat_after_leap(self, target_node_id: str, remaining_ratio: float = 0.2):
        """
        再編相 M_Δ 後の残存熱処理 (H_remaining)
        単なる 0 リセットではなく、未解消の不整合比率を残す
        """
        if target_node_id in self.node_heats:
            self.node_heats[target_node_id].prediction *= remaining_ratio
            self.node_heats[target_node_id].input_err *= remaining_ratio

        self.global_heat.prediction *= remaining_ratio
        self.global_heat.input_err *= remaining_ratio

    def inherit_canary_state_to_prod(
        self,
        canary_version: str,
        heat_ratio: float = 0.5,
    ):
        """
        カナリア展開完了 (Full Commit) に伴う残存熱・観測統計の継承 (公理B4: 代謝の連続性)
        カナリアで新候補自身が経験した微小な不整合 (H_canary) および観測統計 (ξ_canary) を、
        新本番のベース運用状態へ合流・引き継ぐ。
        """
        # 1. カナリア期間中に蓄積されたノード別熱を本番ノード熱にマージ
        keys_to_merge = [k for k in self.versioned_heats.keys() if k[0] == canary_version]
        for (ver, nid) in keys_to_merge:
            c_heat = self.versioned_heats[(ver, nid)]
            if nid not in self.node_heats:
                self.node_heats[nid] = HeatVector()
            self.node_heats[nid].prediction += c_heat.prediction * heat_ratio
            self.node_heats[nid].input_err += c_heat.input_err * heat_ratio

        # 2. カナリア期間中の観測統計 (未分類、欠落、未知、差し戻し) を本番グローバル統計に合流
        if canary_version in self.versioned_observations:
            c_obs = self.versioned_observations[canary_version]
            self.total_tickets += c_obs["total_tickets"]
            self.unclassified_count += c_obs["unclassified_count"]
            self.missing_info_count += c_obs["missing_info_count"]
            self.unknown_input_count += c_obs["unknown_input_count"]
            self.rejection_events_count += c_obs["rejection_events_count"]

        # 3. 隔離バケットのクリーンアップ
        self.clear_version_heat(canary_version)

