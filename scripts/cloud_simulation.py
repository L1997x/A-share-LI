from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Iterable

try:
    from .exit_feedback import BASE_EXIT_SETTINGS, refresh_exit_feedback
except ImportError:
    from exit_feedback import BASE_EXIT_SETTINGS, refresh_exit_feedback

INITIAL_CASH = 100_000.0
STATE_SCHEMA_VERSION = 6
MAX_POSITION_PCT = 0.20
TRIAL_POSITION_PCT = 0.10
MAX_POSITIONS = 5
MAX_BUYS_PER_RUN = 2
MAX_AFTERNOON_BUYS = 1
MIN_SCORE = 7.5
TRIAL_MIN_SCORE = 8.8
TRIAL_ATR_BUFFER = 1.5
TRIAL_MAX_MA20_PREMIUM = 0.05
PLAN_VALID_DAYS = 3
STOP_LOSS_PCT = 0.06
TAKE_PROFIT_PCT = 0.12
TRAILING_STOP_PCT = 0.06
MAX_HOLD_DAYS = 10
BUY_SLIPPAGE_PCT = 0.002
SELL_SLIPPAGE_PCT = 0.002

AUTO_SETTINGS = {
    "enabled": True,
    "maxPositionPct": MAX_POSITION_PCT,
    "maxStocks": MAX_POSITIONS,
    "minScore": MIN_SCORE,
    "maxBuysPerRun": MAX_BUYS_PER_RUN,
    "afternoonMaxBuysPerRun": MAX_AFTERNOON_BUYS,
    "afternoonMaxPositionPct": TRIAL_POSITION_PCT,
    "afternoonMinScoreBuffer": 0.3,
    "afternoonMinRiskAppetite": 0.45,
    "afternoonMinFundFlowScore": 0.0,
    "stopLossPct": STOP_LOSS_PCT,
    "takeProfitPct": TAKE_PROFIT_PCT,
    "trailingStopPct": TRAILING_STOP_PCT,
    "morningSlippagePct": BUY_SLIPPAGE_PCT,
    "sellSlippagePct": SELL_SLIPPAGE_PCT,
    "buyPlanValidDays": PLAN_VALID_DAYS,
    "reduceScoreThreshold": 6.8,
    "exitScoreThreshold": 6.2,
    "maxHoldDays": MAX_HOLD_DAYS,
}

FEE_SETTINGS = {
    "commissionRate": 0.0003,
    "minCommission": 5.0,
    "stampDutyRate": 0.0005,
    "transferFeeRate": 0.00001,
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _first_price(*values: Any) -> float | None:
    for value in values:
        number = _num(value)
        if number is not None and number > 0:
            return number
    return None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _phase(payload: dict[str, Any]) -> str:
    phase = str(payload.get("update_phase") or (payload.get("universe_scan") or {}).get("update_phase") or "")
    if phase in {"morning_entry", "afternoon_risk", "evening_watch"}:
        return phase
    label = str(payload.get("update_phase_label") or (payload.get("universe_scan") or {}).get("update_phase_label") or "")
    if "20点" in label or "次日" in label:
        return "evening_watch"
    if "14:30" in label or "尾盘" in label:
        return "afternoon_risk"
    return "morning_entry"


def _phase_label(payload: dict[str, Any]) -> str:
    return str(payload.get("update_phase_label") or (payload.get("universe_scan") or {}).get("update_phase_label") or _phase(payload))


def _run_key(payload: dict[str, Any]) -> str:
    return "|".join(
        str(value)
        for value in (payload.get("generated_at"), payload.get("as_of_date"), _phase_label(payload))
        if value
    )


def default_simulation() -> dict[str, Any]:
    return {
        "schemaVersion": STATE_SCHEMA_VERSION,
        "cloudManaged": True,
        "initialCash": INITIAL_CASH,
        "cash": INITIAL_CASH,
        "positions": {},
        "trades": [],
        "selectedCode": "",
        "autoSettings": deepcopy(AUTO_SETTINGS),
        "feeSettings": deepcopy(FEE_SETTINGS),
        "lastAutoRunKey": "",
        "autoLog": [],
        "pendingBuyOrders": [],
        "sellPlans": {},
        "decisionJournal": [],
        "exitReviews": [],
        "exitFeedback": {},
        "effectiveExitSettings": deepcopy(BASE_EXIT_SETTINGS),
        "diagnostics": {
            "snapshotsProcessed": 0,
            "plansCreated": 0,
            "buyChecks": 0,
            "buysExecuted": 0,
            "sellsExecuted": 0,
            "waitReasonCounts": {},
            "cancelReasonCounts": {},
            "lastRunAt": "",
            "lastRunSummary": "尚未执行",
        },
    }


def sanitize_simulation(raw: dict[str, Any] | None) -> dict[str, Any]:
    state = default_simulation()
    if not isinstance(raw, dict):
        return state
    for key in (
        "cash",
        "positions",
        "trades",
        "lastAutoRunKey",
        "autoLog",
        "pendingBuyOrders",
        "sellPlans",
        "decisionJournal",
        "exitReviews",
        "exitFeedback",
        "effectiveExitSettings",
        "diagnostics",
    ):
        if key in raw:
            state[key] = deepcopy(raw[key])
    state["schemaVersion"] = STATE_SCHEMA_VERSION
    state["cloudManaged"] = True
    state["initialCash"] = INITIAL_CASH
    state["autoSettings"] = deepcopy(AUTO_SETTINGS)
    state["feeSettings"] = deepcopy(FEE_SETTINGS)
    state["cash"] = max(0.0, _num(state.get("cash")) or 0.0)
    for key, fallback in (("positions", {}), ("sellPlans", {}), ("diagnostics", {})):
        if not isinstance(state.get(key), dict):
            state[key] = deepcopy(fallback)
    for key in ("trades", "autoLog", "pendingBuyOrders", "decisionJournal", "exitReviews"):
        if not isinstance(state.get(key), list):
            state[key] = []
    diagnostics = default_simulation()["diagnostics"]
    diagnostics.update(state.get("diagnostics") or {})
    diagnostics["waitReasonCounts"] = dict(diagnostics.get("waitReasonCounts") or {})
    diagnostics["cancelReasonCounts"] = dict(diagnostics.get("cancelReasonCounts") or {})
    state["diagnostics"] = diagnostics
    refresh_exit_feedback(state)
    return state


def _fees(side: str, gross: float) -> dict[str, float]:
    commission = max(gross * FEE_SETTINGS["commissionRate"], FEE_SETTINGS["minCommission"])
    stamp = gross * FEE_SETTINGS["stampDutyRate"] if side == "sell" else 0.0
    transfer = gross * FEE_SETTINGS["transferFeeRate"]
    return {
        "commission": round(commission, 4),
        "stampDuty": round(stamp, 4),
        "transferFee": round(transfer, 4),
        "total": round(commission + stamp + transfer, 4),
    }


def _stock_price(stock: dict[str, Any] | None) -> float | None:
    if not stock:
        return None
    return _first_price(stock.get("live_quote_price"), stock.get("close"), stock.get("daily_close"), stock.get("latest_price"))


def _stock_maps(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    stocks = {str(row.get("code")): row for row in payload.get("stocks") or [] if row.get("code")}
    review = payload.get("review") or {}
    reviews = {str(row.get("code")): row for row in review.get("records") or [] if row.get("code")}
    return stocks, reviews


def _market(payload: dict[str, Any]) -> dict[str, Any]:
    return (payload.get("universe_scan") or {}).get("market_environment") or {}


def _actionable(stock: dict[str, Any]) -> bool:
    return bool(stock.get("is_buyable_now") or stock.get("buy_signal_key") in {"pullback_buy", "breakout_buy"})


def _hard_blocked(stock: dict[str, Any]) -> bool:
    return bool(
        stock.get("entry_safety_block_buy")
        or stock.get("market_context_block_buy")
        or stock.get("buy_signal_key") in {"risk_wait", "market_wait", "avoid"}
        or stock.get("status_key") == "avoid"
    )


def _trial_eligible(stock: dict[str, Any], market: dict[str, Any]) -> bool:
    regime = str(market.get("regime") or stock.get("market_regime") or "")
    risk_appetite = _num(market.get("risk_appetite")) or _num(stock.get("market_risk_appetite")) or 0.0
    fund_flow = _num(stock.get("fund_flow_score"))
    return bool(
        regime in {"strong", "warm"}
        and risk_appetite >= 0.75
        and (_num(stock.get("score")) or 0.0) >= TRIAL_MIN_SCORE
        and not _hard_blocked(stock)
        and (fund_flow is None or fund_flow >= 0.0)
    )


def _trial_ceiling(stock: dict[str, Any]) -> float | None:
    upper = _first_price(stock.get("entry_price_upper"), stock.get("recommended_entry_price"))
    if upper is None:
        return None
    atr = _num(stock.get("atr14")) or 0.0
    no_chase = _first_price(stock.get("no_chase_price"))
    ceiling = upper + TRIAL_ATR_BUFFER * max(0.0, atr)
    return min(ceiling, no_chase) if no_chase else ceiling


def _plan_for_stock(stock: dict[str, Any], payload: dict[str, Any], run_key: str) -> dict[str, Any] | None:
    market = _market(payload)
    actionable = _actionable(stock)
    trial = _trial_eligible(stock, market)
    planned = _first_price(
        stock.get("buyable_price") if actionable else None,
        stock.get("recommended_entry_price"),
        stock.get("buyable_price"),
        stock.get("close"),
    )
    if planned is None:
        return None
    no_chase = _first_price(stock.get("no_chase_price"))
    if actionable:
        candidates = [
            _first_price(stock.get("buyable_price_upper")),
            _first_price(stock.get("breakout_buy_upper_price")),
            no_chase,
            planned * 1.012,
        ]
        plan_type = "executable"
        target_pct = MAX_POSITION_PCT
        reason = f"明确可买信号：{stock.get('buy_signal_label') or stock.get('buy_signal_key')}"
    elif trial:
        candidates = [_trial_ceiling(stock), no_chase]
        plan_type = "trial"
        target_pct = TRIAL_POSITION_PCT
        reason = "偏暖市场高分候选，等待进入1.5个ATR波动缓冲区且通过止跌过滤后小仓试买"
    else:
        candidates = [_first_price(stock.get("entry_price_upper")), no_chase]
        plan_type = "watch"
        target_pct = TRIAL_POSITION_PCT
        reason = "观察计划，尚未升级为可执行买入信号"
    finite = [value for value in candidates if value is not None and value > 0]
    max_buy = min(finite) if finite else planned
    signal_date = str(payload.get("as_of_date") or "")
    parsed = _parse_date(signal_date) or date.today()
    return {
        "id": f"{stock.get('code')}-{run_key}",
        "code": str(stock.get("code")),
        "name": stock.get("name") or stock.get("code"),
        "status": "pending",
        "signalDate": signal_date,
        "signalPhase": _phase(payload),
        "createdRunKey": run_key,
        "plannedExecutionPhase": "morning_entry",
        "validUntilDate": (parsed + timedelta(days=PLAN_VALID_DAYS)).isoformat(),
        "plannedEntryPrice": _round(planned, 2),
        "maxBuyPrice": _round(max_buy, 2),
        "noChasePrice": _round(no_chase, 2),
        "score": _round(_num(stock.get("score")), 1),
        "buySignalKey": stock.get("buy_signal_key") or "",
        "statusKey": stock.get("status_key") or "",
        "entrySafetyBlockBuy": bool(stock.get("entry_safety_block_buy")),
        "planType": plan_type,
        "targetPositionPct": target_pct,
        "reason": reason,
        "cancelReason": "",
        "executedAt": "",
        "executionPrice": None,
    }


def _portfolio_value(state: dict[str, Any], stocks: dict[str, dict[str, Any]], reviews: dict[str, dict[str, Any]]) -> float:
    market_value = 0.0
    for code, position in state["positions"].items():
        price = _stock_price(stocks.get(code)) or _stock_price(reviews.get(code))
        if price:
            market_value += (_num(position.get("quantity")) or 0.0) * price
        else:
            market_value += _num(position.get("costBasis")) or 0.0
    return state["cash"] + market_value


def _affordable_quantity(state: dict[str, Any], price: float, target_pct: float, portfolio_value: float) -> int:
    gross_limit = min(state["cash"], portfolio_value * target_pct)
    quantity = int(gross_limit / price / 100) * 100
    while quantity >= 100:
        gross = quantity * price
        if gross + _fees("buy", gross)["total"] <= state["cash"] + 0.0001:
            return quantity
        quantity -= 100
    return 0


def _record(state: dict[str, Any], payload: dict[str, Any], record: dict[str, Any]) -> None:
    at = str(payload.get("generated_at") or datetime.now().isoformat(timespec="seconds"))
    state["decisionJournal"].insert(
        0,
        {
            "id": f"{record.get('type', 'record')}-{record.get('code', '')}-{at}",
            "at": at,
            "tradeDate": str(payload.get("as_of_date") or ""),
            "phase": _phase_label(payload),
            "phaseKey": _phase(payload),
            **record,
        },
    )
    state["decisionJournal"] = state["decisionJournal"][:100]


def _count_reason(state: dict[str, Any], kind: str, reason: str) -> None:
    key = "waitReasonCounts" if kind == "wait" else "cancelReasonCounts"
    counts = state["diagnostics"].setdefault(key, {})
    counts[reason] = int(counts.get(reason, 0)) + 1


def _create_plans(state: dict[str, Any], payload: dict[str, Any], events: list[dict[str, Any]]) -> None:
    stocks, reviews = _stock_maps(payload)
    run_key = _run_key(payload)
    open_slots = max(0, MAX_POSITIONS - len(state["positions"]))
    if not open_slots:
        events.append({"type": "buy_plan", "summary": "持仓数量已达上限，不生成新计划"})
        return
    candidates: list[tuple[int, float, dict[str, Any]]] = []
    for stock in stocks.values():
        code = str(stock.get("code"))
        if code in state["positions"] or (_num(stock.get("score")) or 0.0) < MIN_SCORE or _hard_blocked(stock):
            continue
        plan = _plan_for_stock(stock, payload, run_key)
        if not plan:
            continue
        priority = {"executable": 2, "trial": 1, "watch": 0}[plan["planType"]]
        candidates.append((priority, _num(stock.get("score")) or 0.0, plan))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    portfolio_value = _portfolio_value(state, stocks, reviews)
    selected: list[dict[str, Any]] = []
    for _, _, plan in candidates:
        price = _num(plan.get("maxBuyPrice")) or _num(plan.get("plannedEntryPrice")) or 0.0
        if _affordable_quantity(state, price, _num(plan.get("targetPositionPct")) or TRIAL_POSITION_PCT, portfolio_value) < 100:
            continue
        selected.append(plan)
        if len(selected) >= min(MAX_BUYS_PER_RUN, open_slots):
            break
    selected_codes = {plan["code"] for plan in selected}
    today = _parse_date(payload.get("as_of_date"))
    preserved = []
    for order in state["pendingBuyOrders"]:
        if order.get("status") != "pending" or order.get("code") in selected_codes:
            continue
        valid_until = _parse_date(order.get("validUntilDate"))
        if today and valid_until and today > valid_until:
            continue
        preserved.append(order)
    state["pendingBuyOrders"] = (selected + preserved + [order for order in state["pendingBuyOrders"] if order.get("status") != "pending"][:30])[:50]
    for order in selected:
        state["diagnostics"]["plansCreated"] = int(state["diagnostics"].get("plansCreated", 0)) + 1
        _record(
            state,
            payload,
            {
                "type": "buy_plan_created",
                "status": "pending",
                "code": order["code"],
                "name": order["name"],
                "summary": "生成可执行计划" if order["planType"] == "executable" else "生成小仓试买观察" if order["planType"] == "trial" else "生成观察计划",
                "reason": order["reason"],
                "plannedEntryPrice": order["plannedEntryPrice"],
                "maxBuyPrice": order["maxBuyPrice"],
                "noChasePrice": order["noChasePrice"],
                "validUntilDate": order["validUntilDate"],
                "score": order["score"],
            },
        )
    names = "、".join(f"{order['name']}({order['planType']})" for order in selected)
    events.append({"type": "buy_plan", "summary": f"更新云端计划{len(selected)}只：{names}" if selected else "当前没有符合资金和风控约束的新计划"})


def _stop_take_prices(stock: dict[str, Any], entry: float, state: dict[str, Any]) -> tuple[float, float]:
    policy = state.get("effectiveExitSettings") or BASE_EXIT_SETTINGS
    stop_loss_pct = _num(policy.get("stopLossPct")) or STOP_LOSS_PCT
    take_profit_pct = _num(policy.get("takeProfitPct")) or TAKE_PROFIT_PCT
    fallback_stop = entry * (1 - stop_loss_pct)
    invalid = _first_price(stock.get("invalid_price"))
    stop = max(fallback_stop, invalid) if invalid and invalid < entry else fallback_stop
    fallback_take = entry * (1 + take_profit_pct)
    resistance = _first_price(stock.get("resistance_price"))
    take = min(fallback_take, resistance) if resistance and resistance > entry else fallback_take
    return round(stop, 2), round(take, 2)


def _buy(state: dict[str, Any], payload: dict[str, Any], stock: dict[str, Any], order: dict[str, Any], price: float, quantity: int, reason: str) -> None:
    gross = price * quantity
    fees = _fees("buy", gross)
    total = gross + fees["total"]
    stop, take = _stop_take_prices(stock, price, state)
    code = str(stock.get("code"))
    lot = {
        "quantity": quantity,
        "price": round(price, 2),
        "grossAmount": round(gross, 2),
        "fees": fees["total"],
        "costBasis": round(total, 2),
        "tradeDate": str(payload.get("as_of_date") or ""),
        "at": str(payload.get("generated_at") or ""),
        "stopLossPrice": stop,
        "takeProfitPrice": take,
        "highestPrice": round(price, 2),
        "entryReason": reason,
        "source": "cloud_auto",
    }
    state["positions"][code] = {
        "code": code,
        "name": stock.get("name") or code,
        "quantity": quantity,
        "costBasis": round(total, 2),
        "lots": [lot],
        "stopLossPrice": stop,
        "takeProfitPrice": take,
        "highestPrice": round(price, 2),
        "entryReason": reason,
        "themeGroup": stock.get("portfolio_theme_group") or stock.get("theme_group") or "",
        "updatedAt": str(payload.get("generated_at") or ""),
    }
    state["cash"] = round(state["cash"] - total, 4)
    trade = {
        "id": f"buy-{code}-{payload.get('generated_at')}",
        "at": str(payload.get("generated_at") or ""),
        "tradeDate": str(payload.get("as_of_date") or ""),
        "type": "buy",
        "source": "cloud_auto",
        "reason": reason,
        "code": code,
        "name": stock.get("name") or code,
        "price": round(price, 2),
        "quantity": quantity,
        "amount": round(gross, 2),
        "grossAmount": round(gross, 2),
        "netAmount": round(total, 2),
        "fees": fees,
        "stopLossPrice": stop,
        "takeProfitPrice": take,
    }
    state["trades"].insert(0, trade)
    state["trades"] = state["trades"][:200]
    order["status"] = "executed"
    order["executedAt"] = _run_key(payload)
    order["executionPrice"] = round(price, 2)
    state["diagnostics"]["buysExecuted"] = int(state["diagnostics"].get("buysExecuted", 0)) + 1
    _record(
        state,
        payload,
        {
            "type": "buy_executed",
            "status": "executed",
            "code": code,
            "name": stock.get("name") or code,
            "summary": f"云端自动买入{quantity}股",
            "reason": reason,
            "plannedEntryPrice": order.get("plannedEntryPrice"),
            "maxBuyPrice": order.get("maxBuyPrice"),
            "snapshotPrice": _stock_price(stock),
            "executionPrice": round(price, 2),
            "stopLossPrice": stop,
            "takeProfitPrice": take,
            "quantity": quantity,
            "feeTotal": fees["total"],
            "score": order.get("score"),
        },
    )


def _execute_buys(state: dict[str, Any], payload: dict[str, Any], events: list[dict[str, Any]]) -> None:
    stocks, reviews = _stock_maps(payload)
    _create_plans(state, payload, events)
    today = _parse_date(payload.get("as_of_date"))
    market = _market(payload)
    phase = _phase(payload)
    max_buys = MAX_AFTERNOON_BUYS if phase == "afternoon_risk" else MAX_BUYS_PER_RUN
    executed = 0
    portfolio_value = _portfolio_value(state, stocks, reviews)
    for order in state["pendingBuyOrders"]:
        if order.get("status") != "pending" or order.get("code") in state["positions"]:
            continue
        valid_until = _parse_date(order.get("validUntilDate"))
        if today and valid_until and today > valid_until:
            order["status"] = "expired"
            order["cancelReason"] = "计划超过有效期"
            _count_reason(state, "cancel", "计划超过有效期")
            continue
        stock = stocks.get(str(order.get("code")))
        state["diagnostics"]["buyChecks"] = int(state["diagnostics"].get("buyChecks", 0)) + 1
        if not stock:
            reason = "股票不在最新池，云端继续观察但不执行"
            _count_reason(state, "wait", reason)
            continue
        if _hard_blocked(stock):
            reason = "模型或接入安全层硬拦截"
            order["status"] = "cancelled"
            order["cancelReason"] = reason
            _count_reason(state, "cancel", reason)
            continue
        actionable = _actionable(stock)
        trial = _trial_eligible(stock, market)
        if not actionable and not trial:
            reason = "观察计划尚未升级为可执行信号"
            _count_reason(state, "wait", reason)
            _record(state, payload, {"type": "buy_deferred", "status": "pending", "code": order["code"], "name": order["name"], "summary": "继续观察", "reason": reason, "plannedEntryPrice": order.get("plannedEntryPrice"), "maxBuyPrice": order.get("maxBuyPrice"), "score": order.get("score")})
            continue
        snapshot_price = _stock_price(stock)
        if snapshot_price is None:
            reason = "当前快照缺少有效价格"
            _count_reason(state, "wait", reason)
            continue
        invalid = _first_price(stock.get("invalid_price"))
        if invalid and snapshot_price <= invalid:
            reason = "价格跌破失效价，避免接飞刀"
            order["status"] = "cancelled"
            order["cancelReason"] = reason
            _count_reason(state, "cancel", reason)
            continue
        ma20 = _first_price(stock.get("ma20"))
        if ma20 and snapshot_price < ma20 * 0.97:
            reason = "价格低于MA20超过3%，等待止跌确认"
            _count_reason(state, "wait", reason)
            continue
        if trial and ma20 and snapshot_price > ma20 * (1 + TRIAL_MAX_MA20_PREMIUM):
            reason = "价格高于MA20超过5%，避免追涨后回撤"
            _count_reason(state, "wait", reason)
            continue
        max_buy = _num(order.get("maxBuyPrice"))
        if trial:
            trial_ceiling = _trial_ceiling(stock)
            max_buy = min(value for value in (max_buy, trial_ceiling) if value is not None) if max_buy and trial_ceiling else max_buy or trial_ceiling
        execution_price = snapshot_price * (1 + BUY_SLIPPAGE_PCT)
        if max_buy and execution_price > max_buy:
            reason = "含滑点价格高于最高买入价"
            _count_reason(state, "wait", reason)
            _record(state, payload, {"type": "buy_deferred", "status": "pending", "code": order["code"], "name": order["name"], "summary": "价格未触发", "reason": reason, "plannedEntryPrice": order.get("plannedEntryPrice"), "maxBuyPrice": _round(max_buy, 2), "snapshotPrice": _round(snapshot_price, 2), "score": order.get("score")})
            continue
        if executed >= max_buys:
            reason = "本次快照买入数量已达上限"
            _count_reason(state, "wait", reason)
            continue
        target_pct = MAX_POSITION_PCT if actionable else TRIAL_POSITION_PCT
        quantity = _affordable_quantity(state, execution_price, target_pct, portfolio_value)
        if quantity < 100:
            reason = "10万元资金与单票仓位限制下不足一手"
            order["status"] = "cancelled"
            order["cancelReason"] = reason
            _count_reason(state, "cancel", reason)
            continue
        reason = "明确可买信号验证通过" if actionable else "偏暖市场高分标的进入1.5个ATR缓冲区并通过止跌过滤，小仓试买"
        _buy(state, payload, stock, order, execution_price, quantity, reason)
        events.append({"type": "buy", "summary": f"云端买入{stock.get('name')}{quantity}股，成交{execution_price:.2f}"})
        executed += 1
    if not executed:
        events.append({"type": "buy_wait", "summary": "本次买入复检未成交，原因已写入决策复盘"})


def _available_quantity(position: dict[str, Any], today: str) -> int:
    return sum(int(_num(lot.get("quantity")) or 0) for lot in position.get("lots") or [] if str(lot.get("tradeDate")) != today)


def _hold_days(position: dict[str, Any], today: str) -> int:
    dates = sorted(_parse_date(lot.get("tradeDate")) for lot in position.get("lots") or [] if _parse_date(lot.get("tradeDate")))
    current = _parse_date(today)
    return max(0, (current - dates[0]).days) if dates and current else 0


def _sell(state: dict[str, Any], payload: dict[str, Any], stock: dict[str, Any], position: dict[str, Any], price: float, quantity: int, reason: str) -> None:
    today = str(payload.get("as_of_date") or "")
    remaining = quantity
    released = 0.0
    next_lots = []
    for lot in position.get("lots") or []:
        lot_qty = int(_num(lot.get("quantity")) or 0)
        if remaining <= 0 or str(lot.get("tradeDate")) == today:
            next_lots.append(lot)
            continue
        take = min(lot_qty, remaining)
        per_share = (_num(lot.get("costBasis")) or 0.0) / max(1, lot_qty)
        released += per_share * take
        remaining -= take
        if lot_qty > take:
            updated = deepcopy(lot)
            updated["quantity"] = lot_qty - take
            updated["costBasis"] = round((_num(lot.get("costBasis")) or 0.0) - per_share * take, 4)
            next_lots.append(updated)
    if remaining:
        return
    gross = price * quantity
    fees = _fees("sell", gross)
    net = gross - fees["total"]
    realized = net - released
    position["lots"] = next_lots
    position["quantity"] = int(_num(position.get("quantity")) or 0) - quantity
    position["costBasis"] = round(max(0.0, (_num(position.get("costBasis")) or 0.0) - released), 4)
    position["updatedAt"] = str(payload.get("generated_at") or "")
    code = str(position.get("code"))
    if position["quantity"] <= 0:
        state["positions"].pop(code, None)
        state["sellPlans"].pop(code, None)
    state["cash"] = round(state["cash"] + net, 4)
    state["trades"].insert(0, {
        "id": f"sell-{code}-{payload.get('generated_at')}", "at": str(payload.get("generated_at") or ""), "tradeDate": today,
        "type": "sell", "source": "cloud_auto", "reason": reason, "code": code, "name": position.get("name") or code,
        "price": round(price, 2), "quantity": quantity, "amount": round(gross, 2), "grossAmount": round(gross, 2),
        "netAmount": round(net, 2), "fees": fees, "realizedPnl": round(realized, 2),
    })
    state["trades"] = state["trades"][:200]
    state["diagnostics"]["sellsExecuted"] = int(state["diagnostics"].get("sellsExecuted", 0)) + 1
    _record(state, payload, {"type": "sell_executed", "status": "executed", "code": code, "name": position.get("name") or code, "summary": f"云端自动卖出{quantity}股", "reason": reason, "snapshotPrice": _stock_price(stock), "executionPrice": round(price, 2), "quantity": quantity, "feeTotal": fees["total"], "realizedPnl": round(realized, 2)})


def _execute_sells(state: dict[str, Any], payload: dict[str, Any], events: list[dict[str, Any]]) -> None:
    stocks, reviews = _stock_maps(payload)
    today = str(payload.get("as_of_date") or "")
    phase = _phase(payload)
    policy = state.get("effectiveExitSettings") or BASE_EXIT_SETTINGS
    trailing_stop_pct = _num(policy.get("trailingStopPct")) or TRAILING_STOP_PCT
    exit_score_threshold = _num(policy.get("exitScoreThreshold")) or BASE_EXIT_SETTINGS["exitScoreThreshold"]
    max_hold_days = int(_num(policy.get("maxHoldDays")) or MAX_HOLD_DAYS)
    for code, position in list(state["positions"].items()):
        stock = stocks.get(code) or reviews.get(code)
        price = _stock_price(stock)
        if not stock or price is None:
            continue
        position["highestPrice"] = max(_num(position.get("highestPrice")) or 0.0, price)
        for lot in position.get("lots") or []:
            lot["highestPrice"] = max(_num(lot.get("highestPrice")) or _num(lot.get("price")) or 0.0, price)
        available = _available_quantity(position, today)
        if available < 100:
            continue
        stop = _first_price(position.get("stopLossPrice"))
        take = _first_price(position.get("takeProfitPrice"))
        highest = _num(position.get("highestPrice")) or price
        avg_cost = (_num(position.get("costBasis")) or 0.0) / max(1, int(_num(position.get("quantity")) or 0))
        trailing = highest * (1 - trailing_stop_pct)
        reason = ""
        if stop and price <= stop:
            reason = f"触发硬止损{stop:.2f}"
        elif take and price >= take:
            reason = f"触发止盈{take:.2f}"
        elif highest > avg_cost * 1.04 and price <= trailing:
            reason = f"触发移动止盈{trailing:.2f}"
        elif phase in {"afternoon_risk", "evening_watch"} and (_num(stock.get("score")) or 0.0) < exit_score_threshold:
            reason = "评分跌破退出阈值"
        elif phase in {"afternoon_risk", "evening_watch"} and stock.get("status_key") == "avoid":
            reason = "模型状态转为不追高/退出"
        elif phase in {"afternoon_risk", "evening_watch"} and _hold_days(position, today) >= max_hold_days:
            reason = f"持仓达到{max_hold_days}天上限"
        if not reason:
            state["sellPlans"][code] = {
                "code": code, "name": position.get("name") or code, "status": "pending", "createdRunKey": state["sellPlans"].get(code, {}).get("createdRunKey") or _run_key(payload),
                "updatedRunKey": _run_key(payload), "plannedCheckPhase": "afternoon_risk", "hardStopPrice": stop, "takeProfitPrice": take,
                "trailingStopPct": trailing_stop_pct, "maxHoldDays": max_hold_days, "sellPriority": "normal", "warning": "", "lastDecision": "继续持有",
            }
            continue
        execution = price * (1 - SELL_SLIPPAGE_PCT)
        _sell(state, payload, stock, position, execution, available, reason)
        events.append({"type": "sell", "summary": f"云端卖出{position.get('name')}{available}股，原因：{reason}"})


def run_cloud_simulation(payload: dict[str, Any], raw_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = sanitize_simulation(raw_state)
    run_key = _run_key(payload)
    if not run_key or state.get("lastAutoRunKey") == run_key:
        return state
    events: list[dict[str, Any]] = []
    refresh_exit_feedback(state, payload)
    _execute_sells(state, payload, events)
    refresh_exit_feedback(state)
    if _phase(payload) == "evening_watch":
        _create_plans(state, payload, events)
    else:
        _execute_buys(state, payload, events)
    diagnostics = state["diagnostics"]
    diagnostics["snapshotsProcessed"] = int(diagnostics.get("snapshotsProcessed", 0)) + 1
    diagnostics["lastRunAt"] = str(payload.get("generated_at") or "")
    diagnostics["lastRunSummary"] = "；".join(event["summary"] for event in events) or "本次快照没有触发动作"
    state["autoLog"].insert(0, {
        "runKey": run_key,
        "at": str(payload.get("generated_at") or ""),
        "tradeDate": str(payload.get("as_of_date") or ""),
        "phase": _phase_label(payload),
        "phaseKey": _phase(payload),
        "events": events,
    })
    state["autoLog"] = state["autoLog"][:50]
    state["lastAutoRunKey"] = run_key
    state["cash"] = round(state["cash"], 4)
    return state


def replay_cloud_simulation(payloads: Iterable[dict[str, Any]], raw_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = sanitize_simulation(raw_state)
    ordered = sorted(payloads, key=lambda item: str(item.get("generated_at") or ""))
    for payload in ordered:
        state = run_cloud_simulation(payload, state)
    return state
