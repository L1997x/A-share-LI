from __future__ import annotations

from copy import deepcopy
from statistics import median
from typing import Any


EXIT_HORIZONS = (3, 5, 20, 30)
EXIT_FEEDBACK_MIN_SAMPLES = 3
BASE_EXIT_SETTINGS = {
    "stopLossPct": 0.06,
    "takeProfitPct": 0.12,
    "trailingStopPct": 0.06,
    "exitScoreThreshold": 6.2,
    "maxHoldDays": 10,
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _price(row: dict[str, Any] | None) -> float | None:
    if not row:
        return None
    for key in ("live_quote_price", "close", "daily_close", "latest_price"):
        value = _num(row.get(key))
        if value is not None and value > 0:
            return value
    return None


def _payload_stock(payload: dict[str, Any], code: str) -> dict[str, Any] | None:
    for row in payload.get("stocks") or []:
        if str(row.get("code")) == code:
            return row
    for row in (payload.get("review") or {}).get("records") or []:
        if str(row.get("code")) == code:
            return row
    return None


def _phase(payload: dict[str, Any]) -> str:
    phase = str(payload.get("update_phase") or (payload.get("universe_scan") or {}).get("update_phase") or "")
    if phase:
        return phase
    label = str(payload.get("update_phase_label") or "")
    return "evening_watch" if "20点" in label or "次日" in label else ""


def _reason_key(reason: Any) -> str:
    text = str(reason or "")
    if "硬止损" in text:
        return "hard_stop"
    if "移动止盈" in text:
        return "trailing_stop"
    if "止盈" in text:
        return "take_profit"
    if "评分" in text:
        return "score_exit"
    if "状态" in text or "不追高" in text or "退出" in text:
        return "model_exit"
    if "持仓达到" in text or "持有" in text:
        return "max_hold"
    return "other"


def _reason_label(reason_key: str) -> str:
    return {
        "hard_stop": "硬止损",
        "trailing_stop": "移动止盈",
        "take_profit": "目标止盈",
        "score_exit": "评分退出",
        "model_exit": "模型状态退出",
        "max_hold": "持有期退出",
        "other": "其他退出",
    }.get(reason_key, "其他退出")


def _review_id(trade: dict[str, Any]) -> str:
    return str(trade.get("id") or f"sell-{trade.get('code')}-{trade.get('at')}")


def _latest_entry_trade(trades: list[dict[str, Any]], sell: dict[str, Any]) -> dict[str, Any] | None:
    code = str(sell.get("code") or "")
    exit_at = str(sell.get("at") or "")
    candidates = [
        trade
        for trade in trades
        if trade.get("type") == "buy"
        and str(trade.get("code") or "") == code
        and (not exit_at or str(trade.get("at") or "") <= exit_at)
    ]
    return max(candidates, key=lambda trade: str(trade.get("at") or ""), default=None)


def ensure_exit_reviews(state: dict[str, Any]) -> list[dict[str, Any]]:
    reviews = state.get("exitReviews")
    if not isinstance(reviews, list):
        reviews = []
    existing = {str(review.get("tradeId") or review.get("id")): review for review in reviews if isinstance(review, dict)}
    trades = [trade for trade in state.get("trades") or [] if isinstance(trade, dict)]

    for trade in trades:
        if trade.get("type") != "sell":
            continue
        trade_id = _review_id(trade)
        if trade_id in existing:
            continue
        entry = _latest_entry_trade(trades, trade) or {}
        exit_price = _num(trade.get("price"))
        entry_price = _num(entry.get("price"))
        quantity = int(_num(trade.get("quantity")) or 0)
        realized = _num(trade.get("realizedPnl"))
        entry_amount = entry_price * quantity if entry_price and quantity else None
        reason_key = _reason_key(trade.get("reason"))
        review = {
            "id": trade_id,
            "tradeId": trade_id,
            "code": str(trade.get("code") or ""),
            "name": trade.get("name") or trade.get("code") or "-",
            "exitAt": str(trade.get("at") or ""),
            "exitDate": str(trade.get("tradeDate") or trade.get("at") or "")[:10],
            "exitPrice": _round(exit_price, 2),
            "quantity": quantity,
            "reason": str(trade.get("reason") or ""),
            "reasonKey": reason_key,
            "reasonLabel": _reason_label(reason_key),
            "realizedPnl": _round(realized, 2),
            "realizedReturnPct": _round(realized / entry_amount * 100, 3) if realized is not None and entry_amount else None,
            "entryAt": str(entry.get("at") or ""),
            "entryPrice": _round(entry_price, 2),
            "entryReason": str(entry.get("reason") or ""),
            "dailyPrices": {},
            "milestones": {str(horizon): None for horizon in EXIT_HORIZONS},
            "observedTradingDays": 0,
            "currentDate": None,
            "currentPrice": None,
            "currentReturnPct": None,
            "maxPostExitReturnPct": None,
            "minPostExitReturnPct": None,
            "latestMatureHorizon": None,
            "qualityLabel": "等待3个交易日样本",
            "modelAction": "样本尚未成熟，不调整卖出参数。",
            "feedbackEligible": False,
        }
        reviews.append(review)
        existing[trade_id] = review

    reviews.sort(key=lambda review: str(review.get("exitAt") or ""), reverse=True)
    state["exitReviews"] = reviews[:100]
    return state["exitReviews"]


def _return_from_exit(review: dict[str, Any], price: Any) -> float | None:
    exit_price = _num(review.get("exitPrice"))
    current = _num(price)
    if not exit_price or not current:
        return None
    ratio = current / exit_price
    if ratio < 0.2 or ratio > 5:
        return None
    return (ratio - 1) * 100


def _quality(return_pct: float | None) -> tuple[str, str]:
    if return_pct is None:
        return "等待3个交易日样本", "样本尚未成熟，不调整卖出参数。"
    if return_pct <= -5:
        return "及时避险", "卖出后继续明显下跌，同类退出规则获得正反馈。"
    if return_pct <= 1:
        return "卖出合理", "卖出后未出现明显机会损失，保持当前退出纪律。"
    if return_pct <= 3:
        return "轻微卖早", "存在小幅机会成本，继续积累样本，暂不调参。"
    if return_pct <= 8:
        return "偏早卖出", "同类退出可能偏敏感，成熟样本足够后降低卖出敏感度。"
    return "明显卖早", "卖出后继续大幅上涨，成熟样本足够后放宽对应退出条件。"


def update_exit_reviews(state: dict[str, Any], payload: dict[str, Any]) -> None:
    reviews = ensure_exit_reviews(state)
    current_date = str(payload.get("as_of_date") or "")[:10]
    generated_at = str(payload.get("generated_at") or "")
    is_evening = _phase(payload) == "evening_watch"
    if not current_date:
        return

    for review in reviews:
        exit_date = str(review.get("exitDate") or "")[:10]
        if not exit_date or current_date <= exit_date:
            continue
        row = _payload_stock(payload, str(review.get("code") or ""))
        current_price = _price(row)
        current_return = _return_from_exit(review, current_price)
        daily_prices = review.get("dailyPrices")
        if not isinstance(daily_prices, dict):
            daily_prices = {}
            review["dailyPrices"] = daily_prices

        for observation_date, observation in daily_prices.items():
            if observation_date < current_date and isinstance(observation, dict):
                observation["finalized"] = True

        if current_price is not None and current_return is not None:
            daily_prices[current_date] = {
                "date": current_date,
                "at": generated_at,
                "price": _round(current_price, 2),
                "returnPct": _round(current_return, 3),
                "phase": _phase(payload),
                "finalized": is_evening,
            }

        observations = [daily_prices[key] for key in sorted(daily_prices) if isinstance(daily_prices[key], dict)]
        finalized = [observation for observation in observations if observation.get("finalized")]
        milestones = review.get("milestones") if isinstance(review.get("milestones"), dict) else {}
        for horizon in EXIT_HORIZONS:
            if len(finalized) >= horizon:
                observation = finalized[horizon - 1]
                milestones[str(horizon)] = {
                    "horizon": horizon,
                    "date": observation.get("date"),
                    "at": observation.get("at"),
                    "price": observation.get("price"),
                    "returnPct": observation.get("returnPct"),
                }
            else:
                milestones.setdefault(str(horizon), None)
        review["milestones"] = milestones
        review["observedTradingDays"] = len(observations)
        if observations:
            latest = observations[-1]
            returns = [_num(item.get("returnPct")) for item in observations]
            valid_returns = [value for value in returns if value is not None]
            review["currentDate"] = latest.get("date")
            review["currentPrice"] = latest.get("price")
            review["currentReturnPct"] = latest.get("returnPct")
            review["maxPostExitReturnPct"] = _round(max(valid_returns), 3) if valid_returns else None
            review["minPostExitReturnPct"] = _round(min(valid_returns), 3) if valid_returns else None

        mature_horizons = [horizon for horizon in EXIT_HORIZONS if isinstance(milestones.get(str(horizon)), dict)]
        latest_horizon = max(mature_horizons, default=None)
        latest_return = _num(milestones[str(latest_horizon)].get("returnPct")) if latest_horizon else None
        quality_label, model_action = _quality(latest_return)
        review["latestMatureHorizon"] = latest_horizon
        review["qualityLabel"] = quality_label
        review["modelAction"] = model_action
        review["feedbackEligible"] = latest_horizon is not None


def _stats(records: list[dict[str, Any]], horizon: int) -> dict[str, Any]:
    values = []
    for record in records:
        milestone = (record.get("milestones") or {}).get(str(horizon))
        value = _num(milestone.get("returnPct")) if isinstance(milestone, dict) else None
        if value is not None:
            values.append(value)
    if not values:
        return {
            "horizon": horizon,
            "sampleCount": 0,
            "avgPostExitReturnPct": None,
            "medianPostExitReturnPct": None,
            "prematureExitRatePct": None,
            "avoidedLossRatePct": None,
        }
    return {
        "horizon": horizon,
        "sampleCount": len(values),
        "avgPostExitReturnPct": _round(sum(values) / len(values), 3),
        "medianPostExitReturnPct": _round(median(values), 3),
        "prematureExitRatePct": _round(sum(value >= 3 for value in values) / len(values) * 100, 1),
        "avoidedLossRatePct": _round(sum(value <= -3 for value in values) / len(values) * 100, 1),
    }


def _effective_settings(adjustments: dict[str, Any]) -> dict[str, Any]:
    return {
        "stopLossPct": _round(_clamp(BASE_EXIT_SETTINGS["stopLossPct"] + adjustments["stopLossPctDelta"], 0.04, 0.10), 4),
        "takeProfitPct": _round(_clamp(BASE_EXIT_SETTINGS["takeProfitPct"] + adjustments["takeProfitPctDelta"], 0.08, 0.20), 4),
        "trailingStopPct": _round(_clamp(BASE_EXIT_SETTINGS["trailingStopPct"] + adjustments["trailingStopPctDelta"], 0.04, 0.10), 4),
        "exitScoreThreshold": _round(_clamp(BASE_EXIT_SETTINGS["exitScoreThreshold"] + adjustments["exitScoreThresholdDelta"], 5.6, 6.8), 2),
        "maxHoldDays": int(_clamp(BASE_EXIT_SETTINGS["maxHoldDays"] + adjustments["maxHoldDaysDelta"], 7, 20)),
    }


def build_exit_feedback(state: dict[str, Any]) -> dict[str, Any]:
    reviews = ensure_exit_reviews(state)
    horizon_stats = [_stats(reviews, horizon) for horizon in EXIT_HORIZONS]
    reason_stats = []
    for reason_key in ("hard_stop", "take_profit", "trailing_stop", "score_exit", "model_exit", "max_hold", "other"):
        reason_records = [review for review in reviews if review.get("reasonKey") == reason_key]
        if not reason_records:
            continue
        reason_stats.append(
            {
                "reasonKey": reason_key,
                "reasonLabel": _reason_label(reason_key),
                "exitCount": len(reason_records),
                "horizons": [_stats(reason_records, horizon) for horizon in EXIT_HORIZONS],
            }
        )

    adjustments = {
        "stopLossPctDelta": 0.0,
        "takeProfitPctDelta": 0.0,
        "trailingStopPctDelta": 0.0,
        "exitScoreThresholdDelta": 0.0,
        "maxHoldDaysDelta": 0,
        "applied": False,
        "notes": [],
    }

    stats_by_reason = {item["reasonKey"]: {row["horizon"]: row for row in item["horizons"]} for item in reason_stats}

    def mature(reason: str, horizon: int) -> dict[str, Any] | None:
        stat = (stats_by_reason.get(reason) or {}).get(horizon)
        return stat if stat and int(stat.get("sampleCount") or 0) >= EXIT_FEEDBACK_MIN_SAMPLES else None

    score_stat = mature("score_exit", 5)
    if score_stat:
        average = _num(score_stat.get("avgPostExitReturnPct")) or 0.0
        if average >= 3:
            adjustments["exitScoreThresholdDelta"] = -0.2
            adjustments["notes"].append("评分退出后5日平均上涨，退出阈值下调0.2并减少过早卖出。")
        elif average <= -3:
            adjustments["exitScoreThresholdDelta"] = 0.1
            adjustments["notes"].append("评分退出后5日继续下跌，退出阈值上调0.1以更早控制风险。")

    stop_stat = mature("hard_stop", 3)
    if stop_stat:
        average = _num(stop_stat.get("avgPostExitReturnPct")) or 0.0
        if average >= 4:
            adjustments["stopLossPctDelta"] = 0.005
            adjustments["notes"].append("硬止损后3日反弹偏多，未来新仓止损放宽0.5个百分点。")
        elif average <= -4:
            adjustments["stopLossPctDelta"] = -0.003
            adjustments["notes"].append("硬止损后3日继续下跌，未来新仓止损收紧0.3个百分点。")

    trailing_stat = mature("trailing_stop", 5)
    if trailing_stat:
        average = _num(trailing_stat.get("avgPostExitReturnPct")) or 0.0
        if average >= 4:
            adjustments["trailingStopPctDelta"] = 0.005
            adjustments["notes"].append("移动止盈后5日上涨偏多，回撤容忍放宽0.5个百分点。")
        elif average <= -4:
            adjustments["trailingStopPctDelta"] = -0.003
            adjustments["notes"].append("移动止盈后5日继续下跌，回撤容忍收紧0.3个百分点。")

    take_stat = mature("take_profit", 5)
    if take_stat and (_num(take_stat.get("avgPostExitReturnPct")) or 0.0) >= 5:
        adjustments["takeProfitPctDelta"] = 0.01
        adjustments["notes"].append("目标止盈后5日仍明显上涨，未来新仓止盈目标提高1个百分点。")

    hold_stat = mature("max_hold", 20)
    if hold_stat:
        average = _num(hold_stat.get("avgPostExitReturnPct")) or 0.0
        if average >= 5:
            adjustments["maxHoldDaysDelta"] = 2
            adjustments["notes"].append("持有期退出后20日上涨偏多，最长持有期增加2个交易日。")
        elif average <= -3:
            adjustments["maxHoldDaysDelta"] = -1
            adjustments["notes"].append("持有期退出后20日继续走弱，最长持有期减少1个交易日。")

    adjustments["applied"] = bool(adjustments["notes"])
    effective = _effective_settings(adjustments)
    mature_count = sum(bool(review.get("feedbackEligible")) for review in reviews)
    confidence = "高" if mature_count >= 8 else "中" if mature_count >= EXIT_FEEDBACK_MIN_SAMPLES else "低"
    if adjustments["applied"]:
        action = "；".join(adjustments["notes"])
    elif mature_count:
        action = "已有成熟卖出样本，但同类样本未达到3笔或偏差未越过阈值，暂不调参。"
    else:
        action = "等待首批卖出样本达到3个交易日，当前退出参数保持不变。"
    return {
        "schemaVersion": 1,
        "method": "按每笔模拟卖出的成交价，追踪卖出后3/5/20/30个交易日收盘收益；同一交易日只计一次，成熟同类样本至少3笔后才小幅调整对应退出参数。",
        "horizons": list(EXIT_HORIZONS),
        "exitCount": len(reviews),
        "maturedExitCount": mature_count,
        "minSamplesForAdjustment": EXIT_FEEDBACK_MIN_SAMPLES,
        "confidence": confidence,
        "horizonStats": horizon_stats,
        "reasonStats": reason_stats,
        "parameterAdjustments": adjustments,
        "effectiveSettings": effective,
        "modelAction": action,
    }


def refresh_exit_feedback(state: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_exit_reviews(state)
    if isinstance(payload, dict):
        update_exit_reviews(state, payload)
    feedback = build_exit_feedback(state)
    state["exitFeedback"] = feedback
    state["effectiveExitSettings"] = deepcopy(feedback["effectiveSettings"])
    return feedback
