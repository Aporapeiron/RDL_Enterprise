"""
Tests for RDL Simulation Harness Core Components
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from datetime import datetime
from rdl_simulation.clock import SimulationClock
from rdl_simulation.events import EventQueue, SimEvent, EventType
from rdl_simulation.agent import Persona, UserAgent, AuthorityAgent, EnvironmentAgent
from rdl_simulation.metrics import SimMetricsCollector
from rdl_simulation.world import SimulationWorld
from rdl_simulation.scenario import ScenarioPack


class DummyScenario(ScenarioPack):
    def __init__(self):
        super().__init__("dummy_scenario", "Testing dummy events")

    def setup(self, world: SimulationWorld) -> None:
        self.schedule_event(
            day=1,
            hour=10,
            event_type="test_event",
            source_id="agent_1",
            target_id="agent_2",
            payload={"msg": "hello"},
        )


class TestSimulationHarness(unittest.TestCase):
    def test_clock_progression(self):
        clock = SimulationClock(minutes_per_tick=15)
        self.assertEqual(clock.current_tick, 0)
        self.assertEqual(clock.current_day, 1)

        # 4 ticks = 1 hour
        clock.tick(4)
        self.assertEqual(clock.current_tick, 4)
        self.assertEqual(clock.current_day, 1)

        # 24 hours = 96 ticks total
        clock.advance_hours(23)
        self.assertEqual(clock.current_day, 2)

    def test_event_queue_ordering(self):
        eq = EventQueue()
        eq.push(scheduled_tick=10, event_type="late", source_id="a", target_id="b", priority=10)
        eq.push(scheduled_tick=5, event_type="early", source_id="a", target_id="b", priority=10)
        eq.push(scheduled_tick=5, event_type="urgent", source_id="a", target_id="b", priority=1)

        # tick 4 -> ready is empty
        ready = eq.pop_ready(4)
        self.assertEqual(len(ready), 0)

        # tick 5 -> urgent first, then early
        ready = eq.pop_ready(5)
        self.assertEqual(len(ready), 2)
        self.assertEqual(ready[0].event_type, "urgent")
        self.assertEqual(ready[1].event_type, "early")

        # tick 10 -> late
        ready = eq.pop_ready(10)
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0].event_type, "late")

    def test_persona_finite_observation(self):
        # 1. 熟練ペルソナ（高忍耐、高信頼性、低曖昧さ）
        veteran = Persona(name="田中ベテラン", cohort="veteran", expertise=0.9, patience=0.95, feedback_reliability=1.0)
        res_vet = veteran.evaluate_response("VPN", "VPNの接続方法はProfileを再作成してください", oracle_truth="Profile")
        self.assertFalse(res_vet["abandoned"])
        self.assertTrue(res_vet["user_resolved"])
        self.assertFalse(res_vet["complaint"])

        # 2. 短気ペルソナ（極低忍耐、誤答時に即放置/苦情）
        impatient = Persona(name="短気新人", cohort="impatient", expertise=0.1, patience=0.0, feedback_reliability=1.0)
        res_imp = impatient.evaluate_response("VPN", "関係のない案内です", oracle_truth="Profile")
        # 正解ではないため、忍耐ゼロなら放置または苦情
        self.assertTrue(res_imp["abandoned"] or res_imp["user_resolved"] is False)

    def test_world_step_and_scenario(self):
        world = SimulationWorld(minutes_per_tick=15)
        user = UserAgent("user_01", Persona("一般ユーザー"))
        world.register_agent(user)

        received_events = []
        world.register_event_handler("test_event", lambda ev, w: received_events.append(ev))

        scenario = DummyScenario()
        world.load_scenario(scenario)

        # Day 1, hour 10:00 は start_time (09:00) から 1時間後 = 4 ticks
        # 3 ticks 進めてもまだ発火しない
        for _ in range(3):
            world.step()
        self.assertEqual(len(received_events), 0)

        # 4 tick 目で発火
        world.step()
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].payload["msg"], "hello")

    def test_replayer_determinism_comparison(self):
        from rdl_simulation.replay import SimulationReplayer, TraceRecord

        t1 = [
            TraceRecord(tick=4, day=1, event_type="test", source_id="u1", target_id="ai", payload={"q": "v"}),
            TraceRecord(tick=8, day=1, event_type="fb", source_id="u1", target_id="ai", payload={"r": True}),
        ]
        t2 = [
            TraceRecord(tick=4, day=1, event_type="test", source_id="u1", target_id="ai", payload={"q": "v"}),
            TraceRecord(tick=8, day=1, event_type="fb", source_id="u1", target_id="ai", payload={"r": True}),
        ]
        t3 = [
            TraceRecord(tick=4, day=1, event_type="test", source_id="u1", target_id="ai", payload={"q": "v"}),
            TraceRecord(tick=9, day=1, event_type="fb", source_id="u1", target_id="ai", payload={"r": True}),
        ]

        ok, msg = SimulationReplayer.compare_traces(t1, t2)
        self.assertTrue(ok)
        self.assertIsNone(msg)

        ok_bad, msg_bad = SimulationReplayer.compare_traces(t1, t3)
        self.assertFalse(ok_bad)
        self.assertIn("Tick 不一致", msg_bad)


if __name__ == "__main__":
    unittest.main()
