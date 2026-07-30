from __future__ import annotations

import unittest

from scripts.cloud_simulation import default_simulation, run_cloud_simulation


def payload(
    generated_at: str,
    phase: str,
    price: float,
    *,
    score: float = 9.2,
    signal: str = "wait",
    status: str = "wait",
    entry_upper: float = 50.0,
    atr: float = 4.0,
    ma20: float = 49.0,
    ma60: float | None = None,
    trend_eligible: bool = True,
) -> dict:
    labels = {
        "morning_entry": "10点早盘接入",
        "afternoon_risk": "14:30尾盘风控",
        "evening_watch": "20点次日关注",
    }
    trade_date = generated_at[:10]
    stock = {
        "code": "600001",
        "name": "测试股份",
        "score": score,
        "close": price,
        "live_quote_price": price,
        "live_quote_date": trade_date,
        "recommended_entry_price": 48.0,
        "entry_price_upper": entry_upper,
        "atr14": atr,
        "no_chase_price": 60.0,
        "invalid_price": 44.0,
        "ma20": ma20,
        "ma60": ma60 if ma60 is not None else ma20 - 3.0,
        "ma20_slope_5d_pct": 1.2 if trend_eligible else -1.2,
        "ma60_slope_10d_pct": 0.4 if trend_eligible else -0.5,
        "trend_trade_eligible": trend_eligible,
        "trend_block_buy": not trend_eligible,
        "trend_label": "上涨趋势" if trend_eligible else "下降趋势",
        "resistance_price": 58.0,
        "buy_signal_key": signal,
        "buy_signal_label": "等待触发" if signal == "wait" else "回撤可买",
        "buyable_price": price if signal in {"pullback_buy", "breakout_buy"} else None,
        "buyable_price_upper": entry_upper if signal in {"pullback_buy", "breakout_buy"} else None,
        "status_key": status,
        "is_buyable_now": signal in {"pullback_buy", "breakout_buy"},
        "entry_safety_block_buy": False,
        "market_context_block_buy": False,
        "fund_flow_score": 0.0,
        "market_regime": "warm",
        "market_risk_appetite": 0.85,
    }
    return {
        "generated_at": generated_at,
        "as_of_date": trade_date,
        "update_phase": phase,
        "update_phase_label": labels[phase],
        "source_status": {"fallback": False},
        "universe_scan": {
            "update_phase": phase,
            "update_phase_label": labels[phase],
            "market_environment": {"regime": "warm", "risk_appetite": 0.85},
        },
        "stocks": [stock],
        "review": {"records": []},
    }


class CloudSimulationTests(unittest.TestCase):
    def test_warm_high_score_plan_buys_only_inside_atr_buffer(self) -> None:
        state = run_cloud_simulation(payload("2026-07-13T20:00:00+08:00", "evening_watch", 57.0, ma20=55.0), default_simulation())
        self.assertEqual(state["pendingBuyOrders"][0]["planType"], "trial")
        self.assertEqual(state["pendingBuyOrders"][0]["maxBuyPrice"], 56.0)

        state = run_cloud_simulation(payload("2026-07-14T10:00:00+08:00", "morning_entry", 57.0, ma20=55.0), state)
        self.assertEqual(state["diagnostics"]["buysExecuted"], 0)
        self.assertEqual(state["diagnostics"]["waitReasonCounts"]["含滑点价格高于最高买入价"], 1)

        state = run_cloud_simulation(payload("2026-07-14T11:20:00+08:00", "morning_entry", 55.0, ma20=55.0), state)
        self.assertEqual(state["diagnostics"]["buysExecuted"], 1)
        self.assertEqual(state["positions"]["600001"]["quantity"], 100)
        self.assertLess(state["cash"], 95_000)
        self.assertGreater(state["cash"], 94_000)

    def test_observation_plan_does_not_buy_without_signal_upgrade(self) -> None:
        evening = payload("2026-07-13T20:00:00+08:00", "evening_watch", 49.0, score=8.0)
        state = run_cloud_simulation(evening, default_simulation())
        self.assertEqual(state["pendingBuyOrders"][0]["planType"], "watch")

        morning = payload("2026-07-14T10:00:00+08:00", "morning_entry", 48.0, score=8.0)
        state = run_cloud_simulation(morning, state)
        self.assertEqual(state["positions"], {})
        self.assertEqual(state["diagnostics"]["waitReasonCounts"]["观察计划尚未升级为可执行信号"], 1)

    def test_t_plus_one_blocks_same_day_stop_and_sells_next_day(self) -> None:
        state = run_cloud_simulation(payload("2026-07-13T20:00:00+08:00", "evening_watch", 49.0, signal="pullback_buy"), default_simulation())
        state = run_cloud_simulation(payload("2026-07-14T10:00:00+08:00", "morning_entry", 49.0, signal="pullback_buy"), state)
        self.assertIn("600001", state["positions"])

        state = run_cloud_simulation(payload("2026-07-14T14:30:00+08:00", "afternoon_risk", 44.5, signal="wait"), state)
        self.assertIn("600001", state["positions"])
        self.assertEqual(state["diagnostics"]["sellsExecuted"], 0)

        state = run_cloud_simulation(payload("2026-07-15T10:00:00+08:00", "morning_entry", 44.5, signal="wait"), state)
        self.assertNotIn("600001", state["positions"])
        self.assertEqual(state["diagnostics"]["sellsExecuted"], 1)
        self.assertEqual(state["trades"][0]["type"], "sell")

    def test_initial_buy_loss_is_only_slippage_and_fees(self) -> None:
        state = run_cloud_simulation(payload("2026-07-13T20:00:00+08:00", "evening_watch", 49.0, signal="pullback_buy"), default_simulation())
        state = run_cloud_simulation(payload("2026-07-14T10:00:00+08:00", "morning_entry", 49.0, signal="pullback_buy"), state)
        position = state["positions"]["600001"]
        marked_assets = state["cash"] + position["quantity"] * 49.0
        return_pct = (marked_assets / state["initialCash"] - 1) * 100
        self.assertGreater(return_pct, -0.1)
        self.assertLess(return_pct, 0)

    def test_stale_trial_plan_does_not_block_a_new_candidate(self) -> None:
        state = run_cloud_simulation(payload("2026-07-13T20:00:00+08:00", "evening_watch", 57.0, ma20=55.0), default_simulation())
        self.assertEqual(state["pendingBuyOrders"][0]["code"], "600001")

        morning = payload("2026-07-14T10:00:00+08:00", "morning_entry", 55.0, ma20=55.0)
        morning["stocks"][0]["code"] = "600002"
        morning["stocks"][0]["name"] = "新候选"
        state = run_cloud_simulation(morning, state)

        self.assertIn("600002", state["positions"])
        self.assertNotIn("600001", state["positions"])

    def test_trial_plan_does_not_chase_far_above_ma20(self) -> None:
        state = run_cloud_simulation(payload("2026-07-13T20:00:00+08:00", "evening_watch", 55.0), default_simulation())
        state = run_cloud_simulation(payload("2026-07-14T10:00:00+08:00", "morning_entry", 55.0, ma20=50.0), state)

        self.assertEqual(state["positions"], {})
        self.assertEqual(state["diagnostics"]["waitReasonCounts"]["价格高于MA20超过5%，避免追涨后回撤"], 1)

    def test_downtrend_stock_does_not_generate_a_buy_plan(self) -> None:
        evening = payload("2026-07-13T20:00:00+08:00", "evening_watch", 49.0, trend_eligible=False)
        state = run_cloud_simulation(evening, default_simulation())

        self.assertEqual(state["pendingBuyOrders"], [])
        self.assertEqual(state["positions"], {})

    def test_existing_plan_is_cancelled_when_trend_turns_down(self) -> None:
        evening = payload("2026-07-13T20:00:00+08:00", "evening_watch", 49.0, signal="pullback_buy")
        state = run_cloud_simulation(evening, default_simulation())
        self.assertEqual(state["pendingBuyOrders"][0]["status"], "pending")

        morning = payload("2026-07-14T10:00:00+08:00", "morning_entry", 48.5, signal="pullback_buy", trend_eligible=False)
        state = run_cloud_simulation(morning, state)

        self.assertEqual(state["positions"], {})
        self.assertEqual(state["pendingBuyOrders"][0]["status"], "cancelled")
        self.assertEqual(state["pendingBuyOrders"][0]["cancelReason"], "未满足上涨趋势门槛，取消旧买入计划")


if __name__ == "__main__":
    unittest.main()
