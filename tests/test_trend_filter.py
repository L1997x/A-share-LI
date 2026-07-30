from __future__ import annotations

import unittest

import pandas as pd

from scripts.generate_pool import calculate_trend_metrics


class TrendFilterTests(unittest.TestCase):
    def test_rising_multi_average_structure_is_trade_eligible(self) -> None:
        close = pd.Series([50 + index * 0.5 for index in range(100)])
        trend = calculate_trend_metrics(close, latest_close=100.0)

        self.assertEqual(trend["trend_key"], "uptrend")
        self.assertTrue(trend["trend_trade_eligible"])
        self.assertGreater(trend["ma20_slope_5d_pct"], 0)
        self.assertGreater(trend["ma60_slope_10d_pct"], 0)

    def test_falling_structure_is_not_trade_eligible(self) -> None:
        close = pd.Series([100 - index * 0.5 for index in range(100)])
        trend = calculate_trend_metrics(close, latest_close=50.0)

        self.assertEqual(trend["trend_key"], "downtrend")
        self.assertFalse(trend["trend_trade_eligible"])
        self.assertLess(trend["ma20_slope_5d_pct"], 0)

    def test_rebound_below_long_average_remains_observation_only(self) -> None:
        close = pd.Series([100 - index * 0.6 for index in range(70)] + [58 + index * 0.45 for index in range(30)])
        trend = calculate_trend_metrics(close, latest_close=72.0)

        self.assertEqual(trend["trend_key"], "recovering")
        self.assertFalse(trend["trend_trade_eligible"])
        self.assertGreater(trend["ma20_slope_5d_pct"], 0)
