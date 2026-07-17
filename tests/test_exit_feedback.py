from __future__ import annotations

import unittest

from scripts.exit_feedback import BASE_EXIT_SETTINGS, build_exit_feedback, refresh_exit_feedback


def trade_state() -> dict:
    return {
        "trades": [
            {
                "id": "sell-1",
                "type": "sell",
                "code": "601899",
                "name": "紫金矿业",
                "at": "2026-07-15T14:30:00+08:00",
                "tradeDate": "2026-07-15",
                "price": 29.0,
                "quantity": 300,
                "realizedPnl": 120.0,
                "reason": "评分跌破退出阈值",
            },
            {
                "id": "buy-1",
                "type": "buy",
                "code": "601899",
                "name": "紫金矿业",
                "at": "2026-07-10T10:00:00+08:00",
                "tradeDate": "2026-07-10",
                "price": 28.5,
                "quantity": 300,
                "reason": "回撤可买",
            },
        ]
    }


def quote_payload(date: str, phase: str, price: float) -> dict:
    times = {
        "morning_entry": "10:00:00",
        "afternoon_risk": "14:30:00",
        "evening_watch": "20:00:00",
    }
    return {
        "generated_at": f"{date}T{times[phase]}+08:00",
        "as_of_date": date,
        "update_phase": phase,
        "stocks": [],
        "review": {
            "records": [
                {
                    "code": "601899",
                    "name": "紫金矿业",
                    "live_quote_price": price,
                    "close": price,
                }
            ]
        },
    }


class ExitFeedbackTests(unittest.TestCase):
    def test_existing_sell_trade_is_migrated_without_reset(self) -> None:
        state = trade_state()

        refresh_exit_feedback(state)
        refresh_exit_feedback(state)

        self.assertEqual(len(state["exitReviews"]), 1)
        review = state["exitReviews"][0]
        self.assertEqual(review["tradeId"], "sell-1")
        self.assertEqual(review["reasonKey"], "score_exit")
        self.assertEqual(review["entryPrice"], 28.5)
        self.assertEqual(review["exitPrice"], 29.0)

    def test_unique_trading_days_and_evening_finalization(self) -> None:
        state = trade_state()
        refresh_exit_feedback(state, quote_payload("2026-07-15", "evening_watch", 29.1))
        self.assertEqual(state["exitReviews"][0]["observedTradingDays"], 0)

        refresh_exit_feedback(state, quote_payload("2026-07-16", "morning_entry", 28.8))
        refresh_exit_feedback(state, quote_payload("2026-07-16", "afternoon_risk", 28.6))
        refresh_exit_feedback(state, quote_payload("2026-07-16", "evening_watch", 28.7))
        refresh_exit_feedback(state, quote_payload("2026-07-17", "morning_entry", 28.5))
        refresh_exit_feedback(state, quote_payload("2026-07-20", "morning_entry", 28.2))

        review = state["exitReviews"][0]
        self.assertEqual(review["observedTradingDays"], 3)
        self.assertIsNone(review["milestones"]["3"])

        refresh_exit_feedback(state, quote_payload("2026-07-20", "evening_watch", 28.0))
        review = state["exitReviews"][0]
        self.assertEqual(review["observedTradingDays"], 3)
        self.assertEqual(review["milestones"]["3"]["date"], "2026-07-20")
        self.assertAlmostEqual(review["milestones"]["3"]["returnPct"], -3.448, places=3)
        self.assertTrue(review["feedbackEligible"])

    def test_parameter_adjustment_requires_three_same_reason_samples(self) -> None:
        state = {"trades": [], "exitReviews": []}
        for index in range(3):
            state["exitReviews"].append(
                {
                    "id": f"sell-{index}",
                    "tradeId": f"sell-{index}",
                    "reasonKey": "score_exit",
                    "reasonLabel": "评分退出",
                    "feedbackEligible": True,
                    "milestones": {"3": None, "5": {"returnPct": 5.0}, "20": None, "30": None},
                }
            )

        two_sample_feedback = build_exit_feedback({**state, "exitReviews": state["exitReviews"][:2]})
        self.assertFalse(two_sample_feedback["parameterAdjustments"]["applied"])
        self.assertEqual(two_sample_feedback["effectiveSettings"]["exitScoreThreshold"], BASE_EXIT_SETTINGS["exitScoreThreshold"])

        feedback = build_exit_feedback(state)
        self.assertTrue(feedback["parameterAdjustments"]["applied"])
        self.assertEqual(feedback["effectiveSettings"]["exitScoreThreshold"], 6.0)


if __name__ == "__main__":
    unittest.main()
