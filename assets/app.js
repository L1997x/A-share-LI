const INITIAL_SIM_CASH = 100000;
const SIM_STORAGE_KEY = "a-share-li-simulation-v1";
const AUTO_REFRESH_INTERVAL_MS = 2 * 60 * 1000;
const AUTO_REFRESH_INTERVAL_LABEL = "2分钟";

const DEFAULT_AUTO_SETTINGS = {
  enabled: true,
  maxPositionPct: 0.2,
  maxStocks: 5,
  minScore: 7.5,
  maxBuysPerRun: 2,
  afternoonMaxBuysPerRun: 1,
  afternoonMaxPositionPct: 0.1,
  afternoonMinScoreBuffer: 0.3,
  afternoonMinRiskAppetite: 0.45,
  afternoonMinFundFlowScore: 0,
  stopLossPct: 0.06,
  takeProfitPct: 0.12,
  trailingStopPct: 0.06,
  morningSlippagePct: 0.002,
  sellSlippagePct: 0.002,
  buyPlanValidDays: 3,
  reduceScoreThreshold: 6.8,
  exitScoreThreshold: 6.2,
  maxHoldDays: 10,
};

const DEFAULT_FEE_SETTINGS = {
  commissionRate: 0.0003,
  minCommission: 5,
  stampDutyRate: 0.0005,
  transferFeeRate: 0.00001,
};

const state = {
  data: null,
  review: null,
  filter: "all",
  simulation: loadSimulation(),
  autoRunMessage: "",
  refreshTimer: null,
  refreshInFlight: false,
  lastRefreshCheckAt: "",
  lastRefreshStatus: "等待首次检查",
  lastRefreshError: "",
};

const statusMap = {
  watch: { label: "观察区", className: "status-watch" },
  breakout: { label: "突破确认", className: "status-breakout" },
  wait: { label: "等回踩", className: "status-wait" },
  avoid: { label: "不追高", className: "status-avoid" },
};

const byId = (id) => document.getElementById(id);

const escapeHtml = (value) =>
  String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");

function isFiniteNumber(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

const formatNumber = (value, digits = 2) => {
  if (!isFiniteNumber(value)) return "-";
  return Number(value).toFixed(digits);
};

const formatCurrency = (value, digits = 2) => {
  if (!isFiniteNumber(value)) return "-";
  return Number(value).toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
};

const formatPercent = (value, digits = 2) => {
  if (!isFiniteNumber(value)) return "-";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(digits)}%`;
};

function formatRefreshTime(value) {
  if (!value) return "尚未检查";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "尚未检查";
  return date.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function refreshReasonLabel(reason) {
  const labels = {
    auto: "自动检查",
    initial: "首次加载",
    manual: "手动检查",
    online: "恢复联网检查",
    focus: "页面重新聚焦检查",
    pageshow: "页面恢复检查",
    visible: "回到前台检查",
  };
  return labels[reason] || "手动检查";
}

function formatDataMinute(value, fallback = "-") {
  const match = String(value || "").match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
  return match ? `${match[1]} ${match[2]}:${match[3]}` : fallback;
}

const formatFundMoney = (value) => {
  if (!isFiniteNumber(value)) return "-";
  return `${(Number(value) / 100000000).toFixed(2)}亿`;
};

const formatSignedNumber = (value, digits = 2) => {
  if (!isFiniteNumber(value)) return "-";
  const number = Number(value);
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(digits)}`;
};

const returnClass = (value) => {
  if (!isFiniteNumber(value)) return "return-flat";
  if (Number(value) > 0) return "return-positive";
  if (Number(value) < 0) return "return-negative";
  return "return-flat";
};

const buySignalClass = (key) => {
  if (key === "pullback_buy" || key === "breakout_buy") return "buy-now";
  if (key === "risk_wait" || key === "avoid") return "buy-avoid";
  return "buy-wait";
};

const fundFlowClass = (score) => {
  if (!isFiniteNumber(score)) return "fund-neutral";
  if (Number(score) >= 3) return "fund-positive";
  if (Number(score) <= -3) return "fund-negative";
  return "fund-neutral";
};

const chipClass = (score) => {
  if (!isFiniteNumber(score)) return "chip-neutral";
  if (Number(score) >= 0.4) return "chip-positive";
  if (Number(score) <= -0.3) return "chip-negative";
  return "chip-neutral";
};

const feedbackClass = (value) => {
  if (!isFiniteNumber(value)) return "feedback-neutral";
  if (Number(value) > 0.05) return "feedback-positive";
  if (Number(value) < -0.05) return "feedback-negative";
  return "feedback-neutral";
};

const formatBuyPrice = (stock) => {
  if (stock.is_buyable_now) return formatNumber(stock.buyable_price);
  return "未触发";
};

const formatFeedbackFactors = (stock) => {
  const factors = stock.feedback_factors || [];
  if (!factors.length) return stock.feedback_note || "暂无足够历史样本，反馈分暂不明显。";
  return factors
    .slice(0, 3)
    .map((factor) => `${factor.label}：${formatSignedNumber(factor.score_effect, 2)}，样本${factor.sample_count ?? "-"}`)
    .join("；");
};

const formatPriceFeedback = (stock) => {
  const adjustment = formatPercent(stock.price_feedback_adjustment_pct, 3);
  if (adjustment === "-") return stock.price_feedback_note || "价格反馈暂缺。";
  return `${stock.price_feedback_label || "价格纪律不变"}：${adjustment}。${stock.price_feedback_note || ""}`;
};

const formatEntrySafety = (stock) => {
  const adjustment = formatPercent(stock.entry_safety_adjustment_pct, 3);
  const factors = stock.entry_safety_factors || [];
  const safetyStatus = stock.entry_safety_block_buy
    ? "可买信号已被接入风控取消。"
    : stock.entry_safety_risk_flag
      ? "带接入风险标记，需等更低价格或更强确认。"
      : "";
  const factorText = factors.length
    ? factors
        .slice(0, 2)
        .map(
          (factor) =>
            `${factor.label}: ${formatPercent(factor.price_adjustment_pct, 3)}，触达后${formatPercent(
              factor.avg_touch_return_pct
            )}，未触达错过${formatPercent(factor.avg_missed_return_pct)}，触达率${formatPercent(
              factor.touch_rate_pct
            )}，回撤${formatPercent(factor.avg_adverse_drawdown_pct)}，暴跌率${formatPercent(factor.crash_rate_pct)}`
        )
        .join("；")
    : stock.entry_safety_note || "历史接入价样本不足。";
  return `${stock.entry_safety_label || "接入样本不足"}：安全调整 ${adjustment}。${safetyStatus}${factorText}`;
};

function createDefaultSimulation() {
  return {
    schemaVersion: 4,
    initialCash: INITIAL_SIM_CASH,
    cash: INITIAL_SIM_CASH,
    positions: {},
    trades: [],
    selectedCode: "",
    autoSettings: { ...DEFAULT_AUTO_SETTINGS },
    feeSettings: { ...DEFAULT_FEE_SETTINGS },
    lastAutoRunKey: "",
    autoLog: [],
    pendingBuyOrders: [],
    sellPlans: {},
    decisionJournal: [],
  };
}

function loadSimulation() {
  try {
    const raw = JSON.parse(localStorage.getItem(SIM_STORAGE_KEY) || "null");
    return sanitizeSimulation(raw);
  } catch (_) {
    return createDefaultSimulation();
  }
}

function sanitizeSimulation(raw) {
  if (!raw || typeof raw !== "object") return createDefaultSimulation();
  const initialCash = isFiniteNumber(raw.initialCash) && Number(raw.initialCash) > 0 ? Number(raw.initialCash) : INITIAL_SIM_CASH;
  const cash = isFiniteNumber(raw.cash) ? Number(raw.cash) : initialCash;
  const positions = {};
  const autoSettings = normalizeAutoSettings(raw.autoSettings);
  const feeSettings = normalizeFeeSettings(raw.feeSettings);

  Object.entries(raw.positions || {}).forEach(([code, position]) => {
    const quantity = Math.max(0, Math.floor(Number(position.quantity || 0)));
    if (!quantity) return;
    const avgCost = isFiniteNumber(position.avgCost) ? Number(position.avgCost) : Number(position.costBasis || 0) / quantity;
    const rawLots = Array.isArray(position.lots)
      ? position.lots
      : [{ quantity, price: avgCost, tradeDate: "历史", at: position.updatedAt || "" }];
    const lots = rawLots
      .map((lot) => ({
        quantity: Math.max(0, Math.floor(Number(lot.quantity || 0))),
        price: isFiniteNumber(lot.price) ? Number(lot.price) : avgCost,
        costBasis: isFiniteNumber(lot.costBasis)
          ? Number(lot.costBasis)
          : Math.max(0, Math.floor(Number(lot.quantity || 0))) * (isFiniteNumber(lot.price) ? Number(lot.price) : avgCost),
        fees: isFiniteNumber(lot.fees) ? Number(lot.fees) : 0,
        tradeDate: String(lot.tradeDate || "历史"),
        at: String(lot.at || ""),
        stopLossPrice: isFiniteNumber(lot.stopLossPrice) ? Number(lot.stopLossPrice) : null,
        takeProfitPrice: isFiniteNumber(lot.takeProfitPrice) ? Number(lot.takeProfitPrice) : null,
        highestPrice: isFiniteNumber(lot.highestPrice) ? Number(lot.highestPrice) : isFiniteNumber(lot.price) ? Number(lot.price) : avgCost,
        entryReason: lot.entryReason || "",
        source: lot.source || "manual",
      }))
      .filter((lot) => lot.quantity > 0 && isFiniteNumber(lot.price) && isFiniteNumber(lot.costBasis));
    const costBasis = lots.reduce((sum, lot) => sum + lot.quantity * lot.price, 0);
    if (!lots.length || costBasis <= 0) return;
    positions[code] = {
      code,
      name: position.name || code,
      quantity: lots.reduce((sum, lot) => sum + lot.quantity, 0),
      costBasis: lots.reduce((sum, lot) => sum + lot.costBasis, 0),
      lots,
      stopLossPrice: isFiniteNumber(position.stopLossPrice) ? Number(position.stopLossPrice) : null,
      takeProfitPrice: isFiniteNumber(position.takeProfitPrice) ? Number(position.takeProfitPrice) : null,
      highestPrice: isFiniteNumber(position.highestPrice) ? Number(position.highestPrice) : null,
      entryReason: position.entryReason || "",
      updatedAt: position.updatedAt || new Date().toISOString(),
    };
  });

  const trades = Array.isArray(raw.trades) ? raw.trades.slice(0, 100) : [];
  const autoLog = Array.isArray(raw.autoLog) ? raw.autoLog.slice(0, 50) : [];
  const pendingBuyOrders = normalizePendingBuyOrders(raw.pendingBuyOrders);
  const sellPlans = normalizeSellPlans(raw.sellPlans);
  const decisionJournal = normalizeDecisionJournal(raw.decisionJournal);
  return {
    schemaVersion: 4,
    initialCash,
    cash,
    positions,
    trades,
    selectedCode: raw.selectedCode || "",
    autoSettings,
    feeSettings,
    lastAutoRunKey: raw.lastAutoRunKey || "",
    autoLog,
    pendingBuyOrders,
    sellPlans,
    decisionJournal,
  };
}

function normalizeAutoSettings(raw = {}) {
  return {
    enabled: raw.enabled === undefined ? DEFAULT_AUTO_SETTINGS.enabled : Boolean(raw.enabled),
    maxPositionPct: clampPercent(raw.maxPositionPct, DEFAULT_AUTO_SETTINGS.maxPositionPct, 0.01, 1),
    maxStocks: Math.max(1, Math.min(10, Math.floor(Number(raw.maxStocks ?? DEFAULT_AUTO_SETTINGS.maxStocks)) || DEFAULT_AUTO_SETTINGS.maxStocks)),
    minScore: Math.max(0, Math.min(10, Number(raw.minScore ?? DEFAULT_AUTO_SETTINGS.minScore))),
    maxBuysPerRun: Math.max(1, Math.min(5, Math.floor(Number(raw.maxBuysPerRun ?? DEFAULT_AUTO_SETTINGS.maxBuysPerRun)) || DEFAULT_AUTO_SETTINGS.maxBuysPerRun)),
    afternoonMaxBuysPerRun: Math.max(
      1,
      Math.min(3, Math.floor(Number(raw.afternoonMaxBuysPerRun ?? DEFAULT_AUTO_SETTINGS.afternoonMaxBuysPerRun)) || DEFAULT_AUTO_SETTINGS.afternoonMaxBuysPerRun)
    ),
    afternoonMaxPositionPct: clampPercent(raw.afternoonMaxPositionPct, DEFAULT_AUTO_SETTINGS.afternoonMaxPositionPct, 0.01, 0.3),
    afternoonMinScoreBuffer: Math.max(0, Math.min(2, Number(raw.afternoonMinScoreBuffer ?? DEFAULT_AUTO_SETTINGS.afternoonMinScoreBuffer))),
    afternoonMinRiskAppetite: Math.max(0, Math.min(1, Number(raw.afternoonMinRiskAppetite ?? DEFAULT_AUTO_SETTINGS.afternoonMinRiskAppetite))),
    afternoonMinFundFlowScore: Math.max(-10, Math.min(10, Number(raw.afternoonMinFundFlowScore ?? DEFAULT_AUTO_SETTINGS.afternoonMinFundFlowScore))),
    stopLossPct: clampPercent(raw.stopLossPct, DEFAULT_AUTO_SETTINGS.stopLossPct, 0.01, 0.3),
    takeProfitPct: clampPercent(raw.takeProfitPct, DEFAULT_AUTO_SETTINGS.takeProfitPct, 0.01, 0.8),
    trailingStopPct: clampPercent(raw.trailingStopPct, DEFAULT_AUTO_SETTINGS.trailingStopPct, 0.01, 0.3),
    morningSlippagePct: clampPercent(raw.morningSlippagePct, DEFAULT_AUTO_SETTINGS.morningSlippagePct, 0, 0.02),
    sellSlippagePct: clampPercent(raw.sellSlippagePct, DEFAULT_AUTO_SETTINGS.sellSlippagePct, 0, 0.02),
    buyPlanValidDays: Math.max(
      1,
      Math.min(10, Math.floor(Number(raw.buyPlanValidDays ?? DEFAULT_AUTO_SETTINGS.buyPlanValidDays)) || DEFAULT_AUTO_SETTINGS.buyPlanValidDays)
    ),
    reduceScoreThreshold: Math.max(0, Math.min(10, Number(raw.reduceScoreThreshold ?? DEFAULT_AUTO_SETTINGS.reduceScoreThreshold))),
    exitScoreThreshold: Math.max(0, Math.min(10, Number(raw.exitScoreThreshold ?? DEFAULT_AUTO_SETTINGS.exitScoreThreshold))),
    maxHoldDays: Math.max(1, Math.min(60, Math.floor(Number(raw.maxHoldDays ?? DEFAULT_AUTO_SETTINGS.maxHoldDays)) || DEFAULT_AUTO_SETTINGS.maxHoldDays)),
  };
}

function normalizePendingBuyOrders(raw = []) {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((order) => {
      if (!order || typeof order !== "object") return null;
      return {
        id: order.id || `${order.code || "order"}-${order.createdRunKey || order.signalDate || ""}`,
        code: String(order.code || ""),
        name: order.name || order.code || "",
        status: order.status || "pending",
        signalDate: order.signalDate || "",
        signalPhase: order.signalPhase || "",
        createdRunKey: order.createdRunKey || "",
        plannedExecutionPhase: order.plannedExecutionPhase || "morning_entry",
        validUntilDate: order.validUntilDate || (order.signalDate ? buyPlanValidUntilDate(order.signalDate) : ""),
        plannedEntryPrice: isFiniteNumber(order.plannedEntryPrice) ? Number(order.plannedEntryPrice) : null,
        maxBuyPrice: isFiniteNumber(order.maxBuyPrice) ? Number(order.maxBuyPrice) : null,
        noChasePrice: isFiniteNumber(order.noChasePrice) ? Number(order.noChasePrice) : null,
        score: isFiniteNumber(order.score) ? Number(order.score) : null,
        buySignalKey: order.buySignalKey || "",
        statusKey: order.statusKey || "",
        entrySafetyBlockBuy: Boolean(order.entrySafetyBlockBuy),
        reason: order.reason || "",
        cancelReason: order.cancelReason || "",
        executedAt: order.executedAt || "",
        executionPrice: isFiniteNumber(order.executionPrice) ? Number(order.executionPrice) : null,
      };
    })
    .filter((order) => order && order.code);
}

function normalizeSellPlans(raw = {}) {
  const source = raw && typeof raw === "object" ? raw : {};
  const plans = {};
  Object.entries(source).forEach(([code, plan]) => {
    if (!plan || typeof plan !== "object") return;
    plans[code] = {
      code,
      name: plan.name || code,
      status: plan.status || "pending",
      createdRunKey: plan.createdRunKey || "",
      updatedRunKey: plan.updatedRunKey || "",
      plannedCheckPhase: plan.plannedCheckPhase || "afternoon_risk",
      hardStopPrice: isFiniteNumber(plan.hardStopPrice) ? Number(plan.hardStopPrice) : null,
      takeProfitPrice: isFiniteNumber(plan.takeProfitPrice) ? Number(plan.takeProfitPrice) : null,
      trailingStopPct: isFiniteNumber(plan.trailingStopPct) ? Number(plan.trailingStopPct) : DEFAULT_AUTO_SETTINGS.trailingStopPct,
      maxHoldDays: Number.isFinite(Number(plan.maxHoldDays)) ? Number(plan.maxHoldDays) : DEFAULT_AUTO_SETTINGS.maxHoldDays,
      sellPriority: plan.sellPriority || "normal",
      warning: plan.warning || "",
      lastDecision: plan.lastDecision || "",
    };
  });
  return plans;
}

function normalizeDecisionJournal(raw = []) {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((record) => {
      if (!record || typeof record !== "object") return null;
      return {
        id: record.id || `${record.code || "record"}-${record.at || ""}`,
        at: record.at || "",
        tradeDate: record.tradeDate || "",
        phase: record.phase || "",
        phaseKey: record.phaseKey || "",
        type: record.type || "info",
        status: record.status || "",
        code: String(record.code || ""),
        name: record.name || record.code || "",
        summary: record.summary || "",
        reason: record.reason || "",
        plannedEntryPrice: isFiniteNumber(record.plannedEntryPrice) ? Number(record.plannedEntryPrice) : null,
        maxBuyPrice: isFiniteNumber(record.maxBuyPrice) ? Number(record.maxBuyPrice) : null,
        noChasePrice: isFiniteNumber(record.noChasePrice) ? Number(record.noChasePrice) : null,
        validUntilDate: record.validUntilDate || "",
        snapshotPrice: isFiniteNumber(record.snapshotPrice) ? Number(record.snapshotPrice) : null,
        executionPrice: isFiniteNumber(record.executionPrice) ? Number(record.executionPrice) : null,
        stopLossPrice: isFiniteNumber(record.stopLossPrice) ? Number(record.stopLossPrice) : null,
        takeProfitPrice: isFiniteNumber(record.takeProfitPrice) ? Number(record.takeProfitPrice) : null,
        quantity: isFiniteNumber(record.quantity) ? Number(record.quantity) : null,
        feeTotal: isFiniteNumber(record.feeTotal) ? Number(record.feeTotal) : null,
        realizedPnl: isFiniteNumber(record.realizedPnl) ? Number(record.realizedPnl) : null,
        score: isFiniteNumber(record.score) ? Number(record.score) : null,
      };
    })
    .filter((record) => record && (record.code || record.summary))
    .slice(0, 100);
}

function normalizeFeeSettings(raw = {}) {
  return {
    commissionRate: clampPercent(raw.commissionRate, DEFAULT_FEE_SETTINGS.commissionRate, 0, 0.01),
    minCommission: Math.max(0, Number(raw.minCommission ?? DEFAULT_FEE_SETTINGS.minCommission)),
    stampDutyRate: clampPercent(raw.stampDutyRate, DEFAULT_FEE_SETTINGS.stampDutyRate, 0, 0.01),
    transferFeeRate: clampPercent(raw.transferFeeRate, DEFAULT_FEE_SETTINGS.transferFeeRate, 0, 0.001),
  };
}

function clampPercent(value, fallback, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, number));
}

function saveSimulation() {
  localStorage.setItem(SIM_STORAGE_KEY, JSON.stringify(state.simulation));
}

function feeSettings() {
  state.simulation.feeSettings = normalizeFeeSettings(state.simulation.feeSettings);
  return state.simulation.feeSettings;
}

function autoSettings() {
  state.simulation.autoSettings = normalizeAutoSettings(state.simulation.autoSettings);
  return state.simulation.autoSettings;
}

function calculateTradeFees(type, grossAmount) {
  if (!isFiniteNumber(grossAmount) || Number(grossAmount) <= 0) {
    return { commission: 0, stampDuty: 0, transferFee: 0, total: 0 };
  }
  const settings = feeSettings();
  const amount = Number(grossAmount);
  const commission = settings.commissionRate > 0 ? Math.max(amount * settings.commissionRate, settings.minCommission) : 0;
  const stampDuty = type === "sell" ? amount * settings.stampDutyRate : 0;
  const transferFee = amount * settings.transferFeeRate;
  return {
    commission,
    stampDuty,
    transferFee,
    total: commission + stampDuty + transferFee,
  };
}

function totalTradeFees() {
  return (state.simulation.trades || []).reduce((sum, trade) => sum + Number(trade.fees?.total || trade.feeTotal || 0), 0);
}

function currentTradeDate() {
  return state.data?.as_of_date || new Date().toISOString().slice(0, 10);
}

function parseDateText(value) {
  const date = new Date(`${value || ""}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDateText(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function addCalendarDays(dateText, days) {
  const date = parseDateText(dateText) || parseDateText(currentTradeDate()) || new Date();
  date.setDate(date.getDate() + Number(days || 0));
  return formatDateText(date);
}

function compareDateText(left, right) {
  const leftDate = parseDateText(left);
  const rightDate = parseDateText(right);
  if (!leftDate || !rightDate) return 0;
  return leftDate.getTime() - rightDate.getTime();
}

function buyPlanValidUntilDate(signalDate, validDays = DEFAULT_AUTO_SETTINGS.buyPlanValidDays) {
  return addCalendarDays(signalDate || currentTradeDate(), Math.max(1, Number(validDays || DEFAULT_AUTO_SETTINGS.buyPlanValidDays)));
}

function ensureBuyOrderValidUntil(order) {
  if (!order.validUntilDate) {
    order.validUntilDate = buyPlanValidUntilDate(order.signalDate || currentTradeDate(), autoSettings().buyPlanValidDays);
  }
  return order.validUntilDate;
}

function isBuyOrderExpired(order, tradeDate = currentTradeDate()) {
  const validUntilDate = ensureBuyOrderValidUntil(order);
  return compareDateText(tradeDate, validUntilDate) > 0;
}

function currentCalendarDateText() {
  return formatDateText(new Date());
}

function weekdayLabel(dateText) {
  const labels = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
  const date = parseDateText(dateText);
  return date ? labels[date.getDay()] : "";
}

function isWeekendDate(dateText) {
  const date = parseDateText(dateText);
  if (!date) return false;
  const day = date.getDay();
  return day === 0 || day === 6;
}

function expectedIntradaySnapshot(data, now = new Date()) {
  const asOfDate = data?.as_of_date;
  const generatedAt = String(data?.generated_at || "");
  const chinaParts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(now)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value])
  );
  const today = `${chinaParts.year}-${chinaParts.month}-${chinaParts.day}`;
  if (!asOfDate || asOfDate !== today || isWeekendDate(today)) return null;

  const currentMinutes = Number(chinaParts.hour) * 60 + Number(chinaParts.minute);
  const checkpoints = [
    { target: 10 * 60, grace: 25, label: "10:00" },
    { target: 11 * 60 + 20, grace: 25, label: "11:20" },
    { target: 13 * 60 + 30, grace: 25, label: "13:30" },
    { target: 14 * 60 + 30, grace: 25, label: "14:30" },
    { target: 20 * 60, grace: 25, label: "20:00" },
  ];
  const dueCheckpoints = checkpoints.filter((item) => currentMinutes >= item.target + item.grace);
  const expected = dueCheckpoints[dueCheckpoints.length - 1];
  if (!expected) return null;

  const generatedMatch = generatedAt.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
  const generatedDate = generatedMatch?.[1] || "";
  const generatedMinutes = generatedMatch ? Number(generatedMatch[2]) * 60 + Number(generatedMatch[3]) : -1;
  if (generatedDate === today && generatedMinutes >= expected.target - 12) return null;
  return expected;
}

function marketFreshnessNote(data) {
  const asOfDate = data?.as_of_date;
  if (!asOfDate) return "";
  const today = currentCalendarDateText();
  const delayedSnapshot = expectedIntradaySnapshot(data);
  if (delayedSnapshot) {
    return `；${delayedSnapshot.label} 计划快照尚未发布，系统正在等待 GitHub 备用任务恢复`;
  }
  if (compareDateText(asOfDate, today) >= 0) return "";
  if (isWeekendDate(today)) {
    return `；今天${weekdayLabel(today)}休市，显示最近交易日 ${asOfDate} 的数据属于正常情况`;
  }
  return `；数据日期早于今天 ${today}，请等待下一次 GitHub 更新或手动刷新`;
}

function stocks() {
  return state.data?.stocks || [];
}

function findStock(code) {
  return stocks().find((stock) => String(stock.code) === String(code));
}

function reviewRecords() {
  return state.review?.records || state.data?.review?.records || [];
}

function findReviewRecord(code) {
  return reviewRecords().find((record) => String(record.code) === String(code));
}

function stockOrReview(code) {
  return findStock(code) || findReviewRecord(code);
}

function latestPriceFor(code) {
  const stock = findStock(code);
  const review = findReviewRecord(code);
  const candidates = [stock?.live_quote_price, stock?.close, stock?.daily_close, review?.latest_price];
  const price = candidates.find((candidate) => isFiniteNumber(candidate) && Number(candidate) > 0);
  return price ? Number(price) : null;
}

function buyOrderStockSnapshot(order) {
  const stock = findStock(order.code);
  if (stock) return { stock, inLatestPool: true, hasFreshPrice: true };
  const review = findReviewRecord(order.code);
  if (!review) return { stock: null, inLatestPool: false, hasFreshPrice: false };
  const hasFreshPrice = review.latest_date === currentTradeDate() && isFiniteNumber(review.latest_price) && Number(review.latest_price) > 0;
  return {
    stock: {
      code: order.code,
      name: order.name || review.name || order.code,
      close: review.latest_price,
      live_quote_price: review.latest_price,
      recommended_entry_price: order.plannedEntryPrice || review.first_recommended_entry_price,
      buyable_price: order.plannedEntryPrice || review.first_buyable_price,
      buyable_price_upper: order.maxBuyPrice || review.first_entry_price_upper,
      no_chase_price: order.noChasePrice || review.first_entry_price_upper,
      score: order.score || review.review_rank || 0,
      buy_signal_key: order.buySignalKey || review.first_buy_signal_key || "",
      status_key: order.statusKey || review.first_status_key || "",
      entry_safety_block_buy: Boolean(order.entrySafetyBlockBuy || review.first_entry_safety_block_buy),
      fromReviewOnly: true,
    },
    inLatestPool: false,
    hasFreshPrice,
  };
}

function suggestedTradePrice(stock) {
  const candidates = [
    stock?.is_buyable_now ? stock.buyable_price : null,
    stock?.recommended_entry_price,
    stock?.buyable_price,
    stock?.close,
  ];
  const price = candidates.find((candidate) => isFiniteNumber(candidate) && Number(candidate) > 0);
  return price ? Number(price) : null;
}

function computeStopLossPrice(stock, entryPrice) {
  const settings = autoSettings();
  const fallback = entryPrice * (1 - settings.stopLossPct);
  const invalidPrice = Number(stock?.invalid_price);
  if (Number.isFinite(invalidPrice) && invalidPrice > 0 && invalidPrice < entryPrice) {
    return Math.max(invalidPrice, fallback);
  }
  return fallback;
}

function computeTakeProfitPrice(stock, entryPrice) {
  const settings = autoSettings();
  const fallback = entryPrice * (1 + settings.takeProfitPct);
  const resistancePrice = Number(stock?.resistance_price);
  if (Number.isFinite(resistancePrice) && resistancePrice > entryPrice) {
    return Math.min(resistancePrice, fallback);
  }
  return fallback;
}

function averageCost(position) {
  if (!position || !position.quantity) return 0;
  return position.costBasis / position.quantity;
}

function availableSellQuantity(position) {
  const today = currentTradeDate();
  return (position?.lots || [])
    .filter((lot) => lot.tradeDate !== today)
    .reduce((sum, lot) => sum + lot.quantity, 0);
}

function normalizeQuantity(value) {
  const quantity = Math.floor(Number(value || 0) / 100) * 100;
  return quantity >= 100 ? quantity : 0;
}

function minimumBuyLotCost(price) {
  if (!isFiniteNumber(price) || Number(price) <= 0) return null;
  const grossAmount = Number(price) * 100;
  return grossAmount + calculateTradeFees("buy", grossAmount).total;
}

function buyOrderAffordability(order, options = {}) {
  const settings = autoSettings();
  const price = firstFinite(order.plannedEntryPrice, order.maxBuyPrice);
  if (!price) {
    return { affordable: false, reason: "缺少计划价，无法校验一手资金" };
  }
  const positionPct = isFiniteNumber(options.positionPct) ? Number(options.positionPct) : settings.maxPositionPct;
  const snapshot = portfolioSnapshot();
  const minimumCost = minimumBuyLotCost(price);
  const positionLimit = snapshot.totalAssets * positionPct;
  const quantity = autoBuyQuantity(price, { positionPct });
  if (quantity >= 100) {
    return { affordable: true, minimumCost, positionLimit, cash: state.simulation.cash };
  }
  const bottleneck = minimumCost > state.simulation.cash ? `当前现金${formatCurrency(state.simulation.cash)}元` : `单票上限${formatCurrency(positionLimit)}元`;
  return {
    affordable: false,
    minimumCost,
    positionLimit,
    cash: state.simulation.cash,
    reason: `一手约需${formatCurrency(minimumCost)}元，超过${bottleneck}`,
  };
}

function filterAffordableBuyOrders(orders, events, contextLabel, options = {}) {
  const affordableOrders = [];
  const blockedOrders = [];
  orders.forEach((order) => {
    const check = buyOrderAffordability(order, options);
    order.minimumLotCost = check.minimumCost;
    if (check.affordable) {
      affordableOrders.push(order);
    } else {
      order.affordabilityBlockReason = check.reason;
      blockedOrders.push(order);
    }
  });
  if (blockedOrders.length) {
    const sample = blockedOrders
      .slice(0, 3)
      .map((order) => `${order.name}${order.minimumLotCost ? `一手约${formatCurrency(order.minimumLotCost, 0)}元` : ""}`)
      .join("、");
    events?.push({ type: "buy_skip", summary: `${contextLabel}资金约束过滤${blockedOrders.length}只：${sample}` });
  }
  return affordableOrders;
}

function portfolioSnapshot() {
  const positions = Object.values(state.simulation.positions || {}).map((position) => {
    const price = latestPriceFor(position.code) ?? averageCost(position);
    const marketValue = position.quantity * price;
    const estimatedSellFees = calculateTradeFees("sell", marketValue);
    const liquidationValue = Math.max(0, marketValue - estimatedSellFees.total);
    const bookUnrealized = marketValue - position.costBasis;
    const liquidationUnrealized = liquidationValue - position.costBasis;
    const bookUnrealizedPct = position.costBasis > 0 ? (bookUnrealized / position.costBasis) * 100 : 0;
    const liquidationUnrealizedPct = position.costBasis > 0 ? (liquidationUnrealized / position.costBasis) * 100 : 0;
    const highestPrice = Math.max(
      position.highestPrice || 0,
      price || 0,
      ...(position.lots || []).map((lot) => Number(lot.highestPrice || lot.price || 0))
    );
    return {
      ...position,
      avgCost: averageCost(position),
      latestPrice: price,
      marketValue,
      estimatedSellFees: estimatedSellFees.total,
      liquidationValue,
      unrealized: bookUnrealized,
      unrealizedPct: bookUnrealizedPct,
      bookUnrealized,
      bookUnrealizedPct,
      liquidationUnrealized,
      liquidationUnrealizedPct,
      highestPrice,
      availableQuantity: availableSellQuantity(position),
    };
  });
  const marketValue = positions.reduce((sum, position) => sum + position.marketValue, 0);
  const liquidationValue = positions.reduce((sum, position) => sum + position.liquidationValue, 0);
  const bookAssets = state.simulation.cash + marketValue;
  const totalAssets = bookAssets;
  const liquidationAssets = state.simulation.cash + liquidationValue;
  const totalReturn = totalAssets - state.simulation.initialCash;
  const totalReturnPct = state.simulation.initialCash > 0 ? (totalReturn / state.simulation.initialCash) * 100 : 0;
  const liquidationReturn = liquidationAssets - state.simulation.initialCash;
  const liquidationReturnPct = state.simulation.initialCash > 0 ? (liquidationReturn / state.simulation.initialCash) * 100 : 0;
  return {
    positions,
    marketValue,
    liquidationValue,
    bookAssets,
    totalAssets,
    totalReturn,
    totalReturnPct,
    liquidationAssets,
    liquidationReturn,
    liquidationReturnPct,
  };
}

function addTrade(trade) {
  state.simulation.trades.unshift({
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    at: new Date().toISOString(),
    tradeDate: currentTradeDate(),
    ...trade,
  });
  state.simulation.trades = state.simulation.trades.slice(0, 200);
}

function buyPosition(stock, price, quantity, options = {}) {
  const normalizedQuantity = normalizeQuantity(quantity);
  if (!stock || !isFiniteNumber(price) || Number(price) <= 0 || !normalizedQuantity) {
    return { ok: false, message: "买入参数无效。" };
  }
  const grossAmount = Number(price) * normalizedQuantity;
  const fees = calculateTradeFees("buy", grossAmount);
  const totalCost = grossAmount + fees.total;
  if (totalCost > state.simulation.cash + 0.0001) {
    return { ok: false, message: `可用现金不足，本次需要 ${formatCurrency(totalCost)} 元。` };
  }

  const stopLossPrice = computeStopLossPrice(stock, Number(price));
  const takeProfitPrice = computeTakeProfitPrice(stock, Number(price));
  const position =
    state.simulation.positions[stock.code] ||
    {
      code: stock.code,
      name: stock.name,
      quantity: 0,
      costBasis: 0,
      lots: [],
      updatedAt: "",
    };
  position.name = stock.name;
  position.quantity += normalizedQuantity;
  position.costBasis += totalCost;
  position.stopLossPrice = stopLossPrice;
  position.takeProfitPrice = takeProfitPrice;
  position.highestPrice = Math.max(position.highestPrice || 0, Number(price));
  position.entryReason = options.reason || position.entryReason || "";
  position.lots.push({
    quantity: normalizedQuantity,
    price: Number(price),
    grossAmount,
    fees: fees.total,
    costBasis: totalCost,
    tradeDate: currentTradeDate(),
    at: new Date().toISOString(),
    stopLossPrice,
    takeProfitPrice,
    highestPrice: Number(price),
    entryReason: options.reason || "",
    source: options.source || "manual",
  });
  position.updatedAt = new Date().toISOString();
  state.simulation.positions[stock.code] = position;
  state.simulation.cash -= totalCost;
  addTrade({
    type: "buy",
    source: options.source || "manual",
    reason: options.reason || "手动模拟买入",
    code: stock.code,
    name: stock.name,
    price: Number(price),
    quantity: normalizedQuantity,
    amount: grossAmount,
    grossAmount,
    netAmount: totalCost,
    fees,
    stopLossPrice,
    takeProfitPrice,
  });
  return { ok: true, grossAmount, fees, totalCost, quantity: normalizedQuantity, stopLossPrice, takeProfitPrice };
}

function sellPosition(stock, price, quantity, options = {}) {
  const position = state.simulation.positions[stock.code];
  const normalizedQuantity = normalizeQuantity(quantity);
  if (!stock || !position || !position.quantity) {
    return { ok: false, message: "当前没有这只股票的模拟持仓。" };
  }
  if (!isFiniteNumber(price) || Number(price) <= 0 || !normalizedQuantity) {
    return { ok: false, message: "卖出参数无效。" };
  }
  const availableQuantity = availableSellQuantity(position);
  if (normalizedQuantity > availableQuantity) {
    return {
      ok: false,
      message: `可卖数量不足。按 T+1 规则，本交易日买入的数量暂不可卖；当前可卖 ${availableQuantity} 股。`,
    };
  }

  let remaining = normalizedQuantity;
  let releasedCost = 0;
  const today = currentTradeDate();
  position.lots = position.lots.map((lot) => {
    if (remaining <= 0 || lot.tradeDate === today) return lot;
    const take = Math.min(lot.quantity, remaining);
    const perShareCost = lot.costBasis / lot.quantity;
    remaining -= take;
    releasedCost += perShareCost * take;
    return {
      ...lot,
      quantity: lot.quantity - take,
      costBasis: Math.max(0, lot.costBasis - perShareCost * take),
    };
  });

  if (remaining > 0) {
    return { ok: false, message: "卖出失败：可卖老仓不足。" };
  }

  const grossAmount = Number(price) * normalizedQuantity;
  const fees = calculateTradeFees("sell", grossAmount);
  const netAmount = grossAmount - fees.total;
  const realizedPnl = netAmount - releasedCost;
  position.lots = position.lots.filter((lot) => lot.quantity > 0);
  position.quantity -= normalizedQuantity;
  position.costBasis = Math.max(0, position.costBasis - releasedCost);
  position.updatedAt = new Date().toISOString();
  if (position.quantity <= 0) {
    delete state.simulation.positions[stock.code];
  } else {
    state.simulation.positions[stock.code] = position;
  }
  state.simulation.cash += netAmount;
  addTrade({
    type: "sell",
    source: options.source || "manual",
    reason: options.reason || "手动模拟卖出",
    code: stock.code,
    name: stock.name,
    price: Number(price),
    quantity: normalizedQuantity,
    amount: grossAmount,
    grossAmount,
    netAmount,
    fees,
    realizedPnl,
  });
  return { ok: true, grossAmount, fees, netAmount, releasedCost, realizedPnl, quantity: normalizedQuantity };
}

function setSimulationMessage(message, type = "info") {
  const element = byId("simulationMessage");
  element.textContent = message || "";
  element.className = `simulation-message ${type}`;
}

function setManualRefreshButton(isLoading = false, label = "") {
  const button = byId("manualRefreshButton");
  if (!button) return;
  button.disabled = Boolean(isLoading);
  button.textContent = label || (isLoading ? "更新中..." : "手动更新");
}

function currentAutoRunKey() {
  return autoRunKeyForData(state.data);
}

function autoRunKeyForData(data) {
  return [data?.generated_at, data?.as_of_date, data?.universe_scan?.update_phase_label].filter(Boolean).join("|");
}

function currentPhaseKey() {
  const phase = state.data?.universe_scan?.update_phase;
  const label = state.data?.universe_scan?.update_phase_label || "";
  if (phase === "morning_entry" || label.includes("10点") || label.includes("早盘") || label.includes("接入") || label.includes("买入复检")) return "morning_entry";
  if (phase === "afternoon_risk" || label.includes("14") || label.includes("尾盘") || label.includes("风控")) return "afternoon_risk";
  if (phase === "evening_watch" || label.includes("20点") || label.includes("次日")) return "evening_watch";
  const hour = Number(String(state.data?.generated_at || "").match(/T(\d{2}):/)?.[1]);
  if (hour >= 19 || hour < 2) return "evening_watch";
  if (hour >= 14) return "afternoon_risk";
  if (hour >= 9) return "morning_entry";
  return "unknown";
}

function phaseLabel(phase = currentPhaseKey()) {
  const labels = {
    morning_entry: "买入复检窗口",
    afternoon_risk: "14:30卖出执行",
    evening_watch: "20点生成次日计划",
    unknown: "非交易执行时段",
  };
  return labels[phase] || labels.unknown;
}

function currentBuyCheckLabel() {
  return state.data?.universe_scan?.update_phase_label || "买入复检";
}

function buyCheckLabelForOptions(options = {}) {
  return isAfternoonFinalMode(options) ? "14:30尾盘严格买入复检" : currentBuyCheckLabel();
}

function runAutoStrategy({ force = false, quiet = false } = {}) {
  if (!state.data) return { ran: false, events: [] };
  const settings = autoSettings();
  if (!settings.enabled) {
    if (!quiet) setSimulationMessage("自动模拟交易未启用。", "info");
    return { ran: false, events: [] };
  }
  const runKey = currentAutoRunKey();
  if (!runKey) return { ran: false, events: [] };
  if (!force && state.simulation.lastAutoRunKey === runKey) {
    if (!quiet) setSimulationMessage("本次数据快照已经自动检查过，没有重复执行。", "info");
    return { ran: false, events: [] };
  }

  const events = [];
  const phase = currentPhaseKey();
  updatePositionHighs();
  ensureSellPlans(runKey);

  if (phase === "evening_watch") {
    expirePendingBuyOrders(events, {
      expiredOnly: true,
      reason: "待买计划超过有效期，自动清理",
      summaryPrefix: "过期待买计划清理",
    });
    createEveningBuyPlans(runKey, events);
    ensureSellPlans(runKey, { log: true, events });
  } else if (phase === "morning_entry") {
    runMorningRiskReview(events);
    executePendingBuyOrders(runKey, events);
  } else if (phase === "afternoon_risk") {
    executeAfternoonSellPlans(events);
    executePendingBuyOrders(runKey, events, { mode: "afternoon_final" });
    expirePendingBuyOrders(events, {
      expiredOnly: true,
      reason: "待买计划超过有效期，自动清理",
      summaryPrefix: "过期待买计划清理",
    });
  } else {
    events.push({ type: "idle", summary: `${phaseLabel(phase)}：仅更新持仓高点，不执行买卖` });
  }

  state.simulation.lastAutoRunKey = runKey;
  pushAutoLog(runKey, phase, events);
  saveSimulation();

  const message = events.length
    ? `自动策略完成：${events.map((event) => event.summary).join("；")}`
    : `自动策略完成：${phaseLabel(phase)}没有触发动作。`;
  state.autoRunMessage = message;
  if (!quiet) setSimulationMessage(message, events.length ? "success" : "info");
  return { ran: true, events };
}

function updatePositionHighs() {
  Object.values(state.simulation.positions || {}).forEach((position) => {
    const price = latestPriceFor(position.code);
    if (!price) return;
    position.highestPrice = Math.max(position.highestPrice || 0, price);
    position.lots = (position.lots || []).map((lot) => ({
      ...lot,
      highestPrice: Math.max(lot.highestPrice || lot.price || 0, price),
    }));
  });
}

function ensureSellPlans(runKey = currentAutoRunKey(), options = {}) {
  state.simulation.sellPlans = state.simulation.sellPlans || {};
  const activeCodes = new Set(Object.keys(state.simulation.positions || {}));
  Object.keys(state.simulation.sellPlans).forEach((code) => {
    if (!activeCodes.has(code)) delete state.simulation.sellPlans[code];
  });

  Object.values(state.simulation.positions || {}).forEach((position) => {
    const stock = stockOrReview(position.code) || { code: position.code, name: position.name };
    state.simulation.sellPlans[position.code] = buildSellPlan(position, stock, runKey, state.simulation.sellPlans[position.code]);
  });

  if (options.log && activeCodes.size) {
    options.events?.push({ type: "sell_plan", summary: `更新${activeCodes.size}只持仓的次日14:30卖出计划` });
  }
}

function buildSellPlan(position, stock, runKey, existing = {}) {
  const settings = autoSettings();
  const avgCost = averageCost(position);
  const hardStopPrice = position.stopLossPrice || existing.hardStopPrice || computeStopLossPrice(stock, avgCost);
  const takeProfitPrice = position.takeProfitPrice || existing.takeProfitPrice || computeTakeProfitPrice(stock, avgCost);
  return {
    code: position.code,
    name: position.name || stock.name || position.code,
    status: "pending",
    createdRunKey: existing.createdRunKey || runKey,
    updatedRunKey: runKey,
    plannedCheckPhase: "afternoon_risk",
    hardStopPrice,
    takeProfitPrice,
    trailingStopPct: settings.trailingStopPct,
    maxHoldDays: settings.maxHoldDays,
    sellPriority: existing.sellPriority || "normal",
    warning: existing.warning || "",
    lastDecision: existing.lastDecision || "",
  };
}

function expirePendingBuyOrders(events, options = {}) {
  const reason = options.reason || "待买计划超过有效期，自动清理";
  const summaryPrefix = options.summaryPrefix || "旧待买计划过期";
  const expiredOnly = options.expiredOnly !== false;
  let expired = 0;
  state.simulation.pendingBuyOrders = (state.simulation.pendingBuyOrders || []).map((order) => {
    if (order.status === "pending") {
      ensureBuyOrderValidUntil(order);
      if (expiredOnly && !isBuyOrderExpired(order)) return order;
      expired += 1;
      const expiredOrder = { ...order, status: "expired", cancelReason: reason };
      pushDecisionRecord({
        type: "buy_plan_expired",
        status: "expired",
        code: expiredOrder.code,
        name: expiredOrder.name,
        summary: `${expiredOrder.name}待买计划过期`,
        reason: expiredOrder.cancelReason,
        plannedEntryPrice: expiredOrder.plannedEntryPrice,
        maxBuyPrice: expiredOrder.maxBuyPrice,
        noChasePrice: expiredOrder.noChasePrice,
        validUntilDate: expiredOrder.validUntilDate,
        score: expiredOrder.score,
      });
      return expiredOrder;
    }
    return order;
  });
  if (expired) events.push({ type: "expire", summary: `${summaryPrefix}${expired}条` });
}

function createEveningBuyPlans(runKey, events) {
  const settings = autoSettings();
  const slots = Math.max(0, settings.maxStocks - Object.keys(state.simulation.positions || {}).length);
  const limit = Math.min(settings.maxBuysPerRun, slots);
  if (!limit) {
    events.push({ type: "buy_plan", summary: "持仓数量已达上限，不生成新买入计划" });
    return;
  }

  const candidateOrders = [...stocks()]
    .filter((stock) => !state.simulation.positions[stock.code])
    .filter((stock) => Number(stock.score || 0) >= settings.minScore)
    .filter((stock) => !stock.entry_safety_block_buy && stock.buy_signal_key !== "risk_wait" && stock.buy_signal_key !== "avoid" && stock.status_key !== "avoid")
    .sort((a, b) => Number(isActionableBuySignal(b)) - Number(isActionableBuySignal(a)) || Number(b.score || 0) - Number(a.score || 0))
    .map((stock) => buildPendingBuyOrder(stock, runKey));
  const orders = filterAffordableBuyOrders(candidateOrders, events, "20点计划").slice(0, limit);

  const refreshedCodes = new Set(orders.map((order) => String(order.code)));
  const stillValidPending = (state.simulation.pendingBuyOrders || []).filter(
    (order) => order.status === "pending" && !refreshedCodes.has(String(order.code)) && !isBuyOrderExpired(order) && buyOrderAffordability(order).affordable
  );
  state.simulation.pendingBuyOrders = [
    ...orders,
    ...stillValidPending,
    ...(state.simulation.pendingBuyOrders || []).filter((order) => order.status !== "pending").slice(0, 30),
  ].slice(0, 50);

  events.push({
    type: "buy_plan",
    summary: orders.length
      ? `生成次日买入复检计划${orders.length}只：${orders.map((order) => order.name).join("、")}；保留有效旧计划${stillValidPending.length}只`
      : `没有符合条件的次日待买计划；保留有效旧计划${stillValidPending.length}只`,
  });
  orders.forEach((order) => {
    pushDecisionRecord({
      type: "buy_plan_created",
      status: "pending",
      code: order.code,
      name: order.name,
      summary: "20点生成次日买入复检计划",
      reason: order.reason,
      plannedEntryPrice: order.plannedEntryPrice,
      maxBuyPrice: order.maxBuyPrice,
      noChasePrice: order.noChasePrice,
      validUntilDate: order.validUntilDate,
      score: order.score,
    });
  });
}

function isActionableBuySignal(stock) {
  return Boolean(stock?.is_buyable_now || stock?.buy_signal_key === "pullback_buy" || stock?.buy_signal_key === "breakout_buy");
}

function isAfternoonFinalMode(options = {}) {
  return options.mode === "afternoon_final";
}

function afternoonBuyBlockReason(stock) {
  const settings = autoSettings();
  const market = state.data?.universe_scan?.market_environment || {};
  const score = Number(stock?.score || 0);
  const minScore = settings.minScore + settings.afternoonMinScoreBuffer;
  const riskAppetite = Number(market.risk_appetite);
  const fundFlowScore = Number(stock?.fund_flow_score);

  if (!isActionableBuySignal(stock)) return "尾盘只接受明确可买信号";
  if (score < minScore) return `尾盘评分需达到${formatNumber(minScore, 1)}以上`;
  if (stock.market_context_block_buy || stock.entry_safety_block_buy) return "尾盘市场/接入风控拦截";
  if (stock.buy_signal_key === "risk_wait" || stock.buy_signal_key === "avoid" || stock.status_key === "avoid") return "尾盘风险状态不买入";
  if (Number.isFinite(riskAppetite) && riskAppetite < settings.afternoonMinRiskAppetite) {
    return `尾盘市场风险偏好${formatNumber(riskAppetite, 2)}低于阈值${formatNumber(settings.afternoonMinRiskAppetite, 2)}`;
  }
  if (Number.isFinite(fundFlowScore) && fundFlowScore < settings.afternoonMinFundFlowScore) {
    return `尾盘资金流分${formatNumber(fundFlowScore, 2)}低于阈值${formatNumber(settings.afternoonMinFundFlowScore, 2)}`;
  }
  return "";
}

function buildPendingBuyOrder(stock, runKey, options = {}) {
  const actionable = options.actionable ?? isActionableBuySignal(stock);
  const signalDate = currentTradeDate();
  const plannedEntryPrice = actionable
    ? firstFinite(stock.buyable_price, stock.close, stock.recommended_entry_price)
    : firstFinite(stock.recommended_entry_price, stock.buyable_price, stock.close);
  const maxCandidates = actionable
    ? [
        stock.buyable_price_upper,
        stock.breakout_buy_upper_price,
        stock.no_chase_price,
        stock.buyable_price ? Number(stock.buyable_price) * 1.012 : null,
        plannedEntryPrice ? plannedEntryPrice * 1.012 : null,
      ]
    : [
        stock.no_chase_price,
        stock.buyable_price_upper,
        stock.breakout_buy_upper_price,
        plannedEntryPrice ? plannedEntryPrice * 1.03 : null,
        stock.buyable_price ? Number(stock.buyable_price) * 1.012 : null,
      ];
  const maxBuyPrice = Math.min(...maxCandidates.filter((value) => isFiniteNumber(value) && Number(value) > 0).map(Number));
  const safeMaxBuyPrice = Number.isFinite(maxBuyPrice) ? maxBuyPrice : plannedEntryPrice;
  return {
    id: `${stock.code}-${runKey}`,
    code: stock.code,
    name: stock.name,
    status: "pending",
    signalDate,
    signalPhase: options.signalPhase || "evening_watch",
    createdRunKey: options.createdRunKey || runKey,
    plannedExecutionPhase: "morning_entry",
    validUntilDate: options.validUntilDate || buyPlanValidUntilDate(signalDate, autoSettings().buyPlanValidDays),
    plannedEntryPrice,
    maxBuyPrice: safeMaxBuyPrice,
    noChasePrice: firstFinite(stock.no_chase_price),
    score: Number(stock.score || 0),
    buySignalKey: stock.buy_signal_key || "",
    statusKey: stock.status_key || "",
    entrySafetyBlockBuy: Boolean(stock.entry_safety_block_buy),
    reason: `${options.reasonPrefix || "20点计划"}：${stock.buy_signal_label || stock.intervention_status || "模型候选"}`,
  };
}

function executePendingBuyOrders(runKey, events, options = {}) {
  const buyCheckLabel = buyCheckLabelForOptions(options);
  const settings = autoSettings();
  const maxExecutions = isAfternoonFinalMode(options) ? settings.afternoonMaxBuysPerRun : Infinity;
  let executedCount = 0;
  let pendingOrders = (state.simulation.pendingBuyOrders || []).filter(
    (order) => order.status === "pending" && order.plannedExecutionPhase === "morning_entry" && order.createdRunKey !== runKey
  );
  if (!pendingOrders.length) {
    pendingOrders = createMorningFallbackBuyOrders(runKey, events, options);
    if (!pendingOrders.length) {
      events.push({ type: "buy_skip", summary: `${buyCheckLabel}无待买计划，且当前快照无明确可买信号，跳过买入` });
      return;
    }
  }

  pendingOrders.forEach((order) => {
    ensureBuyOrderValidUntil(order);
    if (isBuyOrderExpired(order)) {
      order.status = "expired";
      order.cancelReason = "待买计划超过有效期，自动清理";
      events.push({ type: "expire", code: order.code, summary: `${order.name}待买计划过期` });
      pushDecisionRecord({
        type: "buy_plan_expired",
        status: "expired",
        code: order.code,
        name: order.name,
        summary: `${order.name}待买计划过期`,
        reason: order.cancelReason,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        score: order.score,
      });
      return;
    }

    const affordability = buyOrderAffordability(order, {
      positionPct: isAfternoonFinalMode(options) ? settings.afternoonMaxPositionPct : settings.maxPositionPct,
    });
    if (!affordability.affordable) {
      markOrderCancelled(order, affordability.reason);
      events.push({ type: "buy_cancel", code: order.code, summary: `${order.name}取消买入：${affordability.reason}` });
      pushDecisionRecord({
        type: "buy_cancelled",
        status: "cancelled",
        code: order.code,
        name: order.name,
        summary: `${order.name}取消买入`,
        reason: affordability.reason,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        score: order.score,
      });
      return;
    }

    const snapshot = buyOrderStockSnapshot(order);
    const stock = snapshot.stock;
    const price = latestPriceFor(order.code);
    if (!stock || !price || !snapshot.hasFreshPrice) {
      const reason = !stock
        ? `缺少${buyCheckLabel}执行价格，计划有效至${order.validUntilDate}`
        : `暂缺${buyCheckLabel}当天新价格，计划有效至${order.validUntilDate}`;
      events.push({ type: "buy_wait", code: order.code, summary: `${order.name}继续等待：${reason}` });
      pushDecisionRecord({
        type: "buy_deferred",
        status: "pending",
        code: order.code,
        name: order.name,
        summary: `${order.name}等待下一次买入复检`,
        reason,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        score: order.score,
      });
      return;
    }

    const riskDecision = autoBuyDecisionFromPlan(stock, order, price, options);
    if (!riskDecision.shouldBuy) {
      if (riskDecision.action === "wait") {
        const poolNote = snapshot.inLatestPool ? "" : "；股票暂不在最新池，按原计划继续跟踪";
        events.push({ type: "buy_wait", code: order.code, summary: `${order.name}继续等待：${riskDecision.reason}${poolNote}` });
        pushDecisionRecord({
          type: "buy_deferred",
          status: "pending",
          code: order.code,
          name: order.name,
          summary: `${order.name}等待下一次买入复检`,
          reason: `${riskDecision.reason}${poolNote}`,
          plannedEntryPrice: order.plannedEntryPrice,
          maxBuyPrice: order.maxBuyPrice,
          noChasePrice: order.noChasePrice,
          validUntilDate: order.validUntilDate,
          snapshotPrice: price,
          score: order.score,
        });
        return;
      }
      markOrderCancelled(order, riskDecision.reason);
      events.push({ type: "buy_cancel", code: order.code, summary: `${order.name}取消买入：${riskDecision.reason}` });
      pushDecisionRecord({
        type: "buy_cancelled",
        status: "cancelled",
        code: order.code,
        name: order.name,
        summary: `${order.name}取消买入`,
        reason: riskDecision.reason,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        snapshotPrice: price,
        score: order.score,
      });
      return;
    }

    if (executedCount >= maxExecutions) {
      events.push({ type: "buy_wait", code: order.code, summary: `${order.name}继续等待：${buyCheckLabel}已达到本次买入数量上限` });
      pushDecisionRecord({
        type: "buy_deferred",
        status: "pending",
        code: order.code,
        name: order.name,
        summary: `${order.name}等待下一次买入复检`,
        reason: `${buyCheckLabel}已达到本次买入数量上限`,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        snapshotPrice: price,
        score: order.score,
      });
      return;
    }

    const quantity = autoBuyQuantity(riskDecision.price, {
      positionPct: isAfternoonFinalMode(options) ? settings.afternoonMaxPositionPct : settings.maxPositionPct,
    });
    if (!quantity) {
      markOrderCancelled(order, "可用现金不足或不足一手");
      events.push({ type: "buy_cancel", code: order.code, summary: `${order.name}取消买入：可用现金不足` });
      pushDecisionRecord({
        type: "buy_cancelled",
        status: "cancelled",
        code: order.code,
        name: order.name,
        summary: `${order.name}取消买入`,
        reason: order.cancelReason,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        snapshotPrice: price,
        executionPrice: riskDecision.price,
        score: order.score,
      });
      return;
    }

    const result = buyPosition(stock, riskDecision.price, quantity, { source: "auto", reason: `${buyCheckLabel}验证成交：${order.reason}` });
    if (!result.ok) {
      markOrderCancelled(order, result.message);
      events.push({ type: "buy_cancel", code: order.code, summary: `${order.name}取消买入：${result.message}` });
      pushDecisionRecord({
        type: "buy_cancelled",
        status: "cancelled",
        code: order.code,
        name: order.name,
        summary: `${order.name}取消买入`,
        reason: result.message,
        plannedEntryPrice: order.plannedEntryPrice,
        maxBuyPrice: order.maxBuyPrice,
        noChasePrice: order.noChasePrice,
        validUntilDate: order.validUntilDate,
        snapshotPrice: price,
        executionPrice: riskDecision.price,
        quantity,
        score: order.score,
      });
      return;
    }

    order.status = "executed";
    order.executedAt = runKey;
    order.executionPrice = riskDecision.price;
    executedCount += 1;
    state.simulation.sellPlans[stock.code] = buildSellPlan(state.simulation.positions[stock.code], stock, runKey);
    events.push({
      type: "buy",
      code: stock.code,
      name: stock.name,
      summary: `${buyCheckLabel}买入${stock.name}${result.quantity}股/成交${formatNumber(riskDecision.price)}/费用${formatCurrency(result.fees.total)}`,
    });
    pushDecisionRecord({
      type: "buy_executed",
      status: "executed",
      code: stock.code,
      name: stock.name,
      summary: `${buyCheckLabel}买入${stock.name}${result.quantity}股`,
      reason: riskDecision.reason,
      plannedEntryPrice: order.plannedEntryPrice,
      maxBuyPrice: order.maxBuyPrice,
      noChasePrice: order.noChasePrice,
      validUntilDate: order.validUntilDate,
      snapshotPrice: price,
      executionPrice: riskDecision.price,
      stopLossPrice: result.stopLossPrice,
      takeProfitPrice: result.takeProfitPrice,
      quantity: result.quantity,
      feeTotal: result.fees.total,
      score: order.score,
    });
  });
}

function createMorningFallbackBuyOrders(runKey, events, options = {}) {
  const buyCheckLabel = buyCheckLabelForOptions(options);
  const settings = autoSettings();
  const slots = Math.max(0, settings.maxStocks - Object.keys(state.simulation.positions || {}).length);
  const limit = Math.min(isAfternoonFinalMode(options) ? settings.afternoonMaxBuysPerRun : settings.maxBuysPerRun, slots);
  if (!limit) return [];
  const candidateOrders = [...stocks()]
    .filter((stock) => !state.simulation.positions[stock.code])
    .filter((stock) => Number(stock.score || 0) >= settings.minScore)
    .filter((stock) => isActionableBuySignal(stock))
    .filter((stock) => !stock.entry_safety_block_buy && stock.buy_signal_key !== "risk_wait" && stock.buy_signal_key !== "avoid" && stock.status_key !== "avoid")
    .filter((stock) => !isAfternoonFinalMode(options) || !afternoonBuyBlockReason(stock))
    .sort((a, b) => Number(b.score || 0) - Number(a.score || 0))
    .map((stock) =>
      buildPendingBuyOrder(stock, `${runKey}|morning-fallback`, {
        actionable: true,
        signalPhase: isAfternoonFinalMode(options) ? "afternoon_risk" : "morning_entry",
        reasonPrefix: isAfternoonFinalMode(options) ? buyCheckLabel : `${buyCheckLabel}即时计划`,
      })
    );
  const orders = filterAffordableBuyOrders(candidateOrders, events, `${buyCheckLabel}即时计划`, {
    positionPct: isAfternoonFinalMode(options) ? settings.afternoonMaxPositionPct : settings.maxPositionPct,
  }).slice(0, limit);
  if (!orders.length) return [];

  state.simulation.pendingBuyOrders = [
    ...orders,
    ...(state.simulation.pendingBuyOrders || []).filter((order) => order.status !== "pending").slice(0, 30),
  ].slice(0, 50);
  events.push({ type: "buy_plan", summary: `${buyCheckLabel}无昨晚计划，使用当前快照生成即时待买计划${orders.length}只：${orders.map((order) => order.name).join("、")}` });
  orders.forEach((order) => {
    pushDecisionRecord({
      type: "buy_plan_created",
      status: "pending",
      code: order.code,
      name: order.name,
      summary: `${buyCheckLabel}当前快照生成即时待买计划`,
      reason: order.reason,
      plannedEntryPrice: order.plannedEntryPrice,
      maxBuyPrice: order.maxBuyPrice,
      noChasePrice: order.noChasePrice,
      validUntilDate: order.validUntilDate,
      score: order.score,
    });
  });
  return orders;
}

function autoBuyDecisionFromPlan(stock, order, snapshotPrice, options = {}) {
  const settings = autoSettings();
  const buyCheckLabel = buyCheckLabelForOptions(options);
  if (isAfternoonFinalMode(options)) {
    const blockReason = afternoonBuyBlockReason(stock);
    if (blockReason) return { shouldBuy: false, action: "wait", reason: blockReason };
  }
  if (stock.entry_safety_block_buy || stock.buy_signal_key === "risk_wait" || stock.buy_signal_key === "avoid" || stock.status_key === "avoid") {
    return { shouldBuy: false, action: "cancel", reason: `${buyCheckLabel}模型风控拦截` };
  }
  const noChasePrice = firstFinite(order.noChasePrice, stock.no_chase_price);
  if (isFiniteNumber(noChasePrice) && snapshotPrice > Number(noChasePrice)) {
    return { shouldBuy: false, action: "wait", reason: `${buyCheckLabel}价格超过不追高线${formatNumber(noChasePrice)}` };
  }
  const executionPrice = snapshotPrice * (1 + settings.morningSlippagePct);
  const maxBuyPrice = firstFinite(order.maxBuyPrice, stock.buyable_price_upper, stock.no_chase_price);
  if (isFiniteNumber(maxBuyPrice) && executionPrice > Number(maxBuyPrice)) {
    return { shouldBuy: false, action: "wait", reason: `含滑点成交价${formatNumber(executionPrice)}超过最高可买价${formatNumber(maxBuyPrice)}` };
  }
  return { shouldBuy: true, price: executionPrice, reason: `${buyCheckLabel}快照验证通过` };
}

function markOrderCancelled(order, reason) {
  order.status = "cancelled";
  order.cancelReason = reason;
}

function runMorningRiskReview(events) {
  const buyCheckLabel = currentBuyCheckLabel();
  Object.values(state.simulation.positions || {}).forEach((position) => {
    const stock = stockOrReview(position.code) || { code: position.code, name: position.name };
    const price = latestPriceFor(position.code);
    if (!price) return;
    const decision = autoSellDecision(stock, position, price, "morning_entry");
    if (decision.action === "sell" && decision.critical) {
      executeSellDecision(stock, position, price, decision, events, `${buyCheckLabel}极端风险`);
      return;
    }
    if (decision.action !== "hold") {
      const plan = state.simulation.sellPlans[position.code] || buildSellPlan(position, stock, currentAutoRunKey());
      plan.sellPriority = "must_sell_1430";
      plan.warning = decision.reason;
      plan.lastDecision = `${buyCheckLabel}预警，等待14:30确认`;
      state.simulation.sellPlans[position.code] = plan;
      events.push({ type: "sell_warning", code: position.code, summary: `${position.name}${buyCheckLabel}预警：${decision.reason}，等待14:30确认` });
      pushDecisionRecord({
        type: "sell_warning",
        status: "warning",
        code: position.code,
        name: position.name,
        summary: `${position.name}${buyCheckLabel}卖出预警`,
        reason: decision.reason,
        snapshotPrice: price,
        stopLossPrice: plan.hardStopPrice,
        takeProfitPrice: plan.takeProfitPrice,
        quantity: position.quantity,
      });
    }
  });
}

function executeAfternoonSellPlans(events) {
  let checked = 0;
  Object.values({ ...state.simulation.positions }).forEach((position) => {
    checked += 1;
    const stock = stockOrReview(position.code) || { code: position.code, name: position.name };
    const price = latestPriceFor(position.code);
    if (!price) {
      events.push({ type: "sell_skip", code: position.code, summary: `${position.name}缺少14:30执行价格，暂不卖出` });
      pushDecisionRecord({
        type: "sell_skipped",
        status: "skipped",
        code: position.code,
        name: position.name,
        summary: `${position.name}暂不卖出`,
        reason: "缺少14:30执行价格",
        quantity: position.quantity,
      });
      return;
    }
    const decision = autoSellDecision(stock, position, price, "afternoon_risk");
    if (decision.action === "hold" && state.simulation.sellPlans[position.code]?.sellPriority === "must_sell_1430") {
      decision.action = "sell";
      decision.fraction = 1;
      decision.reason = state.simulation.sellPlans[position.code].warning || "买入复检窗口风险预警延续到14:30";
    }
    if (decision.action === "hold") return;
    executeSellDecision(stock, position, price, decision, events, "14:30执行");
  });
  if (!checked) events.push({ type: "sell_skip", summary: "14:30无持仓，无需卖出" });
  if (checked && !events.some((event) => event.type === "sell")) {
    events.push({ type: "sell_hold", summary: "14:30未触发卖出条件，继续持有" });
  }
}

function executeSellDecision(stock, position, snapshotPrice, decision, events, prefix) {
  const available = availableSellQuantity(position);
  if (!available) {
    events.push({ type: "hold", code: position.code, name: position.name, summary: `${position.name}触发${decision.reason}，但 T+1 暂不可卖` });
    pushDecisionRecord({
      type: "sell_blocked",
      status: "blocked",
      code: position.code,
      name: position.name,
      summary: `${position.name}触发卖出但 T+1 暂不可卖`,
      reason: decision.reason,
      snapshotPrice,
      stopLossPrice: state.simulation.sellPlans?.[position.code]?.hardStopPrice,
      takeProfitPrice: state.simulation.sellPlans?.[position.code]?.takeProfitPrice,
      quantity: position.quantity,
    });
    return;
  }
  const sellPrice = snapshotPrice * (1 - autoSettings().sellSlippagePct);
  const targetQuantity = decision.fraction >= 1 ? available : Math.max(100, normalizeQuantity(available * decision.fraction));
  const quantity = Math.min(available, targetQuantity);
  const result = sellPosition(stock, sellPrice, quantity, { source: "auto", reason: `${prefix}：${decision.reason}` });
  if (result.ok) {
    if (state.simulation.sellPlans[position.code]) {
      state.simulation.sellPlans[position.code].lastDecision = decision.reason;
      if (!state.simulation.positions[position.code]) state.simulation.sellPlans[position.code].status = "executed";
    }
    events.push({
      type: "sell",
      code: stock.code,
      name: stock.name,
      summary: `${prefix}卖出${stock.name}${result.quantity}股/${decision.reason}/成交${formatNumber(sellPrice)}/费用${formatCurrency(result.fees.total)}`,
    });
    pushDecisionRecord({
      type: "sell_executed",
      status: "executed",
      code: stock.code,
      name: stock.name,
      summary: `${prefix}卖出${stock.name}${result.quantity}股`,
      reason: decision.reason,
      snapshotPrice,
      executionPrice: sellPrice,
      stopLossPrice: state.simulation.sellPlans?.[position.code]?.hardStopPrice,
      takeProfitPrice: state.simulation.sellPlans?.[position.code]?.takeProfitPrice,
      quantity: result.quantity,
      feeTotal: result.fees.total,
      realizedPnl: result.realizedPnl,
    });
  }
}

function autoSellDecision(stock, position, price, phase = currentPhaseKey()) {
  const settings = autoSettings();
  const plan = state.simulation.sellPlans?.[position.code] || buildSellPlan(position, stock, currentAutoRunKey());
  const stopLossPrice = firstFinite(plan.hardStopPrice, position.stopLossPrice, ...(position.lots || []).map((lot) => lot.stopLossPrice));
  const takeProfitPrice = firstFinite(plan.takeProfitPrice, position.takeProfitPrice, ...(position.lots || []).map((lot) => lot.takeProfitPrice));
  const highestPrice = Math.max(position.highestPrice || 0, ...(position.lots || []).map((lot) => Number(lot.highestPrice || 0)));
  const trailingStopPrice = highestPrice > 0 ? highestPrice * (1 - settings.trailingStopPct) : null;
  const score = Number(stock.score ?? stock.current_score ?? 10);
  const holdDays = positionHoldDays(position);
  const bookReturnPct = position.costBasis > 0 ? ((price * position.quantity - position.costBasis) / position.costBasis) * 100 : 0;
  const riskFlag = stock.status_key === "avoid" || stock.current_status_key === "avoid" || stock.entry_safety_block_buy;
  const morning = phase === "morning_entry";

  if (isFiniteNumber(stopLossPrice) && price <= Number(stopLossPrice)) {
    const critical = price <= Number(stopLossPrice) * 0.97 || riskFlag || score < settings.exitScoreThreshold;
    if (morning && !critical) return { action: "warn", fraction: 0, reason: `跌破止损价${formatNumber(stopLossPrice)}`, critical: false };
    return { action: "sell", fraction: 1, reason: `跌破止损价${formatNumber(stopLossPrice)}`, critical };
  }
  if (isFiniteNumber(takeProfitPrice) && price >= Number(takeProfitPrice)) {
    if (morning) return { action: "warn", fraction: 0, reason: `触发止盈价${formatNumber(takeProfitPrice)}，等待14:30保护利润`, critical: false };
    return { action: "reduce", fraction: 0.5, reason: `触发止盈价${formatNumber(takeProfitPrice)}，卖出一半`, critical: false };
  }
  if (isFiniteNumber(trailingStopPrice) && highestPrice > averageCost(position) * 1.04 && price <= trailingStopPrice) {
    if (morning) return { action: "warn", fraction: 0, reason: `高点回撤超过${formatPercent(settings.trailingStopPct * 100, 1)}`, critical: false };
    return { action: "sell", fraction: 1, reason: `高点回撤超过${formatPercent(settings.trailingStopPct * 100, 1)}`, critical: false };
  }
  if (riskFlag || score < settings.exitScoreThreshold) {
    if (morning && score >= settings.exitScoreThreshold) return { action: "warn", fraction: 0, reason: "模型风险退出预警", critical: false };
    return { action: "sell", fraction: 1, reason: `模型风险退出/评分${formatNumber(score, 1)}`, critical: true };
  }
  if (score < settings.reduceScoreThreshold) {
    if (morning) return { action: "warn", fraction: 0, reason: `评分降至${formatNumber(score, 1)}，14:30考虑减仓`, critical: false };
    return { action: "reduce", fraction: 0.5, reason: `评分降至${formatNumber(score, 1)}，减仓一半`, critical: false };
  }
  if (holdDays >= settings.maxHoldDays && bookReturnPct < 2) {
    if (morning) return { action: "warn", fraction: 0, reason: `持仓${holdDays}天收益未达标，14:30考虑退出`, critical: false };
    return { action: "sell", fraction: 1, reason: `持仓${holdDays}天收益未达标`, critical: false };
  }
  return { action: "hold", fraction: 0, reason: "" };
}

function autoBuyQuantity(price, options = {}) {
  const snapshot = portfolioSnapshot();
  const settings = autoSettings();
  const positionPct = isFiniteNumber(options.positionPct) ? Number(options.positionPct) : settings.maxPositionPct;
  const grossLimit = Math.min(state.simulation.cash, snapshot.totalAssets * positionPct);
  let quantity = normalizeQuantity(grossLimit / price);
  while (quantity > 0) {
    const grossAmount = quantity * price;
    const totalCost = grossAmount + calculateTradeFees("buy", grossAmount).total;
    if (totalCost <= state.simulation.cash + 0.0001) return quantity;
    quantity -= 100;
  }
  return 0;
}

function firstFinite(...values) {
  const value = values.find((item) => isFiniteNumber(item) && Number(item) > 0);
  return value === undefined ? null : Number(value);
}

function positionHoldDays(position) {
  const dates = (position.lots || []).map((lot) => lot.tradeDate).filter(Boolean).sort();
  const firstDate = dates[0];
  if (!firstDate) return 0;
  const start = new Date(`${firstDate}T00:00:00`);
  const end = new Date(`${currentTradeDate()}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 0;
  return Math.max(0, Math.floor((end - start) / 86400000));
}

function pushDecisionRecord(record) {
  const phase = currentPhaseKey();
  state.simulation.decisionJournal = normalizeDecisionJournal(state.simulation.decisionJournal);
  state.simulation.decisionJournal.unshift({
    id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
    at: new Date().toISOString(),
    tradeDate: currentTradeDate(),
    phase: state.data?.universe_scan?.update_phase_label || phaseLabel(phase),
    phaseKey: phase,
    ...record,
  });
  state.simulation.decisionJournal = state.simulation.decisionJournal.slice(0, 100);
}

function pushAutoLog(runKey, phase, events) {
  state.simulation.autoLog = state.simulation.autoLog || [];
  state.simulation.autoLog.unshift({
    runKey,
    at: new Date().toISOString(),
    tradeDate: currentTradeDate(),
    phase: state.data?.universe_scan?.update_phase_label || phaseLabel(phase),
    phaseKey: phase,
    events,
  });
  state.simulation.autoLog = state.simulation.autoLog.slice(0, 50);
}

async function loadPool(options = {}) {
  const { silent = false, reason = "manual" } = options;
  const isManualRefresh = reason === "manual";
  if (state.refreshInFlight) {
    if (isManualRefresh && !silent) setSimulationMessage("正在更新，请稍候。", "info");
    return { ok: false, skipped: true };
  }
  if (isManualRefresh) setManualRefreshButton(true, "更新中...");
  state.refreshInFlight = true;
  const previousRunKey = currentAutoRunKey();
  try {
    const response = await fetch(`data/latest.json?t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const nextData = await response.json();
    const nextRunKey = autoRunKeyForData(nextData);
    const changed = Boolean(nextRunKey && nextRunKey !== previousRunKey);
    state.data = nextData;
    state.review = state.data.review || null;
    try {
      const reviewResponse = await fetch(`data/review.json?t=${Date.now()}`, { cache: "no-store" });
      if (reviewResponse.ok) {
        state.review = await reviewResponse.json();
      }
    } catch (_) {
      state.review = state.data.review || null;
    }
    state.lastRefreshCheckAt = new Date().toISOString();
    state.lastRefreshError = "";
    state.lastRefreshStatus = changed
      ? `发现新快照：${state.data.universe_scan?.update_phase_label || state.data.generated_at || state.data.as_of_date || "-"}`
      : `${refreshReasonLabel(reason)}：暂无新快照`;
    render();
    if (silent && changed) {
      setSimulationMessage(`自动刷新发现新数据：${state.data.universe_scan?.update_phase_label || state.data.generated_at || state.data.as_of_date || "-"}`, "success");
    }
    if (!silent) {
      setSimulationMessage(
        changed
          ? `已更新到最新快照：${state.data.universe_scan?.update_phase_label || state.data.generated_at || state.data.as_of_date || "-"}`
          : state.lastRefreshStatus,
        changed ? "success" : "info"
      );
    }
    return { ok: true, changed, runKey: nextRunKey };
  } catch (error) {
    state.lastRefreshCheckAt = new Date().toISOString();
    state.lastRefreshError = error.message;
    state.lastRefreshStatus = `刷新失败：${error.message}`;
    if (!silent) {
      byId("stockList").innerHTML = `<article class="stock-card"><div class="detail"><p class="logic">数据加载失败：${escapeHtml(
        error.message
      )}</p></div></article>`;
    }
    renderSimulationPanel();
    return { ok: false, error };
  } finally {
    state.refreshInFlight = false;
    if (isManualRefresh) setManualRefreshButton(false);
  }
}

function render() {
  const { data } = state;
  if (!data) return;

  byId("asOfDate").textContent = formatDataMinute(data.generated_at, data.as_of_date || "-");
  byId("poolCount").textContent = String(data.stocks?.length || 0);
  byId("universeScope").textContent = data.universe_scan?.mainboard_count
    ? `${data.universe_scan.mainboard_count}只→${data.universe_scan.deep_analysis_count || 0}只`
    : "-";
  byId("overallSignal").textContent = data.summary?.overall_signal || "-";
  const averageReturn = state.review?.summary?.average_return_pct ?? data.summary?.tracking?.average_return_pct;
  byId("averageReturn").textContent = formatPercent(averageReturn);
  byId("averageReturn").className = returnClass(averageReturn);
  byId("modelDescription").textContent = data.model?.description || byId("modelDescription").textContent;

  renderModelStatus(data);
  renderStockList();
  runAutoStrategy({ quiet: true });
  renderSimulationPanel();
  if (state.autoRunMessage) setSimulationMessage(state.autoRunMessage, "info");
  renderReviewCenter();
}

function renderModelStatus(data) {
  const feedback = data.model_feedback || {};
  const entryFeedback = feedback.entry_effectiveness || {};
  const marketEnvironment = data.universe_scan?.market_environment || {};
  const segmentation = feedback.segmentation || {};
  const concentration = data.portfolio_concentration || {};
  const topThemes = data.universe_scan?.theme_strength?.top_groups || [];
  const topThemeText = topThemes.length
    ? `；强主题：${topThemes
        .slice(0, 3)
        .map((item) => `${item.theme_group}/${item.label}`)
        .join("、")}`
    : "";
  const phaseText = data.universe_scan?.update_phase_label ? `；更新时间段：${data.universe_scan.update_phase_label}` : "";
  const freshnessText = marketFreshnessNote(data);
  const exposureText = data.summary?.theme_exposure
    ? `；最终主题分布：${Object.entries(data.summary.theme_exposure)
        .map(([key, value]) => `${key}${value}`)
        .join("、")}`
    : "";
  const concentrationText = concentration.schema_version
    ? `；组合拥挤度：${concentration.penalized_count ?? 0}只候选被轻微降权`
    : "";

  byId("feedbackStatus").textContent = feedback.schema_version
    ? `反馈模型：${feedback.confidence || "低"}置信；样本 ${feedback.observation_count ?? 0} 条；因子 ${
        feedback.summary?.factor_count ?? 0
      } 个；单股修正上限 ±${formatNumber(feedback.score_cap, 2)} 分。${feedback.summary?.note || ""}`
    : "反馈模型：等待历史样本积累。";

  byId("sourceStatus").textContent = `更新时间：${data.generated_at || "-"}${phaseText}；市场温度：${
    marketEnvironment.label || "-"
  } / ${formatSignedNumber(marketEnvironment.temperature_score, 2)}${freshnessText}；${marketEnvironment.note || ""}${topThemeText}${exposureText}${concentrationText}；数据源：${
    data.source_status?.quotes || "-"
  }；${data.source_status?.note || ""}`;

  if (feedback.schema_version && entryFeedback.schema_version) {
    byId("feedbackStatus").textContent += ` 接入有效性：样本 ${entryFeedback.observation_count ?? 0} 条；触达 ${
      entryFeedback.touched_observation_count ?? 0
    } 条；未触达等待 ${entryFeedback.untouched_wait_observation_count ?? 0} 条；接入风险标记 ${
      data.summary?.entry_risk_flagged ?? 0
    } 只；可买拦截 ${data.summary?.buy_signal_blocked ?? data.summary?.risk_gated ?? 0} 只；市场降级 ${
      data.summary?.market_context_blocked ?? 0
    } 只；安全因子 ${entryFeedback.summary?.factor_count ?? 0} 个。${entryFeedback.summary?.note || ""}`;
    const marketSegments = segmentation.market_regime_counts
      ? Object.entries(segmentation.market_regime_counts)
          .map(([key, value]) => `${key}:${value}`)
          .join("、")
      : "";
    const phaseSegments = segmentation.update_phase_counts
      ? Object.entries(segmentation.update_phase_counts)
          .map(([key, value]) => `${key}:${value}`)
          .join("、")
      : "";
    byId("feedbackStatus").textContent += ` 分层反馈：市场[${marketSegments || "-"}]；时段[${phaseSegments || "-"}]。`;
  }
}

function renderStockList() {
  const filteredStocks = stocks().filter((stock) => {
    if (state.filter === "all") return true;
    if (state.filter === "buyable") return Boolean(stock.is_buyable_now);
    return stock.status_key === state.filter;
  });

  const list = byId("stockList");
  list.innerHTML = "";

  filteredStocks.forEach((stock) => {
    list.appendChild(createStockCard(stock));
  });

  if (!filteredStocks.length) {
    list.innerHTML = '<article class="stock-card"><div class="detail"><p class="logic">当前筛选下没有股票。</p></div></article>';
  }
}

function createStockCard(stock) {
  const template = byId("stockCardTemplate");
  const node = template.content.firstElementChild.cloneNode(true);
  const status = statusMap[stock.status_key] || { label: stock.intervention_status || "观察", className: "" };
  const tracking = stock.tracking || {};
  const trackingReturn = tracking.return_since_first_pct;

  node.querySelector(".stock-name").textContent = stock.name;
  node.querySelector(".stock-code").textContent = `${stock.code} · ${stock.board || "主板"}`;
  node.querySelector(".status-pill").textContent = status.label;
  node.querySelector(".status-pill").classList.add(status.className);
  node.querySelector(".close-price").textContent = formatNumber(stock.close);
  node.querySelector(".buy-signal").textContent = stock.buy_signal_label || "等待触发";
  node.querySelector(".buy-signal").classList.add(buySignalClass(stock.buy_signal_key));
  node.querySelector(".buy-price").textContent = formatBuyPrice(stock);
  node.querySelector(".fund-flow").textContent = stock.fund_flow_label || "资金流暂缺";
  node.querySelector(".fund-flow").classList.add(fundFlowClass(stock.fund_flow_score));
  node.querySelector(".chip-status").textContent = stock.chip_label || "筹码暂缺";
  node.querySelector(".chip-status").classList.add(chipClass(stock.chip_score));
  node.querySelector(".feedback-bonus").textContent = formatSignedNumber(stock.feedback_bonus, 3);
  node.querySelector(".feedback-bonus").classList.add(feedbackClass(stock.feedback_bonus));
  node.querySelector(".entry-price").textContent = formatNumber(stock.recommended_entry_price);
  node.querySelector(".breakout-price").textContent = formatNumber(stock.breakout_confirm_price);
  node.querySelector(".watch-zone").textContent = stock.watch_zone || "-";
  node.querySelector(".no-chase").textContent = formatNumber(stock.no_chase_price);
  node.querySelector(".tracking-return").textContent = formatPercent(trackingReturn);
  node.querySelector(".tracking-return").classList.add(returnClass(trackingReturn));
  node.querySelector(".score").textContent = formatNumber(stock.score, 1);
  node.querySelector(".logic").textContent = stock.logic || "";
  node.querySelector(".theme").textContent = `${stock.theme || "-"}；主题强度 ${
    stock.theme_strength_label || "-"
  } / ${formatNumber(stock.theme_strength_score)}；市场 ${stock.market_temperature_label || "-"}；组合降权 ${formatSignedNumber(
    stock.portfolio_concentration_penalty,
    3
  )}`;
  node.querySelector(".layer-one").textContent = stock.layer_one_rank
    ? `全主板第 ${stock.layer_one_rank} 名，初筛分 ${formatNumber(stock.layer_one_score)}，当日涨跌 ${formatPercent(
        stock.layer_one_pct_chg
      )}，来源：${stock.candidate_source || "-"}`
    : `未进入全主板快照初筛，来源：${stock.candidate_source || "-"}`;
  node.querySelector(".buy-detail").textContent = stock.is_buyable_now
    ? `${stock.buy_signal_label || "可买入观察"}：路径 ${stock.buy_price_path || "-"}，可买价 ${formatNumber(
        stock.buyable_price
      )}，可买区间 ${formatNumber(stock.buyable_price_lower)}-${formatNumber(stock.buyable_price_upper)}。${
        stock.buy_price_note || ""
      }`
    : `${stock.buy_signal_label || "等待触发"}：下一个触发价 ${formatNumber(stock.next_buy_trigger_price)}，路径 ${
        stock.buy_price_path || "-"
      }。${stock.buy_price_note || ""}`;
  node.querySelector(".fund-detail").textContent = `${stock.fund_flow_label || "资金流暂缺"}：今日主力 ${formatFundMoney(
    stock.fund_today_main_net
  )} / ${formatPercent(stock.fund_today_main_net_pct)}，5日主力 ${formatFundMoney(
    stock.fund_5d_main_net
  )} / ${formatPercent(stock.fund_5d_main_net_pct)}，资金分 ${formatNumber(stock.fund_flow_score)}，模型加减分 ${formatNumber(
    stock.fund_flow_bonus
  )}。资金流只作趋势质量验证。`;
  node.querySelector(".chip-detail").textContent = `${stock.chip_label || "筹码暂缺"}：获利比例 ${formatPercent(
    stock.chip_profit_ratio
  )}，平均成本 ${formatNumber(stock.chip_avg_cost)}，现价偏离平均成本 ${formatPercent(
    stock.chip_cost_gap_pct
  )}，70%集中度 ${formatPercent(stock.chip_concentration_70)}，90%集中度 ${formatPercent(
    stock.chip_concentration_90
  )}，筹码分 ${formatNumber(stock.chip_score)}，模型加减分 ${formatNumber(stock.chip_bonus)}。${
    stock.chip_note || "筹码只作成本结构与兑现压力验证。"
  } 来源：${stock.chip_source || "-"}。`;
  node.querySelector(".feedback-detail").textContent = `${stock.feedback_label || "回访样本不足"}：反馈分 ${formatSignedNumber(
    stock.feedback_bonus,
    3
  )}，整体置信 ${stock.feedback_confidence || "低"}。${formatFeedbackFactors(stock)}。${formatPriceFeedback(stock)} ${
    stock.portfolio_concentration_note || ""
  } ${formatEntrySafety(stock)}`;
  node.querySelector(".entry-detail").textContent = `推荐接入价 ${formatNumber(
    stock.recommended_entry_price
  )}，接入区间 ${formatNumber(stock.entry_price_lower)}-${formatNumber(stock.entry_price_upper)}，现价偏离 ${formatPercent(
    stock.entry_gap_pct
  )}。原接入价 ${formatNumber(stock.base_recommended_entry_price)}。${stock.entry_price_note || ""} ${formatEntrySafety(stock)}`;
  node.querySelector(".breakout-detail").textContent = `突破确认价 ${formatNumber(
    stock.breakout_confirm_price
  )}，前高压力 ${formatNumber(stock.resistance_price)}，距现价 ${formatPercent(stock.breakout_gap_pct)}。${
    stock.breakout_price_note || ""
  }`;
  node.querySelector(".first-recommend").textContent = tracking.first_recommend_date
    ? `${tracking.first_recommend_date}，首次价 ${formatNumber(tracking.first_recommend_price)}，已回访 ${
        tracking.tracking_days ?? 0
      } 天`
    : "等待下一次自动刷新后开始记录。";
  node.querySelector(".tracking-detail").textContent = tracking.first_recommend_date
    ? `${tracking.status || "继续观察"}：累计 ${formatPercent(tracking.return_since_first_pct)}，最高 ${formatPercent(
        tracking.max_return_since_first_pct
      )}，距高点 ${formatPercent(tracking.drawdown_from_peak_pct)}。${tracking.comment || ""}`
    : "暂无历史推荐快照。";
  node.querySelector(".trigger").textContent = stock.trigger_condition || "-";
  node.querySelector(".position").textContent = stock.position_hint || "-";
  node.querySelector(".catalysts").textContent = (stock.catalysts || []).join("、") || "-";
  node.querySelector(".risks").textContent = (stock.risks || []).join("、") || "-";

  const detail = node.querySelector(".detail");
  node.querySelector(".card-head").addEventListener("click", () => {
    detail.hidden = !detail.hidden;
  });
  node.querySelector(".trade-pick-button").addEventListener("click", () => {
    selectStockForSimulation(stock);
  });

  return node;
}

function renderSimulationPanel() {
  syncSimulationSelect();
  const snapshot = portfolioSnapshot();

  byId("simulationBadge").textContent = `初始 ${formatCurrency(state.simulation.initialCash, 0)} 元`;
  byId("simTotalAssets").textContent = formatCurrency(snapshot.totalAssets);
  byId("simCash").textContent = formatCurrency(state.simulation.cash);
  byId("simMarketValue").textContent = formatCurrency(snapshot.marketValue);
  byId("simReturn").textContent = `${formatCurrency(snapshot.totalReturn)} / ${formatPercent(snapshot.totalReturnPct)}`;
  byId("simReturn").className = returnClass(snapshot.totalReturn);
  byId("simLiquidationReturn").textContent = `${formatCurrency(snapshot.liquidationReturn)} / ${formatPercent(snapshot.liquidationReturnPct)}`;
  byId("simLiquidationReturn").className = returnClass(snapshot.liquidationReturn);
  byId("simPositionCount").textContent = String(snapshot.positions.length);
  byId("simTotalFees").textContent = formatCurrency(totalTradeFees());
  byId("simAutoStatus").textContent = autoSettings().enabled ? `已启用 · ${phaseLabel()}` : "已关闭";

  renderAutoStrategyControls();
  renderSimulationPositions(snapshot.positions);
  renderSimulationTrades();
  renderSimulationPlans();
  renderDecisionJournal();
  renderAutoLog();
}

function syncSimulationSelect() {
  const select = byId("simulationStockSelect");
  if (!select) return;
  const current = state.simulation.selectedCode || select.value || stocks()[0]?.code || "";
  select.innerHTML = stocks()
    .map((stock) => `<option value="${escapeHtml(stock.code)}">${escapeHtml(stock.name)} · ${escapeHtml(stock.code)}</option>`)
    .join("");
  const nextCode = stocks().some((stock) => stock.code === current) ? current : stocks()[0]?.code || "";
  state.simulation.selectedCode = nextCode;
  select.value = nextCode;
  if (nextCode && !byId("simulationPriceInput").value) {
    const stock = findStock(nextCode);
    const price = suggestedTradePrice(stock);
    if (price) byId("simulationPriceInput").value = formatNumber(price);
  }
}

function renderAutoStrategyControls() {
  const settings = autoSettings();
  const fees = feeSettings();
  byId("autoStrategyEnabled").checked = settings.enabled;
  byId("autoMaxPositionPct").value = formatNumber(settings.maxPositionPct * 100, 0);
  byId("autoMaxStocks").value = String(settings.maxStocks);
  byId("autoMinScore").value = formatNumber(settings.minScore, 1);
  byId("autoBuyPlanValidDays").value = String(settings.buyPlanValidDays);
  byId("autoStopLossPct").value = formatNumber(settings.stopLossPct * 100, 1);
  byId("autoTakeProfitPct").value = formatNumber(settings.takeProfitPct * 100, 1);
  byId("autoTrailingStopPct").value = formatNumber(settings.trailingStopPct * 100, 1);
  byId("feeCommissionRate").value = formatNumber(fees.commissionRate * 100, 3);
  byId("feeMinCommission").value = formatNumber(fees.minCommission, 1);
  byId("feeStampDutyRate").value = formatNumber(fees.stampDutyRate * 100, 3);
  byId("feeTransferRate").value = formatNumber(fees.transferFeeRate * 100, 4);
  const refreshText = `页面自动刷新：开启，每${AUTO_REFRESH_INTERVAL_LABEL}检查一次；最近检查 ${formatRefreshTime(
    state.lastRefreshCheckAt
  )}，${state.lastRefreshStatus || "等待检查"}。页面关闭或被手机系统挂起后不会后台运行。`;
  byId("autoStrategyNote").textContent = `最近策略检查：${
    state.simulation.lastAutoRunKey ? state.simulation.lastAutoRunKey.split("|")[0] : "尚未执行"
  }。模型按离散快照执行：待买计划默认有效${settings.buyPlanValidDays}天；每个快照同时观察买入和卖出；10:00/11:20/13:30正常复检买入并预警卖出，14:30先执行卖出/风控，再用更高门槛、小仓位做最后买入复检，20点复盘续订计划。${refreshText}账面收益只扣已发生费用；清仓后收益会额外扣除预计卖出费用。`;
}

function renderSimulationPositions(positions) {
  const container = byId("simulationPositions");
  if (!positions.length) {
    container.innerHTML = '<p class="empty-text">暂无模拟持仓。可以从股票卡片点击“用此价模拟买入”，或在上方手动选择股票。</p>';
    return;
  }

  container.innerHTML = positions
    .sort((a, b) => b.marketValue - a.marketValue)
    .map(
      (position) => `
        <article class="simulation-row">
          <div>
            <strong>${escapeHtml(position.name)}</strong>
            <em>${escapeHtml(position.code)} · 可卖 ${position.availableQuantity} 股</em>
          </div>
          <div>
            <span>数量 / 成本</span>
            <strong>${position.quantity} / ${formatNumber(position.avgCost)}</strong>
          </div>
          <div>
            <span>最新价 / 市值</span>
            <strong>${formatNumber(position.latestPrice)} / ${formatCurrency(position.marketValue)}</strong>
          </div>
          <div>
            <span>账面浮动收益</span>
            <strong class="${returnClass(position.bookUnrealized)}">${formatCurrency(position.bookUnrealized)} / ${formatPercent(position.bookUnrealizedPct)}</strong>
            <em>清仓后 ${formatCurrency(position.liquidationUnrealized)} / ${formatPercent(position.liquidationUnrealizedPct)} · 止损 ${formatNumber(position.stopLossPrice)} · 止盈 ${formatNumber(position.takeProfitPrice)}</em>
          </div>
        </article>
      `
    )
    .join("");
}

function renderSimulationTrades() {
  const container = byId("simulationTrades");
  const trades = state.simulation.trades || [];
  if (!trades.length) {
    container.innerHTML = '<p class="empty-text">暂无成交记录。模拟成交只保存在当前浏览器，不会同步到真实账户。</p>';
    return;
  }

  container.innerHTML = trades
    .slice(0, 20)
    .map((trade) => {
      const isBuy = trade.type === "buy";
      const pnlText = isBuy || !isFiniteNumber(trade.realizedPnl) ? "" : ` · 已实现 ${formatCurrency(trade.realizedPnl)}`;
      const feeText = trade.fees?.total ? ` · 费用 ${formatCurrency(trade.fees.total)}` : "";
      const sourceText = trade.source === "auto" ? "自动" : "手动";
      return `
        <article class="simulation-row compact">
          <div>
            <strong class="${isBuy ? "buy-now" : "buy-avoid"}">${sourceText}${isBuy ? "买入" : "卖出"} ${escapeHtml(trade.name)}</strong>
            <em>${escapeHtml(trade.tradeDate || "-")} · ${escapeHtml(trade.code)} · ${escapeHtml(trade.reason || "")}</em>
          </div>
          <div>
            <span>价格 / 数量</span>
            <strong>${formatNumber(trade.price)} / ${trade.quantity}</strong>
          </div>
          <div>
            <span>成交额 / 费用</span>
            <strong>${formatCurrency(trade.amount)}${feeText}${pnlText}</strong>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderSimulationPlans() {
  const container = byId("simulationPlans");
  if (!container) return;
  const pendingOrders = (state.simulation.pendingBuyOrders || []).filter((order) => order.status === "pending");
  const sellPlans = Object.values(state.simulation.sellPlans || {}).filter((plan) => plan.status === "pending");

  if (!pendingOrders.length && !sellPlans.length) {
    container.innerHTML = '<p class="empty-text">暂无待执行计划。20点快照会生成次日买入复检计划，并为持仓生成14:30卖出计划。</p>';
    return;
  }

  const buyRows = pendingOrders.map(
    (order) => {
      ensureBuyOrderValidUntil(order);
      return `
        <article class="simulation-row compact">
          <div>
            <strong>待买 ${escapeHtml(order.name)}</strong>
            <em>${escapeHtml(order.code)} · ${escapeHtml(order.reason || "20点计划")}</em>
          </div>
          <div>
            <span>计划/最高买入价</span>
            <strong>${formatNumber(order.plannedEntryPrice)} / ${formatNumber(order.maxBuyPrice)}</strong>
          </div>
          <div>
            <span>执行窗口</span>
            <strong>10:00/11:20/13:30复检，14:30严格复检 · 有效至${escapeHtml(order.validUntilDate || "-")}</strong>
          </div>
        </article>
      `;
    }
  );
  const sellRows = sellPlans.map(
    (plan) => `
      <article class="simulation-row compact">
        <div>
          <strong>待卖检查 ${escapeHtml(plan.name)}</strong>
          <em>${escapeHtml(plan.code)} · ${escapeHtml(plan.warning || plan.lastDecision || "常规风控")}</em>
        </div>
        <div>
          <span>硬止损 / 止盈</span>
          <strong>${formatNumber(plan.hardStopPrice)} / ${formatNumber(plan.takeProfitPrice)}</strong>
        </div>
        <div>
          <span>执行窗口</span>
          <strong>${plan.sellPriority === "must_sell_1430" ? "14:30优先处理" : "14:30检查"}</strong>
        </div>
      </article>
    `
  );
  container.innerHTML = [...buyRows, ...sellRows].join("");
}

function decisionMeta(record) {
  const map = {
    buy_plan_created: { label: "生成待买", className: "buy-wait" },
    buy_plan_expired: { label: "计划过期", className: "buy-wait" },
    buy_deferred: { label: "继续等待", className: "buy-wait" },
    buy_executed: { label: "执行买入", className: "buy-now" },
    buy_cancelled: { label: "取消买入", className: "buy-avoid" },
    sell_warning: { label: "卖出预警", className: "buy-wait" },
    sell_blocked: { label: "卖出受限", className: "buy-wait" },
    sell_skipped: { label: "卖出跳过", className: "buy-wait" },
    sell_executed: { label: "执行卖出", className: "buy-avoid" },
  };
  return map[record.type] || { label: "策略记录", className: "buy-wait" };
}

function labeledNumber(label, value, formatter = formatNumber) {
  const formatted = formatter(value);
  return formatted === "-" ? "" : `${label}${formatted}`;
}

function decisionPriceText(record) {
  const buyParts = [
    labeledNumber("计划", record.plannedEntryPrice),
    labeledNumber("上限", record.maxBuyPrice),
    labeledNumber("不追高", record.noChasePrice),
    record.validUntilDate ? `有效至${record.validUntilDate}` : "",
    labeledNumber("快照", record.snapshotPrice),
    labeledNumber("执行", record.executionPrice),
  ].filter(Boolean);
  const sellParts = [
    labeledNumber("止损", record.stopLossPrice),
    labeledNumber("止盈", record.takeProfitPrice),
    labeledNumber("快照", record.snapshotPrice),
    labeledNumber("执行", record.executionPrice),
  ].filter(Boolean);
  return record.type?.startsWith("sell") ? sellParts.join(" / ") : buyParts.join(" / ");
}

function decisionResultText(record) {
  const parts = [record.summary].filter(Boolean);
  if (isFiniteNumber(record.quantity)) parts.push(`${record.quantity}股`);
  if (isFiniteNumber(record.feeTotal)) parts.push(`费用${formatCurrency(record.feeTotal)}`);
  if (isFiniteNumber(record.realizedPnl)) parts.push(`实现${formatCurrency(record.realizedPnl)}`);
  if (isFiniteNumber(record.score)) parts.push(`评分${formatNumber(record.score, 1)}`);
  return parts.join(" · ") || "-";
}

function renderDecisionJournal() {
  const container = byId("simulationDecisionJournal");
  if (!container) return;
  const allRecords = normalizeDecisionJournal(state.simulation.decisionJournal);
  state.simulation.decisionJournal = allRecords;
  const records = allRecords.slice(0, 20);
  if (!records.length) {
    container.innerHTML = '<p class="empty-text">暂无决策复盘。20点生成计划、买入复检、14:30卖出执行后会在这里留下原因和价格纪律。</p>';
    return;
  }
  container.innerHTML = records
    .map((record) => {
      const meta = decisionMeta(record);
      const priceText = decisionPriceText(record) || "-";
      return `
        <article class="simulation-row compact">
          <div>
            <strong class="${meta.className}">${meta.label} ${escapeHtml(record.name || record.code || "")}</strong>
            <em>${escapeHtml(record.tradeDate || "-")} · ${escapeHtml(record.code || "-")} · ${escapeHtml(record.phase || "")}</em>
          </div>
          <div>
            <span>价格纪律</span>
            <strong>${escapeHtml(priceText)}</strong>
          </div>
          <div>
            <span>原因 / 结果</span>
            <strong>${escapeHtml(decisionResultText(record))}</strong>
            <em>${escapeHtml(record.reason || "")}</em>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderAutoLog() {
  const container = byId("simulationAutoLog");
  const logs = state.simulation.autoLog || [];
  if (!logs.length) {
    container.innerHTML = '<p class="empty-text">暂无自动策略执行记录。启用后，每次读取到新的数据快照会自动检查一次。</p>';
    return;
  }
  container.innerHTML = logs
    .slice(0, 20)
    .map((log) => {
      const eventText = log.events?.length ? log.events.map((event) => event.summary).join("；") : "未触发交易";
      return `
        <article class="simulation-row compact">
          <div>
            <strong>${escapeHtml(log.tradeDate || "-")} · ${escapeHtml(log.phase || "自动检查")}</strong>
            <em>${escapeHtml(log.runKey || "")}</em>
          </div>
          <div>
            <span>动作</span>
            <strong>${escapeHtml(eventText)}</strong>
          </div>
          <div>
            <span>执行时间</span>
            <strong>${escapeHtml(new Date(log.at).toLocaleString("zh-CN"))}</strong>
          </div>
        </article>
      `;
    })
    .join("");
}

function selectedTradeStock() {
  const code = byId("simulationStockSelect").value;
  return findStock(code);
}

function selectStockForSimulation(stock) {
  state.simulation.selectedCode = stock.code;
  const price = suggestedTradePrice(stock);
  byId("simulationStockSelect").value = stock.code;
  if (price) byId("simulationPriceInput").value = formatNumber(price);
  byId("simulationQuantityInput").value = "100";
  saveSimulation();
  setSimulationMessage(`已带入 ${stock.name}，模拟价 ${price ? formatNumber(price) : "-"}。`, "info");
  byId("simulationPanel").scrollIntoView({ behavior: "smooth", block: "start" });
}

function handleSimulationBuy() {
  const stock = selectedTradeStock();
  if (!stock) {
    setSimulationMessage("请先选择一只股票。", "error");
    return;
  }
  const price = Number(byId("simulationPriceInput").value);
  const quantity = normalizeQuantity(byId("simulationQuantityInput").value);
  if (!isFiniteNumber(price) || price <= 0) {
    setSimulationMessage("请输入有效的模拟价格。", "error");
    return;
  }
  if (!quantity) {
    setSimulationMessage("买入数量需至少 100 股，并按 100 股整数手模拟。", "error");
    return;
  }
  byId("simulationQuantityInput").value = String(quantity);
  const result = buyPosition(stock, price, quantity, { source: "manual", reason: "手动模拟买入" });
  if (!result.ok) {
    setSimulationMessage(result.message, "error");
    return;
  }
  saveSimulation();
  renderSimulationPanel();
  setSimulationMessage(
    `已模拟买入 ${stock.name} ${result.quantity} 股，成交额 ${formatCurrency(result.grossAmount)} 元，费用 ${formatCurrency(
      result.fees.total
    )} 元，止损 ${formatNumber(result.stopLossPrice)}，止盈 ${formatNumber(result.takeProfitPrice)}。`,
    "success"
  );
}

function handleSimulationSell() {
  const stock = selectedTradeStock();
  if (!stock) {
    setSimulationMessage("请先选择一只股票。", "error");
    return;
  }
  const position = state.simulation.positions[stock.code];
  if (!position || !position.quantity) {
    setSimulationMessage("当前没有这只股票的模拟持仓。", "error");
    return;
  }
  const price = Number(byId("simulationPriceInput").value);
  const quantity = normalizeQuantity(byId("simulationQuantityInput").value);
  if (!isFiniteNumber(price) || price <= 0) {
    setSimulationMessage("请输入有效的模拟价格。", "error");
    return;
  }
  if (!quantity) {
    setSimulationMessage("卖出数量需至少 100 股，并按 100 股整数手模拟。", "error");
    return;
  }
  const result = sellPosition(stock, price, quantity, { source: "manual", reason: "手动模拟卖出" });
  if (!result.ok) {
    setSimulationMessage(result.message, "error");
    return;
  }
  saveSimulation();
  renderSimulationPanel();
  setSimulationMessage(
    `已模拟卖出 ${stock.name} ${result.quantity} 股，成交额 ${formatCurrency(result.grossAmount)} 元，费用 ${formatCurrency(
      result.fees.total
    )} 元，已实现收益 ${formatCurrency(result.realizedPnl)} 元。`,
    "success"
  );
}

function resetSimulation() {
  if (!window.confirm("确认重置模拟账户？持仓和成交记录都会清空，初始资金恢复为 100000 元。")) return;
  state.simulation = createDefaultSimulation();
  saveSimulation();
  renderSimulationPanel();
  setSimulationMessage("模拟账户已重置。", "success");
}

function renderReviewCenter() {
  const review = state.review;
  const list = byId("reviewList");
  const records = [...(review?.records || [])].sort((a, b) => {
    const aReturn = Number.isFinite(Number(a.return_since_first_pct)) ? Number(a.return_since_first_pct) : -Infinity;
    const bReturn = Number.isFinite(Number(b.return_since_first_pct)) ? Number(b.return_since_first_pct) : -Infinity;
    if (bReturn !== aReturn) return bReturn - aReturn;
    return String(a.code).localeCompare(String(b.code));
  });
  byId("reviewCount").textContent = `${records.length} 条`;
  list.innerHTML = "";

  if (!records.length) {
    list.innerHTML = '<p class="empty-text">暂无历史推荐回访数据。</p>';
    return;
  }

  records.forEach((record, index) => {
    list.appendChild(createReviewRow({ ...record, display_rank: index + 1 }));
  });
}

function createReviewRow(record) {
  const template = byId("reviewRowTemplate");
  const node = template.content.firstElementChild.cloneNode(true);
  const reviewReturn = record.return_since_first_pct;

  node.querySelector(".review-name").textContent = record.name || record.code;
  node.querySelector(".review-code").textContent = `#${record.display_rank || record.review_rank || "-"} · ${record.code} · ${
    record.active_in_current_pool ? "当前池中" : "已调出"
  }`;
  node.querySelector(".review-return").textContent = formatPercent(reviewReturn);
  node.querySelector(".review-return").classList.add(returnClass(reviewReturn));
  node.querySelector(".review-first").textContent = `${record.first_recommend_date || "-"} / ${formatNumber(record.first_recommend_price)}`;
  node.querySelector(".review-status").textContent = record.review_status || "-";
  if (record.entry_return_from_first_entry_pct !== null && record.entry_return_from_first_entry_pct !== undefined) {
    node.querySelector(".review-status").textContent += ` · 接入${formatPercent(
      record.entry_return_from_first_entry_pct
    )} / 回撤${formatPercent(record.entry_drawdown_from_first_entry_pct)}`;
  }
  if (record.review_primary_attribution || record.review_model_action) {
    node.querySelector(".review-status").textContent += ` · 归因 ${record.review_primary_attribution || "-"} · ${
      record.review_model_action || ""
    }`;
  }
  node.title = record.comment || "";

  return node;
}

function bindSimulationEvents() {
  byId("manualRefreshButton").addEventListener("click", () => {
    loadPool({ silent: false, reason: "manual" });
  });

  byId("simulationStockSelect").addEventListener("change", (event) => {
    state.simulation.selectedCode = event.target.value;
    const stock = findStock(event.target.value);
    const price = suggestedTradePrice(stock);
    if (price) byId("simulationPriceInput").value = formatNumber(price);
    saveSimulation();
  });
  byId("simulationBuyButton").addEventListener("click", handleSimulationBuy);
  byId("simulationSellButton").addEventListener("click", handleSimulationSell);
  byId("simulationResetButton").addEventListener("click", resetSimulation);
  byId("autoStrategyRunButton").addEventListener("click", () => {
    runAutoStrategy({ force: false, quiet: false });
    renderSimulationPanel();
  });
  byId("autoStrategyEnabled").addEventListener("change", (event) => {
    state.simulation.autoSettings = { ...autoSettings(), enabled: event.target.checked };
    saveSimulation();
    renderSimulationPanel();
    setSimulationMessage(event.target.checked ? "自动模拟交易已启用。" : "自动模拟交易已关闭。", "info");
  });

  const settingBindings = [
    ["autoMaxPositionPct", "autoSettings", "maxPositionPct", 100],
    ["autoMaxStocks", "autoSettings", "maxStocks", 1],
    ["autoMinScore", "autoSettings", "minScore", 1],
    ["autoBuyPlanValidDays", "autoSettings", "buyPlanValidDays", 1],
    ["autoStopLossPct", "autoSettings", "stopLossPct", 100],
    ["autoTakeProfitPct", "autoSettings", "takeProfitPct", 100],
    ["autoTrailingStopPct", "autoSettings", "trailingStopPct", 100],
    ["feeCommissionRate", "feeSettings", "commissionRate", 100],
    ["feeMinCommission", "feeSettings", "minCommission", 1],
    ["feeStampDutyRate", "feeSettings", "stampDutyRate", 100],
    ["feeTransferRate", "feeSettings", "transferFeeRate", 100],
  ];

  settingBindings.forEach(([id, group, key, divisor]) => {
    byId(id).addEventListener("change", (event) => {
      const rawValue = Number(event.target.value);
      if (!Number.isFinite(rawValue)) return;
      state.simulation[group] = {
        ...(group === "autoSettings" ? autoSettings() : feeSettings()),
        [key]: rawValue / divisor,
      };
      if (key === "maxStocks" || key === "buyPlanValidDays") state.simulation[group][key] = Math.floor(rawValue);
      if (key === "minScore" || key === "minCommission") state.simulation[group][key] = rawValue;
      state.simulation.autoSettings = normalizeAutoSettings(state.simulation.autoSettings);
      state.simulation.feeSettings = normalizeFeeSettings(state.simulation.feeSettings);
      saveSimulation();
      renderSimulationPanel();
      setSimulationMessage("自动策略参数已保存。下一次自动检查会按新参数执行。", "info");
    });
  });
}

function scheduleAutoRefresh() {
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);

  const refreshIfVisible = (reason) => {
    if (document.visibilityState === "visible") {
      loadPool({ silent: true, reason });
    }
  };

  state.refreshTimer = window.setInterval(() => {
    refreshIfVisible("auto");
  }, AUTO_REFRESH_INTERVAL_MS);

  document.addEventListener("visibilitychange", () => {
    refreshIfVisible("visible");
  });

  window.addEventListener("focus", () => {
    refreshIfVisible("focus");
  });

  window.addEventListener("pageshow", () => {
    refreshIfVisible("pageshow");
  });

  window.addEventListener("online", () => {
    loadPool({ silent: true, reason: "online" });
  });
}

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
    button.classList.add("active");
    state.filter = button.dataset.filter;
    render();
  });
});

let deferredPrompt;
const installButton = byId("installButton");

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredPrompt = event;
  installButton.hidden = false;
});

installButton.addEventListener("click", async () => {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  await deferredPrompt.userChoice;
  deferredPrompt = null;
  installButton.hidden = true;
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("sw.js").catch(() => {});
  });
}

bindSimulationEvents();
scheduleAutoRefresh();
loadPool({ reason: "initial" });
