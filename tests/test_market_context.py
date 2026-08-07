from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

import pandas as pd

from scripts.generate_pool import build_market_fund_heat, parse_sina_global_context


CN_TZ = timezone(timedelta(hours=8))


class MarketFundHeatTests(unittest.TestCase):
    def test_broad_inflows_produce_warm_fund_heat(self) -> None:
        frame = pd.DataFrame(
            {
                "turnover": [2.8, 3.1, 2.5, 3.6, 2.9, 3.3],
                "amount": [1.0e9, 1.2e9, 0.8e9, 1.1e9, 0.9e9, 1.0e9],
                "fund_today_main_net_pct": [3.2, 2.1, 1.5, 4.0, 0.8, 2.7],
                "fund_5d_main_net_pct": [2.0, 1.2, 0.5, 3.1, 0.4, 1.6],
                "fund_today_main_net": [1e8, 8e7, 5e7, 1.2e8, 3e7, 7e7],
            }
        )
        heat = build_market_fund_heat(frame)

        self.assertTrue(heat["available"])
        self.assertIn(heat["regime"], {"warm", "hot"})
        self.assertGreater(heat["heat_score"], 9)
        self.assertEqual(heat["coverage_count"], 6)

    def test_missing_fund_flow_is_neutral_not_negative(self) -> None:
        frame = pd.DataFrame({"turnover": [1.5, 1.6], "amount": [1e8, 1e8]})
        heat = build_market_fund_heat(frame)

        self.assertTrue(heat["available"])
        self.assertGreater(heat["heat_score"], -9)
        self.assertEqual(heat["coverage_count"], 0)

    def test_low_coverage_ranked_sample_cannot_fake_warm_market(self) -> None:
        frame = pd.DataFrame(
            {
                "amount": [1e8] * 10,
                "fund_today_main_net_pct": [5.0, 4.0] + [None] * 8,
                "fund_5d_main_net_pct": [3.0, 2.0] + [None] * 8,
                "fund_today_main_net": [8e7, 6e7] + [None] * 8,
            }
        )
        heat = build_market_fund_heat(frame)

        self.assertTrue(heat["coverage_bias_guard"])
        self.assertEqual(heat["regime"], "neutral")
        self.assertLessEqual(heat["heat_score"], 8.0)
        self.assertEqual(heat["direction_reliability"], 0.0)


class GlobalMarketContextTests(unittest.TestCase):
    def test_parses_global_indices_and_futures(self) -> None:
        text = "\n".join(
            [
                'var hq_str_hf_CHA50CFD="100,0,0,0,0,0,23:10:00,99,99,0,0,0,2026-08-06,富时中国A50期货,0";',
                'var hq_str_b_HSI="香港恒生指数,25000,-250,-1.00,0,16:09:00,2026-08-06";',
                'var hq_str_b_NKY="日经225指数,40000,-200,-0.50,0,14:12:00,2026-08-06";',
                'var hq_str_gb_ixic="纳斯达克,20000,-1.20,2026-08-06 23:10:00";',
                'var hq_str_gb_inx="标普500指数,6000,-0.80,2026-08-06 23:10:00";',
                'var hq_str_gb_$dji="道琼斯,45000,-0.50,2026-08-06 23:10:00";',
                'var hq_str_hf_NQ="20000,0,0,0,0,0,23:10:00,20100,20200,0,0,0,2026-08-06,纳指期货,0";',
                'var hq_str_hf_ES="6000,0,0,0,0,0,23:10:00,6030,6060,0,0,0,2026-08-06,标普期货,0";',
                'var hq_str_fx_susdcny="23:10:00,7.2,0,0,0,0,0,0,0,在岸人民币,0.50,0,0,source,0,0,,2026-08-06";',
            ]
        )
        context = parse_sina_global_context(text, datetime(2026, 8, 6, 23, 10, tzinfo=CN_TZ))

        self.assertTrue(context["available"])
        self.assertEqual(context["session"], "欧美交易中")
        self.assertLess(context["impact_score"], 0)
        self.assertGreaterEqual(len(context["instruments"]), 8)

    def test_extreme_broad_decline_sets_hard_risk(self) -> None:
        text = "\n".join(
            [
                'var hq_str_hf_CHA50CFD="95,0,0,0,0,0,23:10:00,100,100,0,0,0,2026-08-06,A50,0";',
                'var hq_str_b_HSI="恒生,95,-5,-5.00,0,16:09:00,2026-08-06";',
                'var hq_str_gb_ixic="纳斯达克,95,-5.00,2026-08-06 23:10:00";',
                'var hq_str_gb_inx="标普500,96,-4.00,2026-08-06 23:10:00";',
                'var hq_str_hf_NQ="95,0,0,0,0,0,23:10:00,100,100,0,0,0,2026-08-06,NQ,0";',
            ]
        )
        context = parse_sina_global_context(text, datetime(2026, 8, 6, 23, 10, tzinfo=CN_TZ))

        self.assertTrue(context["hard_risk"])
        self.assertEqual(context["regime"], "defensive")


if __name__ == "__main__":
    unittest.main()
