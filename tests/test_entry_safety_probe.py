from __future__ import annotations

import unittest

from scripts.generate_pool import (
    apply_feedback_price_adjustment,
    entry_effectiveness_factors,
    entry_safety_effect_for_row,
)


def candidate_row() -> dict:
    return {
        "code": "600001",
        "theme": "测试主题",
        "score": 8.0,
        "trend_score_bonus": 0.8,
        "close": 50.0,
        "recommended_entry_price": 48.0,
        "entry_price_lower": 47.0,
        "entry_price_upper": 49.0,
        "buyable_price": 50.0,
        "buyable_price_lower": 49.5,
        "buyable_price_upper": 51.0,
        "next_buy_trigger_price": 49.5,
        "breakout_buy_upper_price": 51.0,
        "breakout_confirm_price": 49.5,
        "invalid_price": 44.0,
        "no_chase_price": 55.0,
        "entry_gap_pct": 4.2,
        "watch_zone": "47.00-49.00",
        "buy_signal_key": "breakout_buy",
        "buy_signal_label": "可突破试探",
        "buy_price_path": "突破确认",
        "buy_price_note": "基础信号成立。",
        "entry_price_note": "基础接入区间。",
        "is_buyable_now": True,
        "status_key": "breakout",
        "intervention_status": "突破确认",
        "trend_trade_eligible": True,
        "trend_label": "上涨趋势",
        "fund_flow_score": 8.0,
    }


class EntrySafetyProbeTests(unittest.TestCase):
    def test_broad_risk_factor_cannot_hard_block_even_with_severe_history(self) -> None:
        row = candidate_row()
        factor = entry_effectiveness_factors(row)[0]
        feedback = {
            "entry_effectiveness": {
                "observation_count": 100,
                "factor_stats": [
                    {
                        "id": factor["id"],
                        "sample_count": 100,
                        "confidence": 1.0,
                        "price_adjustment_pct": -2.0,
                        "risk_level": "高",
                        "avg_touch_return_pct": -6.0,
                        "avg_adverse_drawdown_pct": -13.0,
                        "crash_rate_pct": 65.0,
                        "actual_buyable_count": 10,
                        "touched_entry_count": 2,
                        "untouched_wait_count": 88,
                    }
                ],
            }
        }

        safety = entry_safety_effect_for_row(row, feedback)
        self.assertTrue(safety["entry_safety_risk_flag"])
        self.assertFalse(safety["entry_safety_block_buy"])
        self.assertEqual(safety["entry_safety_hard_evidence_count"], 0)

        apply_feedback_price_adjustment(row, {"feedback_bonus": 0.0}, safety)
        self.assertTrue(row["is_buyable_now"])
        self.assertEqual(row["buy_signal_key"], "risk_probe")
        self.assertTrue(row["entry_safety_probe_only"])

    def test_severe_touch_evidence_keeps_hard_block(self) -> None:
        row = candidate_row()
        factor = next(item for item in entry_effectiveness_factors(row) if item["dimension"] == "status")
        feedback = {
            "entry_effectiveness": {
                "observation_count": 50,
                "factor_stats": [
                    {
                        "id": factor["id"],
                        "sample_count": 50,
                        "confidence": 1.0,
                        "price_adjustment_pct": -2.0,
                        "risk_level": "高",
                        "avg_touch_return_pct": -6.0,
                        "avg_adverse_drawdown_pct": -13.0,
                        "crash_rate_pct": 65.0,
                        "actual_buyable_count": 8,
                        "touched_entry_count": 4,
                        "untouched_wait_count": 38,
                    }
                ],
            }
        }

        safety = entry_safety_effect_for_row(row, feedback)
        self.assertTrue(safety["entry_safety_block_buy"])
        self.assertEqual(safety["entry_safety_hard_evidence_count"], 1)

        apply_feedback_price_adjustment(row, {"feedback_bonus": 0.0}, safety)
        self.assertFalse(row["is_buyable_now"])
        self.assertEqual(row["buy_signal_key"], "risk_wait")


if __name__ == "__main__":
    unittest.main()
