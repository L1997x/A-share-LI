from __future__ import annotations

import json
import math
import os
import re
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
REVIEW_PATH = DATA_DIR / "review.json"
UNIVERSE_PATH = DATA_DIR / "universe_scan.json"
MODEL_FEEDBACK_PATH = DATA_DIR / "model_feedback.json"
HISTORY_DIR = DATA_DIR / "history"
HISTORY_SNAPSHOT_DIR = HISTORY_DIR / "snapshots"
HISTORY_SNAPSHOT_RETENTION = 120
CN_TZ = timezone(timedelta(hours=8))
FINAL_POOL_SIZE = 10
DEEP_ANALYSIS_LIMIT = 28
UNIVERSE_EXPORT_LIMIT = 120
FEEDBACK_HORIZONS = (1, 3, 5, 10)
FEEDBACK_SCORE_CAP = 0.8
FEEDBACK_MIN_STRONG_SAMPLES = 8
ENTRY_FEEDBACK_MIN_SAMPLES = 6
ENTRY_FEEDBACK_PRICE_CAP_DOWN = -2.4
ENTRY_FEEDBACK_PRICE_CAP_UP = 0.6
ENTRY_CRASH_RETURN_THRESHOLD = -5.0
ENTRY_ADVERSE_DRAW_THRESHOLD = -7.0
MAINBOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
SCHEDULE_PHASE_MAP = {
    "0 2 * * 1-5": ("morning_entry", "10点早盘接入"),
    "8 2 * * 1-5": ("morning_entry", "10点早盘接入"),
    "20 3 * * 1-5": ("morning_entry", "11:20午前买入复检"),
    "28 3 * * 1-5": ("morning_entry", "11:20午前买入复检"),
    "30 5 * * 1-5": ("morning_entry", "13:30午后买入复检"),
    "38 5 * * 1-5": ("morning_entry", "13:30午后买入复检"),
    "30 6 * * 1-5": ("afternoon_risk", "14:30尾盘风控"),
    "38 6 * * 1-5": ("afternoon_risk", "14:30尾盘风控"),
    "0 12 * * 1-5": ("evening_watch", "20点次日关注"),
    "8 12 * * 1-5": ("evening_watch", "20点次日关注"),
}
SCHEDULE_PHASE_MAP.update(
    {
        "53 1 * * 1-5": SCHEDULE_PHASE_MAP["0 2 * * 1-5"],
        "11 2 * * 1-5": SCHEDULE_PHASE_MAP["0 2 * * 1-5"],
        "13 3 * * 1-5": SCHEDULE_PHASE_MAP["20 3 * * 1-5"],
        "31 3 * * 1-5": SCHEDULE_PHASE_MAP["20 3 * * 1-5"],
        "23 5 * * 1-5": SCHEDULE_PHASE_MAP["30 5 * * 1-5"],
        "41 5 * * 1-5": SCHEDULE_PHASE_MAP["30 5 * * 1-5"],
        "23 6 * * 1-5": SCHEDULE_PHASE_MAP["30 6 * * 1-5"],
        "41 6 * * 1-5": SCHEDULE_PHASE_MAP["30 6 * * 1-5"],
        "53 11 * * 1-5": SCHEDULE_PHASE_MAP["0 12 * * 1-5"],
        "11 12 * * 1-5": SCHEDULE_PHASE_MAP["0 12 * * 1-5"],
    }
)
EASTMONEY_FUND_FLOW_URL = "https://push2.eastmoney.com/api/qt/clist/get"
EASTMONEY_FUND_FLOW_FS = "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
FUND_FLOW_KEYS = [
    "fund_today_main_net",
    "fund_today_main_net_pct",
    "fund_5d_main_net",
    "fund_5d_main_net_pct",
    "fund_flow_score",
    "fund_flow_rank",
    "fund_flow_label",
    "fund_flow_bonus",
    "fund_flow_source",
]
CHIP_KEYS = [
    "chip_profit_ratio",
    "chip_avg_cost",
    "chip_cost_gap_pct",
    "chip_concentration_70",
    "chip_concentration_90",
    "chip_score",
    "chip_label",
    "chip_bonus",
    "chip_source",
    "chip_date",
    "chip_note",
]
FEEDBACK_DIMENSION_LABELS = {
    "theme_group": "主题分组",
    "buy_signal": "买入信号",
    "status": "状态",
    "fund_flow": "资金流",
    "chip": "筹码",
    "layer_rank": "全主板排名",
    "entry_gap": "接入价偏离",
    "price_source": "价格源",
}
FEEDBACK_DIMENSION_WEIGHTS = {
    "theme_group": 0.8,
    "buy_signal": 1.0,
    "status": 0.9,
    "fund_flow": 0.75,
    "chip": 0.85,
    "layer_rank": 0.65,
    "entry_gap": 0.7,
    "price_source": 0.35,
}
FEEDBACK_DIMENSION_LABELS["market_regime"] = "市场阶段"
FEEDBACK_DIMENSION_LABELS["update_phase"] = "更新时段"
FEEDBACK_DIMENSION_WEIGHTS["market_regime"] = 0.75
FEEDBACK_DIMENSION_WEIGHTS["update_phase"] = 0.6
FEEDBACK_DIMENSION_LABELS["entry_sample_type"] = "接入样本类型"
ENTRY_EFFECTIVENESS_DIMENSION_WEIGHTS = {
    "buy_signal": 1.25,
    "status": 1.0,
    "entry_gap": 1.1,
    "fund_flow": 0.85,
    "chip": 0.85,
    "theme_group": 0.65,
    "layer_rank": 0.45,
    "entry_sample_type": 1.1,
}
ENTRY_EFFECTIVENESS_DIMENSION_WEIGHTS["market_regime"] = 0.85
ENTRY_EFFECTIVENESS_DIMENSION_WEIGHTS["update_phase"] = 0.65


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    theme: str
    logic: str
    base_score: float
    catalysts: list[str]
    risks: list[str]


CANDIDATES = [
    Candidate("601138", "工业富联", "AI服务器/算力基础设施", "AI算力扩张带动服务器制造与系统集成需求，属于产业链中可验证订单和交付进度的关键环节。", 6.4, ["AI服务器订单", "大客户资本开支", "业绩兑现"], ["估值消化", "客户集中", "短线涨幅后波动"]),
    Candidate("002371", "北方华创", "半导体设备", "国产半导体设备替代与先进制程资本开支相关，属于供给受限、验证周期长的关键环节。", 6.4, ["国产替代", "晶圆厂扩产", "设备订单"], ["估值高", "扩产节奏变化", "技术验证不确定"]),
    Candidate("000988", "华工科技", "光模块/激光设备", "同时受益于光通信升级和制造设备需求，关注光模块放量与激光装备景气度共振。", 6.4, ["光模块需求", "激光设备订单", "业绩兑现"], ["板块拥挤", "订单波动", "竞争加剧"]),
    Candidate("002463", "沪电股份", "AI PCB", "AI服务器高速PCB需求提升，关注高端产能、良率和客户认证。", 6.0, ["AI服务器PCB", "高端产能释放", "客户认证"], ["60日涨幅偏高", "波动放大", "景气预期过满"]),
    Candidate("002409", "雅克科技", "电子材料", "电子材料位于半导体上游关键耗材环节，关注国产替代、客户认证和产能释放。", 5.5, ["国产替代", "材料认证", "先进制程需求"], ["60日涨幅偏高", "估值消化", "客户验证周期长"]),
    Candidate("002916", "深南电路", "PCB/封装基板", "高端PCB和封装基板受益于AI服务器与先进封装，关注产能利用率和高端产品占比。", 5.4, ["AI服务器PCB", "封装基板", "产能释放"], ["60日涨幅偏高", "波动偏高", "预期拥挤"]),
    Candidate("002156", "通富微电", "先进封装", "先进封装是AI芯片扩产和国产替代的关键环节，弹性较高但周期波动也大。", 5.1, ["先进封装需求", "国产替代", "客户订单"], ["换手偏高", "周期反复", "盈利弹性不稳定"]),
    Candidate("002185", "华天科技", "封测", "封测环节受益于半导体景气修复和先进封装渗透，需关注盈利改善质量。", 5.1, ["封测复苏", "先进封装", "国产替代"], ["换手偏高", "盈利修复不确定", "周期反复"]),
    Candidate("603228", "景旺电子", "PCB", "PCB需求受AI硬件和汽车电子拉动，关注高端产能和客户结构升级。", 5.0, ["高端PCB", "汽车电子", "客户结构升级"], ["需求兑现慢", "价格竞争", "板块波动"]),
    Candidate("600584", "长电科技", "先进封装", "全球封测龙头之一，先进封装和行业复苏是主要观察点。", 4.5, ["先进封装", "行业复苏", "大客户订单"], ["60日涨幅偏高", "换手偏高", "盈利修复节奏"]),
]


BROAD_CANDIDATES = [
    Candidate("002050", "三花智控", "机器人/热管理", "机器人执行器与新能源热管理属于高端制造关键部件，关注客户验证、产能和订单兑现。", 5.6, ["机器人执行器", "新能源热管理", "海外客户订单"], ["估值消化", "客户集中", "海外需求波动"]),
    Candidate("002747", "埃斯顿", "工业机器人", "国产工业机器人本体和自动化系统受益于制造业升级，关注订单质量和盈利改善。", 5.1, ["机器人国产替代", "制造业自动化", "订单修复"], ["盈利波动", "竞争加剧", "需求恢复慢"]),
    Candidate("002472", "双环传动", "机器人/精密传动", "精密传动是机器人和汽车智能化的重要零部件，关注高端客户导入和产能利用率。", 5.2, ["机器人减速器", "汽车齿轮", "客户导入"], ["价格竞争", "订单节奏", "估值波动"]),
    Candidate("603728", "鸣志电器", "机器人/控制电机", "控制电机和运动控制系统处于机器人执行链条，关注人形机器人与工业自动化需求验证。", 5.0, ["运动控制", "机器人执行器", "海外订单"], ["题材波动", "验证周期长", "竞争加剧"]),
    Candidate("600406", "国电南瑞", "电网自动化/电力IT", "新型电力系统建设推动调度、继保和电网自动化投资，属于订单可跟踪的基础设施环节。", 5.8, ["电网投资", "新型电力系统", "特高压配套"], ["投资节奏", "估值消化", "订单确认周期"]),
    Candidate("002028", "思源电气", "输变电设备", "电网投资和海外输变电需求带动一次/二次设备，关注订单、交付和毛利率。", 5.5, ["电网建设", "海外输变电", "订单兑现"], ["原材料波动", "海外交付", "估值消化"]),
    Candidate("601179", "中国西电", "特高压/电力设备", "特高压与主网建设推动高压开关和变压器需求，关注招标节奏和产能释放。", 5.2, ["特高压招标", "主网投资", "设备更新"], ["招标波动", "订单兑现慢", "短线情绪波动"]),
    Candidate("600312", "平高电气", "特高压开关", "高压开关设备是特高压建设关键环节，关注订单兑现和盈利弹性。", 5.2, ["特高压建设", "电网设备", "国企改革"], ["订单周期", "毛利率波动", "主题拥挤"]),
    Candidate("603606", "东方电缆", "海缆/海风", "海上风电海缆技术壁垒较高，关注海风项目核准、招标和交付节奏。", 5.0, ["海风招标", "海缆交付", "海外订单"], ["海风装机延迟", "价格竞争", "项目审批"]),
    Candidate("600089", "特变电工", "变压器/硅料/电力设备", "变压器出海和电网投资提供支撑，同时需跟踪硅料周期拖累。", 5.0, ["变压器出海", "电网投资", "能源建设"], ["硅料周期", "多业务估值折价", "原材料波动"]),
    Candidate("600875", "东方电气", "能源装备/燃机", "能源安全和设备更新推动发电装备需求，关注燃机、核电、风电订单。", 5.0, ["能源装备", "设备更新", "核电燃机订单"], ["订单确认慢", "毛利率波动", "周期属性"]),
    Candidate("601727", "上海电气", "能源装备/工业设备", "能源装备和工业设备更新带来订单修复弹性，关注资产质量和盈利改善。", 4.8, ["设备更新", "能源装备", "国企改革"], ["资产减值", "盈利修复慢", "业务复杂"]),
    Candidate("601899", "紫金矿业", "铜/黄金资源", "铜金资源兼具全球供给约束和通胀/利率敏感属性，关注金属价格和产量释放。", 5.7, ["铜价", "金价", "矿山产量"], ["商品价格波动", "海外运营", "汇率风险"]),
    Candidate("600547", "山东黄金", "黄金", "黄金价格受实际利率、避险和央行购金影响，关注金价趋势和矿山成本。", 5.2, ["金价上行", "避险需求", "矿山增产"], ["金价回调", "成本上升", "并购整合"]),
    Candidate("600489", "中金黄金", "黄金/央企资源", "央企黄金平台受益于金价和资源整合预期，关注产量、成本和金价。", 5.0, ["金价", "资源整合", "央企改革"], ["金价波动", "成本压力", "主题兑现慢"]),
    Candidate("601600", "中国铝业", "铝/资源", "铝价和氧化铝供需影响盈利，关注供给约束、电力成本和需求修复。", 4.9, ["铝价", "供给约束", "资源品景气"], ["商品周期", "电力成本", "需求波动"]),
    Candidate("000630", "铜陵有色", "铜/有色金属", "铜资源和冶炼加工受益于铜价和新能源需求，关注库存、TC和价格趋势。", 4.9, ["铜价", "新能源需求", "库存变化"], ["铜价回调", "加工费波动", "周期属性"]),
    Candidate("000960", "锡业股份", "锡/小金属", "锡受半导体焊料与供给约束影响，关注库存、价格和矿端扰动。", 4.9, ["锡价", "供给扰动", "半导体需求"], ["价格波动", "需求反复", "资源政策"]),
    Candidate("600111", "北方稀土", "稀土磁材上游", "稀土供给配额和新能源/机器人需求影响价格，关注价格拐点和库存。", 4.8, ["稀土价格", "机器人磁材", "新能源车"], ["价格下行", "政策扰动", "需求不及预期"]),
    Candidate("000831", "中国稀土", "稀土资源", "稀土资源整合与价格周期是主要变量，关注供给政策和下游磁材需求。", 4.8, ["资源整合", "稀土价格", "政策催化"], ["价格波动", "主题交易", "盈利弹性不稳定"]),
    Candidate("600276", "恒瑞医药", "创新药", "创新药出海和管线兑现是长期重估核心，关注临床进展、授权交易和销售恢复。", 5.3, ["创新药出海", "管线进展", "医保压力缓和"], ["研发失败", "集采降价", "估值消化"]),
    Candidate("000538", "云南白药", "中药/消费医疗", "品牌中药和消费医疗具备现金流属性，关注改革、渠道和利润率改善。", 4.8, ["国企改革", "消费医疗", "分红回报"], ["增长偏慢", "渠道压力", "估值弹性有限"]),
    Candidate("600085", "同仁堂", "中药品牌", "老字号中药品牌具备稀缺性，关注渠道扩张、提价和国企改革。", 4.8, ["中药消费", "品牌提价", "国企改革"], ["消费疲弱", "估值消化", "改革慢"]),
    Candidate("600519", "贵州茅台", "高端白酒", "高端白酒是消费龙头定价权代表，关注批价、渠道库存和分红回报。", 4.9, ["分红回报", "批价企稳", "消费修复"], ["消费疲弱", "批价下行", "估值弹性下降"]),
    Candidate("000858", "五粮液", "高端白酒", "高端白酒需求修复和渠道库存是核心变量，关注批价、动销和费用投放。", 4.7, ["消费修复", "渠道去库", "分红回报"], ["批价波动", "库存压力", "消费疲弱"]),
    Candidate("000333", "美的集团", "家电/机器人", "家电龙头现金流稳定，同时具备机器人与工业技术延展，关注出口和利润率。", 5.1, ["家电出口", "机器人业务", "分红回购"], ["地产拖累", "汇率波动", "增长放缓"]),
    Candidate("000651", "格力电器", "家电/分红", "空调龙头具备高分红属性，关注渠道改革、出口和估值修复。", 4.7, ["分红回报", "估值修复", "出口需求"], ["地产链压力", "竞争加剧", "增长放缓"]),
    Candidate("600887", "伊利股份", "乳制品消费", "乳制品龙头受消费复苏和成本变化影响，关注需求、费用率和分红。", 4.6, ["消费修复", "成本改善", "分红回报"], ["需求疲弱", "价格竞争", "增长放缓"]),
    Candidate("600036", "招商银行", "银行/财富管理", "优质银行资产质量和财富管理修复影响估值，关注息差、不良和分红。", 4.8, ["分红回报", "资产质量企稳", "财富管理修复"], ["息差下行", "地产风险", "经济修复慢"]),
    Candidate("601318", "中国平安", "保险/金融", "保险负债端和投资端修复决定估值弹性，关注新业务价值和资本市场表现。", 4.8, ["保险复苏", "权益市场修复", "分红回报"], ["投资收益波动", "地产敞口", "负债端压力"]),
    Candidate("600030", "中信证券", "券商/资本市场", "券商受资本市场活跃度和政策影响，关注成交额、投行业务和估值修复。", 4.7, ["资本市场改革", "成交活跃", "并购重组"], ["市场低迷", "投行业务波动", "政策节奏"]),
    Candidate("600150", "中国船舶", "船舶制造", "船舶景气周期由新船订单、船价和交付节奏驱动，关注盈利兑现。", 5.3, ["船价高位", "订单交付", "央企整合"], ["周期回落", "成本波动", "交付延迟"]),
    Candidate("600760", "中航沈飞", "军机/航空装备", "航空装备龙头受订单节奏和军工景气影响，关注交付、合同和盈利质量。", 5.0, ["军工订单", "航空装备", "国企改革"], ["订单节奏", "估值消化", "回款周期"]),
    Candidate("000768", "中航西飞", "军机/大飞机", "军机和大飞机产业链核心平台，关注订单、交付和产能效率。", 4.9, ["大飞机", "航空装备", "军工订单"], ["盈利释放慢", "订单节奏", "估值波动"]),
    Candidate("600893", "航发动力", "航空发动机", "航空发动机具备高壁垒属性，关注维修、交付和国产替代进度。", 4.8, ["航空发动机", "国产替代", "维修需求"], ["研发周期", "盈利波动", "订单不透明"]),
    Candidate("002594", "比亚迪", "新能源车/电池", "新能源车龙头兼具整车、电池和出海逻辑，关注销量、价格和利润率。", 5.4, ["出海销量", "电池技术", "智能化"], ["价格战", "利润率压力", "海外政策"]),
    Candidate("601127", "赛力斯", "智能汽车", "智能汽车销量和高端车型放量是核心变量，关注交付、毛利率和合作生态。", 5.1, ["智能汽车销量", "高端车型", "生态合作"], ["销量波动", "估值高", "竞争加剧"]),
    Candidate("000625", "长安汽车", "自主汽车/智能化", "自主品牌和智能化转型提供弹性，关注新能源销量和合资改善。", 4.8, ["新能源转型", "智能化车型", "国企改革"], ["价格战", "销量不及预期", "利润率压力"]),
    Candidate("601689", "拓普集团", "汽车零部件/机器人", "汽车轻量化和机器人执行器预期共同影响估值，关注客户拓展和订单兑现。", 5.0, ["汽车零部件", "机器人执行器", "客户拓展"], ["客户集中", "价格压力", "题材波动"]),
    Candidate("002920", "德赛西威", "智能座舱/智驾", "智能座舱与智能驾驶渗透率提升，关注订单、域控制器和客户结构。", 5.0, ["智能座舱", "智能驾驶", "客户升级"], ["汽车价格战", "研发投入", "客户集中"]),
]


def all_candidates() -> list[Candidate]:
    seen: set[str] = set()
    merged: list[Candidate] = []
    for candidate in [*CANDIDATES, *BROAD_CANDIDATES]:
        if candidate.code in seen:
            continue
        seen.add(candidate.code)
        merged.append(candidate)
    return merged


def board_for(code: str) -> str:
    return "沪市主板" if code.startswith("6") else "深市主板"


def ak_symbol(code: str) -> str:
    return ("sh" if code.startswith("6") else "sz") + code


def safe_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def round_or_none(value: Any, digits: int = 2) -> float | None:
    value = safe_float(value)
    if value is None:
        return None
    return round(value, digits)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else digits


def is_mainboard_code(code: str) -> bool:
    return code.startswith(MAINBOARD_PREFIXES)


def latest_a_share_trade_date() -> str:
    today = datetime.now(CN_TZ).date()
    try:
        import akshare as ak

        calendar = ak.tool_trade_date_hist_sina()
        trade_dates = pd.to_datetime(calendar["trade_date"], errors="coerce").dt.date.dropna().tolist()
        past_dates = [trade_date for trade_date in trade_dates if trade_date <= today]
        if past_dates:
            return max(past_dates).isoformat()
    except Exception:
        pass

    if today.weekday() < 5:
        return today.isoformat()
    days_back = today.weekday() - 4
    return (today - timedelta(days=days_back)).isoformat()


def percentile_rank(series: pd.Series) -> pd.Series:
    return series.rank(method="average", pct=True).fillna(0.0)


def fund_flow_label(score: Any) -> str:
    value = safe_float(score)
    if value is None:
        return "资金流暂缺"
    if value >= 10:
        return "资金明显流入"
    if value >= 3:
        return "资金温和流入"
    if value <= -10:
        return "资金明显流出"
    if value <= -3:
        return "资金偏流出"
    return "资金中性"


def compact_error(error: Exception, limit: int = 180) -> str:
    text = str(error).replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def parse_cn_money(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).replace(",", "").strip()
    if not text or text in {"-", "--", "nan"}:
        return None
    multiplier = 1.0
    if "亿" in text:
        multiplier = 100_000_000.0
    elif "万" in text:
        multiplier = 10_000.0
    text = text.replace("亿元", "").replace("万元", "").replace("元", "").replace("亿", "").replace("万", "").replace("%", "")
    number = safe_float(text)
    return number * multiplier if number is not None else None


def empty_fund_flow_meta() -> dict[str, Any]:
    return {key: None for key in FUND_FLOW_KEYS}


def fund_meta_from_record(record: dict[str, Any] | None) -> dict[str, Any]:
    if not record:
        return empty_fund_flow_meta()
    return {
        "fund_today_main_net": round_or_none(record.get("fund_today_main_net"), 0),
        "fund_today_main_net_pct": round_or_none(record.get("fund_today_main_net_pct")),
        "fund_5d_main_net": round_or_none(record.get("fund_5d_main_net"), 0),
        "fund_5d_main_net_pct": round_or_none(record.get("fund_5d_main_net_pct")),
        "fund_flow_score": round_or_none(record.get("fund_flow_score")),
        "fund_flow_rank": int(record["fund_flow_rank"]) if safe_float(record.get("fund_flow_rank")) is not None else None,
        "fund_flow_label": record.get("fund_flow_label") if isinstance(record.get("fund_flow_label"), str) else None,
        "fund_flow_bonus": round_or_none(record.get("fund_flow_bonus")),
        "fund_flow_source": record.get("fund_flow_source") if isinstance(record.get("fund_flow_source"), str) else None,
    }


def empty_chip_meta() -> dict[str, Any]:
    meta = {key: None for key in CHIP_KEYS}
    meta["chip_label"] = "筹码暂缺"
    meta["chip_bonus"] = 0.0
    meta["chip_note"] = "筹码数据暂缺，未参与本轮加减分。"
    return meta


def chip_label(score: Any) -> str:
    value = safe_float(score)
    if value is None:
        return "筹码暂缺"
    if value >= 1.4:
        return "筹码较健康"
    if value >= 0.4:
        return "筹码中性偏好"
    if value <= -1.2:
        return "筹码压力较大"
    if value <= -0.3:
        return "筹码略有压力"
    return "筹码中性"


def score_chip_structure(profit_ratio: Any, cost_gap_pct: Any, concentration_70: Any) -> tuple[float, str]:
    profit = safe_float(profit_ratio)
    gap = safe_float(cost_gap_pct)
    concentration = safe_float(concentration_70)
    score = 0.0
    notes: list[str] = []

    if profit is not None:
        if 35 <= profit <= 75:
            score += 0.8
            notes.append("获利盘处于较健康区间")
        elif 25 <= profit < 35 or 75 < profit <= 85:
            score += 0.2
            notes.append("获利盘接近中性")
        elif profit > 90:
            score -= 1.0
            notes.append("获利盘过高，兑现压力上升")
        elif profit < 20:
            score -= 0.8
            notes.append("套牢盘偏重")

    if gap is not None:
        if -3 <= gap <= 12:
            score += 0.8
            notes.append("现价贴近平均成本")
        elif 12 < gap <= 25:
            score += 0.1
            notes.append("现价略高于平均成本")
        elif gap > 35:
            score -= 0.8
            notes.append("现价显著高于平均成本")
        elif gap < -10:
            score -= 0.6
            notes.append("现价明显低于平均成本")

    if concentration is not None:
        if concentration <= 12:
            score += 0.6
            notes.append("70%成本区较集中")
        elif concentration <= 22:
            score += 0.2
            notes.append("70%成本区中等集中")
        elif concentration > 35:
            score -= 0.5
            notes.append("成本分布偏松散")

    if not notes:
        notes.append("筹码指标不足")
    return round(clamp(score, -1.6, 2.0), 2), "；".join(notes)


def cost_at_ratio(prices: np.ndarray, chips: np.ndarray, ratio: float) -> float | None:
    total = float(chips.sum())
    if total <= 0:
        return None
    target = total * ratio
    index = int(np.searchsorted(np.cumsum(chips), target, side="left"))
    index = min(max(index, 0), len(prices) - 1)
    return float(prices[index])


def allocate_chip_weights(prices: np.ndarray, low: float, high: float, center: float) -> np.ndarray:
    if high <= low:
        weights = np.zeros_like(prices, dtype=float)
        weights[int(np.argmin(np.abs(prices - center)))] = 1.0
        return weights

    center = clamp(center, low, high)
    weights = np.zeros_like(prices, dtype=float)
    in_range = (prices >= low) & (prices <= high)
    if not np.any(in_range):
        return weights

    left_span = max(center - low, 1e-6)
    right_span = max(high - center, 1e-6)
    left_mask = in_range & (prices <= center)
    right_mask = in_range & (prices > center)
    weights[left_mask] = (prices[left_mask] - low) / left_span
    weights[right_mask] = (high - prices[right_mask]) / right_span
    weights = np.clip(weights, 0, None)
    total = float(weights.sum())
    if total <= 0:
        weights[in_range] = 1.0
        total = float(weights.sum())
    return weights / total


def daily_chip_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in df.tail(160).to_dict("records"):
        turnover = safe_float(row.get("turnover"))
        if turnover is None:
            continue
        turnover_pct = turnover * 100 if turnover <= 1 else turnover
        records.append(
            {
                "date": pd.to_datetime(row.get("date")).strftime("%Y-%m-%d"),
                "open": safe_float(row.get("open")),
                "close": safe_float(row.get("close")),
                "high": safe_float(row.get("high")),
                "low": safe_float(row.get("low")),
                "turnover_pct": turnover_pct,
            }
        )
    return [
        record
        for record in records
        if None
        not in (
            record.get("open"),
            record.get("close"),
            record.get("high"),
            record.get("low"),
            record.get("turnover_pct"),
        )
    ]


def calculate_chip_metrics(
    records: list[dict[str, Any]],
    latest_close: Any = None,
    source: str = "Eastmoney daily kline + local CYQ estimate",
) -> dict[str, Any]:
    usable = records[-160:]
    if len(usable) < 40:
        raise RuntimeError("筹码K线样本不足")

    lows = np.array([float(item["low"]) for item in usable], dtype=float)
    highs = np.array([float(item["high"]) for item in usable], dtype=float)
    min_price = float(np.nanmin(lows))
    max_price = float(np.nanmax(highs))
    if not math.isfinite(min_price) or not math.isfinite(max_price) or max_price <= min_price:
        raise RuntimeError("筹码价格区间无效")

    prices = np.linspace(min_price, max_price, 180)
    chips = np.zeros_like(prices, dtype=float)
    for item in usable:
        turnover = clamp(float(item["turnover_pct"]) / 100, 0, 1)
        if turnover <= 0:
            continue
        low = float(item["low"])
        high = float(item["high"])
        center = (float(item["open"]) + float(item["close"]) + high + low) / 4
        chips *= 1 - turnover
        chips += allocate_chip_weights(prices, low, high, center) * turnover

    total = float(chips.sum())
    if total <= 0:
        raise RuntimeError("筹码分布为空")

    close = safe_float(latest_close) or float(usable[-1]["close"])
    avg_cost = float(np.sum(prices * chips) / total)
    profit_ratio = float(chips[prices <= close].sum() / total * 100)
    low_70 = cost_at_ratio(prices, chips, 0.15)
    high_70 = cost_at_ratio(prices, chips, 0.85)
    low_90 = cost_at_ratio(prices, chips, 0.05)
    high_90 = cost_at_ratio(prices, chips, 0.95)
    concentration_70 = (high_70 - low_70) / (high_70 + low_70) * 100 if low_70 and high_70 else None
    concentration_90 = (high_90 - low_90) / (high_90 + low_90) * 100 if low_90 and high_90 else None
    cost_gap_pct = (close / avg_cost - 1) * 100 if avg_cost else None
    chip_score, note = score_chip_structure(profit_ratio, cost_gap_pct, concentration_70)
    bonus = clamp(chip_score * 0.35, -0.5, 0.6)

    return {
        "chip_profit_ratio": round_or_none(profit_ratio),
        "chip_avg_cost": round_or_none(avg_cost),
        "chip_cost_gap_pct": round_or_none(cost_gap_pct),
        "chip_concentration_70": round_or_none(concentration_70),
        "chip_concentration_90": round_or_none(concentration_90),
        "chip_score": round_or_none(chip_score),
        "chip_label": chip_label(chip_score),
        "chip_bonus": round_or_none(bonus),
        "chip_source": source,
        "chip_date": usable[-1]["date"],
        "chip_note": note,
    }


def eastmoney_fund_request(params: dict[str, str]) -> dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    }
    last_error: Exception | None = None
    request_modes = (
        {"proxies": {"http": "", "https": ""}},
        {},
    )
    for mode in request_modes:
        for _ in range(2):
            try:
                response = requests.get(
                    EASTMONEY_FUND_FLOW_URL,
                    params=params,
                    headers=headers,
                    timeout=20,
                    **mode,
                )
                response.raise_for_status()
                data = response.json()
                if not data.get("data"):
                    raise RuntimeError("Eastmoney fund flow returned empty data")
                return data
            except Exception as exc:  # free endpoints can be rate-limited or proxy-sensitive
                last_error = exc
    raise RuntimeError(str(last_error) if last_error else "Eastmoney fund flow request failed")


def fetch_fund_flow_rank(horizon: str) -> dict[str, dict[str, Any]]:
    configs = {
        "today": {
            "fid": "f62",
            "fields": "f12,f14,f2,f3,f62,f184,f124",
            "net": "f62",
            "pct": "f184",
            "chg": "f3",
            "net_key": "fund_today_main_net",
            "pct_key": "fund_today_main_net_pct",
        },
        "5d": {
            "fid": "f164",
            "fields": "f12,f14,f2,f109,f164,f165,f124",
            "net": "f164",
            "pct": "f165",
            "chg": "f109",
            "net_key": "fund_5d_main_net",
            "pct_key": "fund_5d_main_net_pct",
        },
    }
    config = configs[horizon]
    page_size = 500
    params = {
        "fid": config["fid"],
        "po": "1",
        "pz": str(page_size),
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "fs": EASTMONEY_FUND_FLOW_FS,
        "fields": config["fields"],
    }
    first_page = eastmoney_fund_request(params)
    total = int(first_page["data"].get("total") or 0)
    total_pages = max(1, math.ceil(total / page_size))
    rows = list(first_page["data"].get("diff") or [])
    for page in range(2, total_pages + 1):
        params["pn"] = str(page)
        rows.extend(eastmoney_fund_request(params)["data"].get("diff") or [])

    result: dict[str, dict[str, Any]] = {}
    for rank, item in enumerate(rows, start=1):
        code = normalize_code(item.get("f12"))
        if not code:
            continue
        result[code] = {
            "name": item.get("f14"),
            "rank": rank,
            "price": safe_float(item.get("f2")),
            "pct_chg": safe_float(item.get(config["chg"])),
            config["net_key"]: safe_float(item.get(config["net"])),
            config["pct_key"]: safe_float(item.get(config["pct"])),
            "fund_flow_source": "Eastmoney",
        }
    return result


def ths_fund_headers() -> dict[str, str]:
    from akshare.stock_feature import stock_fund_flow as ths_mod

    js_code = ths_mod.py_mini_racer.MiniRacer()
    js_code.eval(ths_mod._get_file_content_ths("ths.js"))
    v_code = js_code.call("v")
    return {
        "Accept": "text/html, */*; q=0.01",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "hexin-v": v_code,
        "Host": "data.10jqka.com.cn",
        "Pragma": "no-cache",
        "Referer": "http://data.10jqka.com.cn/funds/hyzjl/",
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
    }


def fetch_ths_fund_flow_rank(horizon: str) -> dict[str, dict[str, Any]]:
    from bs4 import BeautifulSoup
    from io import StringIO

    url_map = {
        "today": "http://data.10jqka.com.cn/funds/ggzjl/field/zdf/order/desc/page/{}/ajax/1/free/1/",
        "5d": "http://data.10jqka.com.cn/funds/ggzjl/board/5/field/zdf/order/desc/page/{}/ajax/1/free/1/",
    }
    url_template = url_map[horizon]
    first = requests.get(url_template.format(1), headers=ths_fund_headers(), timeout=20)
    first.raise_for_status()
    soup = BeautifulSoup(first.text, features="lxml")
    page_info = soup.find(name="span", attrs={"class": "page_info"})
    total_pages = int(page_info.text.split("/")[1]) if page_info and "/" in page_info.text else 1
    frames = []
    for page in range(1, total_pages + 1):
        if page == 1:
            text = first.text
        else:
            response = requests.get(url_template.format(page), headers=ths_fund_headers(), timeout=20)
            response.raise_for_status()
            text = response.text
        tables = pd.read_html(StringIO(text))
        if tables:
            frames.append(tables[0])

    if not frames:
        raise RuntimeError("Tonghuashun fund flow returned no tables")

    df = pd.concat(frames, ignore_index=True)
    result: dict[str, dict[str, Any]] = {}
    for index, row in df.iterrows():
        code = normalize_code(row.get("股票代码"))
        if not code:
            continue
        record = result.setdefault(
            code,
            {
                "name": row.get("股票简称"),
                "fund_flow_source": "Tonghuashun",
            },
        )
        if horizon == "today":
            net = parse_cn_money(row.get("净额(元)"))
            amount = parse_cn_money(row.get("成交额(元)"))
            record["fund_today_main_net"] = net
            record["fund_today_main_net_pct"] = (net / amount * 100) if net is not None and amount else None
            record["today_rank"] = int(index + 1)
        else:
            record["fund_5d_main_net"] = parse_cn_money(row.get("资金流入净额(元)"))
            record["five_day_rank"] = int(index + 1)
    return result


def fetch_market_fund_flow() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    warnings: list[str] = []
    provider = "Eastmoney"

    def collect(fetcher, provider_name: str) -> tuple[dict[str, dict[str, Any]], list[str], list[str]]:
        provider_warnings: list[str] = []
        provider_merged: dict[str, dict[str, Any]] = {}
        provider_fetched: list[str] = []
        for horizon in ("today", "5d"):
            try:
                records = fetcher(horizon)
                provider_fetched.append(horizon)
                for code, record in records.items():
                    provider_merged.setdefault(code, {}).update(record)
            except Exception as exc:
                provider_warnings.append(f"资金流{provider_name}-{horizon}获取失败：{compact_error(exc)}")
        return provider_merged, provider_fetched, provider_warnings

    merged, fetched, eastmoney_warnings = collect(fetch_fund_flow_rank, "Eastmoney")
    warnings.extend(eastmoney_warnings)
    if not merged:
        provider = "Tonghuashun"
        merged, fetched, ths_warnings = collect(fetch_ths_fund_flow_rank, "Tonghuashun")
        warnings.extend(ths_warnings)

    return merged, {
        "source": provider,
        "available": bool(merged),
        "fetched_horizons": fetched,
        "record_count": len(merged),
        "warnings": warnings,
        "note": "资金流用于确认趋势质量，不单独构成买卖依据。",
    }


def market_temperature_label(score: Any) -> tuple[str, str]:
    value = safe_float(score)
    if value is None:
        return "市场温度未知", "unknown"
    if value >= 28:
        return "强势可攻", "strong"
    if value >= 10:
        return "偏暖可选", "warm"
    if value > -10:
        return "震荡均衡", "neutral"
    if value > -28:
        return "偏弱谨慎", "cautious"
    return "防守等待", "defensive"


def build_market_environment(mainboard: pd.DataFrame, intraday_position: pd.Series) -> dict[str, Any]:
    count = int(len(mainboard))
    pct = pd.to_numeric(mainboard["pct_chg"], errors="coerce").dropna()
    amount = pd.to_numeric(mainboard["amount"], errors="coerce").fillna(0)
    if count <= 0 or pct.empty:
        return {
            "label": "市场温度未知",
            "regime": "unknown",
            "temperature_score": None,
            "note": "全市场快照不足，市场环境层暂不参与可买信号调整。",
        }

    advancers = int((pct > 0).sum())
    decliners = int((pct < 0).sum())
    strong_count = int((pct >= 5).sum())
    weak_count = int((pct <= -5).sum())
    limit_up_count = int((pct >= 9.5).sum())
    limit_down_count = int((pct <= -9.5).sum())
    up_ratio = advancers / len(pct) * 100
    strong_ratio = strong_count / len(pct) * 100
    weak_ratio = weak_count / len(pct) * 100
    limit_spread_ratio = (limit_up_count - limit_down_count) / len(pct) * 100
    median_pct = float(pct.median())
    avg_pct = float(pct.mean())
    high_close_ratio = float((intraday_position >= 0.65).sum() / count * 100)
    low_close_ratio = float((intraday_position <= 0.35).sum() / count * 100)
    total_amount = float(amount.sum())

    score = (
        (up_ratio - 50) * 0.75
        + median_pct * 7.0
        + (strong_ratio - weak_ratio) * 1.15
        + limit_spread_ratio * 4.2
        + (high_close_ratio - low_close_ratio) * 0.22
    )
    score = clamp(score, -60, 60)
    label, regime = market_temperature_label(score)
    risk_appetite = clamp(0.55 + score / 90, 0.2, 1.15)
    score_bonus = clamp(score / 42, -1.0, 0.8)
    price_adjustment_pct = clamp(score / 55, -1.1, 0.5)

    if regime in {"strong", "warm"}:
        note = "市场扩散和情绪较好，可保留少量顺势试错，但仍不追高。"
    elif regime == "neutral":
        note = "市场处于震荡均衡，个股必须依赖行业强度和接入价纪律筛选。"
    else:
        note = "市场温度偏弱，模型会收紧可买信号和接入价格，优先等待确认。"

    return {
        "label": label,
        "regime": regime,
        "temperature_score": round_or_none(score, 2),
        "risk_appetite": round_or_none(risk_appetite, 3),
        "score_bonus": round_or_none(score_bonus, 3),
        "price_adjustment_pct": round_or_none(price_adjustment_pct, 3),
        "mainboard_count": count,
        "advancers": advancers,
        "decliners": decliners,
        "up_ratio_pct": round_or_none(up_ratio),
        "avg_pct_chg": round_or_none(avg_pct),
        "median_pct_chg": round_or_none(median_pct),
        "strong_count": strong_count,
        "weak_count": weak_count,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "high_close_ratio_pct": round_or_none(high_close_ratio),
        "low_close_ratio_pct": round_or_none(low_close_ratio),
        "total_amount": round_or_none(total_amount, 0),
        "note": note,
    }


def theme_strength_label(score: Any) -> tuple[str, str]:
    value = safe_float(score)
    if value is None:
        return "主题强度未知", "unknown"
    if value >= 72:
        return "强势主线", "strong"
    if value >= 58:
        return "活跃偏强", "active"
    if value >= 44:
        return "中性轮动", "neutral"
    if value >= 30:
        return "转弱观察", "weakening"
    return "弱势回避", "weak"


def build_theme_strength(candidate_library: list[Candidate], universe_by_code: dict[str, dict[str, Any]], mainboard_count: int) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for candidate in candidate_library:
        group_name = theme_group(candidate.theme)
        bucket = groups.setdefault(
            group_name,
            {
                "theme_group": group_name,
                "library_count": 0,
                "matched_count": 0,
                "layer_score_sum": 0.0,
                "rank_score_sum": 0.0,
                "pct_sum": 0.0,
                "positive_count": 0,
                "strong_count": 0,
                "fund_score_sum": 0.0,
                "fund_count": 0,
                "top120_count": 0,
                "members": [],
            },
        )
        bucket["library_count"] += 1
        scan = universe_by_code.get(candidate.code)
        if not scan:
            continue
        layer_score = safe_float(scan.get("layer_one_score")) or 0.0
        pct_chg = safe_float(scan.get("pct_chg")) or 0.0
        rank = safe_float(scan.get("layer_one_rank"))
        rank_score = 0.0
        if rank is not None and mainboard_count:
            rank_score = max(0.0, 100 * (1 - (rank - 1) / max(1, mainboard_count - 1)))
        fund_score = safe_float(scan.get("fund_flow_score"))
        bucket["matched_count"] += 1
        bucket["layer_score_sum"] += layer_score
        bucket["rank_score_sum"] += rank_score
        bucket["pct_sum"] += pct_chg
        bucket["positive_count"] += 1 if pct_chg > 0 else 0
        bucket["strong_count"] += 1 if pct_chg >= 5 else 0
        if rank is not None and rank <= 120:
            bucket["top120_count"] += 1
        if fund_score is not None:
            bucket["fund_score_sum"] += fund_score
            bucket["fund_count"] += 1
        bucket["members"].append(
            {
                "code": candidate.code,
                "name": candidate.name,
                "rank": int(rank) if rank is not None else None,
                "pct_chg": round_or_none(pct_chg),
                "layer_one_score": round_or_none(layer_score),
            }
        )

    rows: list[dict[str, Any]] = []
    for group_name, bucket in groups.items():
        matched = int(bucket["matched_count"])
        if matched:
            avg_layer = bucket["layer_score_sum"] / matched
            avg_rank_score = bucket["rank_score_sum"] / matched
            avg_pct = bucket["pct_sum"] / matched
            positive_ratio = bucket["positive_count"] / matched * 100
            strong_ratio = bucket["strong_count"] / matched * 100
            top120_ratio = bucket["top120_count"] / matched * 100
            avg_fund = bucket["fund_score_sum"] / bucket["fund_count"] if bucket["fund_count"] else 0.0
            raw_strength_score = clamp(
                avg_layer * 0.42
                + avg_rank_score * 0.22
                + positive_ratio * 0.12
                + strong_ratio * 0.12
                + top120_ratio * 0.08
                + avg_fund * 0.35,
                0,
                100,
            )
            sample_confidence = clamp(matched / 4, 0.0, 1.0)
            strength_score = 35 + (raw_strength_score - 35) * sample_confidence
        else:
            avg_layer = avg_rank_score = avg_pct = positive_ratio = strong_ratio = top120_ratio = avg_fund = 0.0
            raw_strength_score = 0.0
            sample_confidence = 0.0
            strength_score = 0.0
        label, regime = theme_strength_label(strength_score)
        members = sorted(
            bucket["members"],
            key=lambda item: item["rank"] if item.get("rank") is not None else 999999,
        )[:8]
        rows.append(
            {
                "theme_group": group_name,
                "label": label,
                "regime": regime,
                "strength_score": round_or_none(strength_score, 2),
                "raw_strength_score": round_or_none(raw_strength_score, 2),
                "sample_confidence": round_or_none(sample_confidence, 3),
                "score_bonus": round_or_none(clamp((strength_score - 50) / 35, -0.7, 0.9), 3),
                "library_count": int(bucket["library_count"]),
                "matched_count": matched,
                "avg_layer_score": round_or_none(avg_layer),
                "avg_rank_score": round_or_none(avg_rank_score),
                "avg_pct_chg": round_or_none(avg_pct),
                "positive_ratio_pct": round_or_none(positive_ratio),
                "strong_ratio_pct": round_or_none(strong_ratio),
                "top120_ratio_pct": round_or_none(top120_ratio),
                "avg_fund_flow_score": round_or_none(avg_fund),
                "members": members,
            }
        )

    rows.sort(key=lambda item: (item.get("strength_score") or 0, item.get("matched_count") or 0), reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return {
        "schema_version": "1.0",
        "method": "按战略主题库内股票在全主板快照中的排名、涨跌扩散、强势股占比和资金流估算主题温度。",
        "group_count": len(rows),
        "by_group": {row["theme_group"]: row for row in rows},
        "top_groups": rows[:8],
    }


def build_universe_scan(candidate_library: list[Candidate]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    import akshare as ak

    fund_flow_by_code, fund_flow_payload = fetch_market_fund_flow()
    df = ak.stock_zh_a_spot()
    if df.empty:
        raise RuntimeError("stock_zh_a_spot returned empty data")
    market_date = latest_a_share_trade_date()
    quote_snapshot_at = datetime.now(CN_TZ).isoformat(timespec="seconds")

    normalized = df.copy()
    normalized["code"] = normalized["代码"].map(normalize_code)
    normalized["name"] = normalized["名称"].astype(str)
    normalized["close"] = pd.to_numeric(normalized["最新价"], errors="coerce")
    normalized["pct_chg"] = pd.to_numeric(normalized["涨跌幅"], errors="coerce")
    normalized["amount"] = pd.to_numeric(normalized["成交额"], errors="coerce")
    normalized["high"] = pd.to_numeric(normalized["最高"], errors="coerce")
    normalized["low"] = pd.to_numeric(normalized["最低"], errors="coerce")

    mainboard = normalized[
        normalized["code"].map(is_mainboard_code)
        & normalized["close"].gt(2)
        & normalized["amount"].gt(30_000_000)
        & ~normalized["name"].str.contains("ST|退", regex=True, na=False)
    ].copy()

    if mainboard.empty:
        raise RuntimeError("no main-board rows after filtering")

    price_range = (mainboard["high"] - mainboard["low"]).replace(0, np.nan)
    intraday_position = ((mainboard["close"] - mainboard["low"]) / price_range).clip(0, 1).fillna(0.5)
    momentum_rank = percentile_rank(mainboard["pct_chg"])
    liquidity_rank = percentile_rank(mainboard["amount"])
    position_rank = percentile_rank(intraday_position)
    for key in FUND_FLOW_KEYS:
        mainboard[key] = mainboard["code"].map(lambda code, field=key: fund_flow_by_code.get(code, {}).get(field))
    mainboard["fund_today_main_net"] = pd.to_numeric(mainboard["fund_today_main_net"], errors="coerce")
    mainboard["fund_today_main_net_pct"] = pd.to_numeric(mainboard["fund_today_main_net_pct"], errors="coerce")
    mainboard["fund_5d_main_net"] = pd.to_numeric(mainboard["fund_5d_main_net"], errors="coerce")
    mainboard["fund_5d_main_net_pct"] = pd.to_numeric(mainboard["fund_5d_main_net_pct"], errors="coerce")
    fund_available = (
        mainboard["fund_today_main_net_pct"].notna()
        | mainboard["fund_5d_main_net_pct"].notna()
        | mainboard["fund_today_main_net"].notna()
        | mainboard["fund_5d_main_net"].notna()
    )
    today_pct_base = mainboard["fund_today_main_net_pct"].fillna(mainboard["fund_today_main_net_pct"].median()).fillna(0)
    five_pct_base = mainboard["fund_5d_main_net_pct"].fillna(mainboard["fund_5d_main_net_pct"].median()).fillna(0)
    today_net_base = mainboard["fund_today_main_net"].fillna(mainboard["fund_today_main_net"].median()).fillna(0)
    five_net_base = mainboard["fund_5d_main_net"].fillna(mainboard["fund_5d_main_net"].median()).fillna(0)
    fund_today_pct_rank = percentile_rank(today_pct_base)
    fund_five_pct_rank = percentile_rank(five_pct_base)
    fund_net_rank = percentile_rank(today_net_base)
    fund_five_net_rank = percentile_rank(five_net_base)
    fund_score = (
        (fund_today_pct_rank - 0.5) * 10
        + (fund_five_pct_rank - 0.5) * 6
        + (fund_net_rank - 0.5) * 7
        + (fund_five_net_rank - 0.5) * 7
    ).where(fund_available, 0).clip(-15, 15)
    mainboard["fund_flow_score"] = fund_score.round(2)
    mainboard["fund_flow_label"] = mainboard["fund_flow_score"].map(fund_flow_label)
    mainboard.loc[~fund_available, "fund_flow_label"] = "资金流暂缺"
    mainboard["fund_flow_rank"] = mainboard["fund_flow_score"].rank(ascending=False, method="min").where(fund_available)
    mainboard["fund_flow_bonus"] = mainboard["fund_flow_score"].map(
        lambda value: round_or_none(clamp(float(value) / 20, -0.7, 0.8))
    )
    heat_penalty = (mainboard["pct_chg"] - 7.5).clip(lower=0).fillna(0) * 4
    weak_penalty = (-mainboard["pct_chg"]).clip(lower=0).fillna(0) * 1.2

    mainboard["layer_one_score"] = (
        momentum_rank * 48
        + liquidity_rank * 34
        + position_rank * 18
        + mainboard["fund_flow_score"]
        - heat_penalty
        - weak_penalty
    ).round(2)
    mainboard = mainboard.sort_values(["layer_one_score", "amount"], ascending=[False, False]).reset_index(drop=True)
    mainboard["layer_one_rank"] = mainboard.index + 1
    market_environment = build_market_environment(mainboard, intraday_position)

    universe_by_code: dict[str, dict[str, Any]] = {}
    for row in mainboard.itertuples(index=False):
        universe_by_code[row.code] = {
            "code": row.code,
            "name": row.name,
            "close": round_or_none(row.close),
            "pct_chg": round_or_none(row.pct_chg),
            "amount": round_or_none(row.amount, 0),
            "quote_date": market_date,
            "quote_snapshot_at": quote_snapshot_at,
            "layer_one_score": round_or_none(row.layer_one_score),
            "layer_one_rank": int(row.layer_one_rank),
            **fund_meta_from_record(row._asdict()),
        }

    library_codes = {candidate.code for candidate in candidate_library}
    theme_strength = build_theme_strength(candidate_library, universe_by_code, int(len(mainboard)))
    matched = [record for code, record in universe_by_code.items() if code in library_codes]
    top_mainboard = [
        {
            "rank": int(row.layer_one_rank),
            "code": row.code,
            "name": row.name,
            "close": round_or_none(row.close),
            "pct_chg": round_or_none(row.pct_chg),
            "amount": round_or_none(row.amount, 0),
            "quote_date": market_date,
            "quote_snapshot_at": quote_snapshot_at,
            "layer_one_score": round_or_none(row.layer_one_score),
            **fund_meta_from_record(row._asdict()),
        }
        for row in mainboard.head(UNIVERSE_EXPORT_LIMIT).itertuples(index=False)
    ]

    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "market_date": market_date,
        "quote_snapshot_at": quote_snapshot_at,
        "source": "akshare.stock_zh_a_spot / Sina intraday snapshot",
        "scope": "全A股主板",
        "raw_count": int(len(normalized)),
        "mainboard_count": int(len(mainboard)),
        "strategic_library_count": len(library_codes),
        "matched_library_count": len(matched),
        "shortlist_limit": DEEP_ANALYSIS_LIMIT,
        "export_limit": UNIVERSE_EXPORT_LIMIT,
        "note": "第一层扫描全主板行情快照，第二层只对可解释战略主题库中的入围标的做深度打分。",
        "market_environment": market_environment,
        "theme_strength": theme_strength,
        "fund_flow": fund_flow_payload,
        "top_mainboard": top_mainboard,
        "matched_library": sorted(matched, key=lambda item: item["layer_one_rank"])[:UNIVERSE_EXPORT_LIMIT],
    }
    return universe_by_code, payload


def fetch_sina_live_quotes(codes: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    quotes: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    symbols = [ak_symbol(code) for code in codes if code]
    quote_snapshot_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    headers = {
        "Referer": "https://finance.sina.com.cn/",
        "User-Agent": "Mozilla/5.0",
    }
    for start in range(0, len(symbols), 60):
        chunk = symbols[start : start + 60]
        if not chunk:
            continue
        url = f"https://hq.sinajs.cn/list={','.join(chunk)}"
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            response.encoding = response.apparent_encoding or "gb18030"
        except Exception as exc:
            warnings.append(f"Sina逐股实时行情获取失败：{compact_error(exc)}")
            continue
        for match in re.finditer(r"var hq_str_(?:sh|sz)(\d{6})=\"([^\"]*)\";", response.text):
            code, payload = match.groups()
            parts = payload.split(",")
            if len(parts) < 32:
                continue
            close = safe_float(parts[3])
            quote_date = parts[30].strip()
            quote_time = parts[31].strip()
            if close is None or close <= 0 or not quote_date:
                continue
            quotes[code] = {
                "live_close": round_or_none(close),
                "live_quote_date": quote_date,
                "live_quote_snapshot_at": quote_snapshot_at,
                "live_quote_time": quote_time,
            }
    return quotes, warnings


def select_candidates_from_universe(candidate_library: list[Candidate]) -> tuple[list[Candidate], dict[str, dict[str, Any]], dict[str, Any], list[str]]:
    warnings: list[str] = []
    try:
        universe_by_code, universe_payload = build_universe_scan(candidate_library)
    except Exception as exc:
        warnings.append(f"全主板第一层扫描失败，回退到战略主题库：{exc}")
        fallback_candidates = candidate_library[:DEEP_ANALYSIS_LIMIT]
        live_quotes, quote_warnings = fetch_sina_live_quotes([candidate.code for candidate in fallback_candidates])
        warnings.extend(quote_warnings)
        fallback_meta = {
            candidate.code: {
                "candidate_source": "主题库兜底",
                "layer_one_score": None,
                "layer_one_rank": None,
                "layer_one_pct_chg": None,
                "layer_one_amount": None,
                "live_quote_date": live_quotes.get(candidate.code, {}).get("live_quote_date"),
                "live_quote_snapshot_at": live_quotes.get(candidate.code, {}).get("live_quote_snapshot_at"),
                "live_close": live_quotes.get(candidate.code, {}).get("live_close"),
                "layer_one_bonus": 0.0,
                "theme_group": theme_group(candidate.theme),
                "theme_strength_score": None,
                "theme_strength_label": "主题强度未知",
                "theme_strength_rank": None,
                "theme_strength_bonus": 0.0,
                **empty_fund_flow_meta(),
            }
            for candidate in fallback_candidates
        }
        universe_payload = {
            "schema_version": "1.0",
            "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "source": "fallback",
            "scope": "战略主题库",
            "raw_count": 0,
            "mainboard_count": 0,
            "strategic_library_count": len(candidate_library),
            "matched_library_count": len(fallback_meta),
            "shortlist_limit": DEEP_ANALYSIS_LIMIT,
            "export_limit": UNIVERSE_EXPORT_LIMIT,
            "note": warnings[0],
            "market_environment": {
                "label": "市场温度未知",
                "regime": "unknown",
                "temperature_score": None,
                "risk_appetite": None,
                "score_bonus": 0.0,
                "price_adjustment_pct": 0.0,
                "note": "全市场扫描失败，市场环境层暂不参与信号调整。",
            },
            "theme_strength": {
                "schema_version": "1.0",
                "method": "fallback",
                "group_count": 0,
                "by_group": {},
                "top_groups": [],
            },
            "top_mainboard": [],
            "matched_library": [],
        }
        quote_dates = [quote.get("live_quote_date") for quote in live_quotes.values() if quote.get("live_quote_date")]
        if quote_dates:
            universe_payload["market_date"] = max(quote_dates)
            universe_payload["quote_snapshot_at"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
            universe_payload["source"] = "fallback + Sina live quotes"
            universe_payload["note"] = f"{universe_payload['note']} 已用Sina逐股行情补充候选池实时价格。"
        return fallback_candidates, fallback_meta, universe_payload, warnings
    warnings.extend(universe_payload.get("fund_flow", {}).get("warnings", []))

    scored: list[tuple[float, Candidate]] = []
    meta_by_code: dict[str, dict[str, Any]] = {}
    theme_strength_by_group = (universe_payload.get("theme_strength") or {}).get("by_group", {})
    for candidate in candidate_library:
        scan = universe_by_code.get(candidate.code)
        if not scan:
            continue
        group_name = theme_group(candidate.theme)
        theme_meta = theme_strength_by_group.get(group_name, {})
        theme_bonus = safe_float(theme_meta.get("score_bonus")) or 0.0
        first_layer_score = safe_float(scan.get("layer_one_score")) or 0.0
        combined_score = first_layer_score + candidate.base_score * 7 + theme_bonus * 10
        meta_by_code[candidate.code] = {
            "candidate_source": "全主板第一层入围",
            "layer_one_score": round_or_none(first_layer_score),
            "layer_one_rank": scan.get("layer_one_rank"),
            "layer_one_pct_chg": scan.get("pct_chg"),
            "layer_one_amount": scan.get("amount"),
            "live_quote_date": scan.get("quote_date") or universe_payload.get("market_date"),
            "live_quote_snapshot_at": scan.get("quote_snapshot_at") or universe_payload.get("quote_snapshot_at"),
            "live_close": scan.get("close"),
            "layer_one_bonus": round_or_none(min(2.2, first_layer_score / 100 * 2.2)),
            "theme_group": group_name,
            "theme_strength_score": theme_meta.get("strength_score"),
            "theme_strength_label": theme_meta.get("label") or "主题强度未知",
            "theme_strength_rank": theme_meta.get("rank"),
            "theme_strength_bonus": round_or_none(theme_bonus, 3),
            **fund_meta_from_record(scan),
        }
        scored.append((combined_score, candidate))

    if len(scored) < min(FINAL_POOL_SIZE, 6):
        warnings.append("全主板扫描匹配到的战略候选不足，已用主题库兜底补齐。")
        existing = {candidate.code for _, candidate in scored}
        supplement_candidates = [candidate for candidate in candidate_library if candidate.code not in existing]
        live_quotes, quote_warnings = fetch_sina_live_quotes([candidate.code for candidate in supplement_candidates[:DEEP_ANALYSIS_LIMIT]])
        warnings.extend(quote_warnings)
        for candidate in candidate_library:
            if candidate.code in existing:
                continue
            live_quote = live_quotes.get(candidate.code, {})
            meta_by_code[candidate.code] = {
                "candidate_source": "主题库补齐",
                "layer_one_score": None,
                "layer_one_rank": None,
                "layer_one_pct_chg": None,
                "layer_one_amount": None,
                "live_quote_date": live_quote.get("live_quote_date"),
                "live_quote_snapshot_at": live_quote.get("live_quote_snapshot_at"),
                "live_close": live_quote.get("live_close"),
                "layer_one_bonus": 0.0,
                "theme_group": theme_group(candidate.theme),
                "theme_strength_score": None,
                "theme_strength_label": "主题强度未知",
                "theme_strength_rank": None,
                "theme_strength_bonus": 0.0,
                **empty_fund_flow_meta(),
            }
            scored.append((candidate.base_score * 7, candidate))
            if len(scored) >= DEEP_ANALYSIS_LIMIT:
                break

    scored.sort(key=lambda item: item[0], reverse=True)
    selected = [candidate for _, candidate in scored[:DEEP_ANALYSIS_LIMIT]]
    universe_payload["deep_analysis_count"] = len(selected)
    universe_payload["final_pool_size"] = FINAL_POOL_SIZE
    return selected, meta_by_code, universe_payload, warnings


def parse_date_value(value: Any):
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_datetime_value(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    return dt.astimezone(CN_TZ)


def snapshot_sort_key(snapshot: dict[str, Any]) -> tuple[Any, str]:
    dt = parse_datetime_value(snapshot.get("generated_at"))
    if dt is not None:
        return dt, str(snapshot.get("snapshot_id") or "")
    parsed_date = parse_date_value(snapshot.get("as_of_date"))
    fallback_dt = datetime.combine(parsed_date or datetime.min.date(), datetime.min.time(), tzinfo=CN_TZ)
    return fallback_dt, str(snapshot.get("snapshot_id") or "")


def load_history_snapshots() -> list[dict[str, Any]]:
    if not HISTORY_DIR.exists():
        return []

    snapshots_by_key: dict[str, dict[str, Any]] = {}
    paths = [*HISTORY_DIR.glob("*.json")]
    if HISTORY_SNAPSHOT_DIR.exists():
        paths.extend(HISTORY_SNAPSHOT_DIR.glob("*.json"))

    for path in sorted(paths):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        stocks = data.get("stocks")
        if not isinstance(stocks, list):
            continue

        as_of_date = data.get("as_of_date") or path.stem[:10]
        generated_at = data.get("generated_at")
        snapshot_id = str(generated_at or path.stem)
        key = f"{as_of_date}|{snapshot_id}"
        if key in snapshots_by_key:
            continue

        snapshots_by_key[key] = {
            "as_of_date": as_of_date,
            "generated_at": generated_at,
            "snapshot_id": snapshot_id,
            "update_phase": data.get("update_phase"),
            "update_phase_label": data.get("update_phase_label"),
            "universe_scan": data.get("universe_scan") or {},
            "stocks": stocks,
        }
    return sorted(snapshots_by_key.values(), key=snapshot_sort_key)


def tracking_entry_key(date_value: Any, source: str, snapshot_id: Any = None) -> str:
    return f"{date_value}|{source}|{snapshot_id or ''}"


TRACKING_SOURCE_ORDER = {
    "review_first": 0,
    "history_snapshot": 1,
    "review_latest": 2,
    "current_pool": 3,
    "latest_quote": 4,
}


def tracking_sort_key(item: dict[str, Any]) -> tuple[Any, int]:
    dt = parse_datetime_value(item.get("generated_at"))
    if dt is None:
        parsed_date = parse_date_value(item.get("date"))
        dt = datetime.combine(parsed_date or datetime.min.date(), datetime.min.time(), tzinfo=CN_TZ)
    return (
        dt,
        TRACKING_SOURCE_ORDER.get(str(item.get("source") or ""), 50),
    )


def attach_tracking(rows: list[dict[str, Any]], as_of_date: str) -> None:
    entries_by_code: dict[str, dict[str, dict[str, Any]]] = {row["code"]: {} for row in rows}

    for record in load_existing_review_records():
        code = record.get("code")
        if code not in entries_by_code:
            continue

        first_price = safe_float(record.get("first_recommend_price"))
        first_date = record.get("first_recommend_date")
        if first_date and first_price is not None:
            entries_by_code[code][tracking_entry_key(first_date, "review_first")] = {
                "date": str(first_date),
                "close": first_price,
                "rank": record.get("first_rank"),
                "status_key": record.get("first_status_key"),
                "source": "review_first",
            }

        latest_price = safe_float(record.get("latest_price"))
        latest_date = record.get("latest_date")
        if latest_date and latest_price is not None:
            entries_by_code[code][tracking_entry_key(latest_date, "review_latest")] = {
                "date": str(latest_date),
                "close": latest_price,
                "rank": record.get("last_seen_rank"),
                "status_key": record.get("current_status_key"),
                "source": "review_latest",
            }

    for snapshot in load_history_snapshots():
        snapshot_date = snapshot.get("as_of_date")
        if not snapshot_date:
            continue
        snapshot_id = snapshot.get("snapshot_id") or snapshot.get("generated_at")
        for stock in snapshot.get("stocks", []):
            code = stock.get("code")
            if code not in entries_by_code:
                continue
            close = safe_float(stock.get("close"))
            if close is None:
                continue
            entries_by_code[code][tracking_entry_key(snapshot_date, "history", snapshot_id)] = {
                "date": str(snapshot_date),
                "generated_at": snapshot.get("generated_at"),
                "close": close,
                "rank": stock.get("rank"),
                "status_key": stock.get("status_key"),
                "source": "history_snapshot",
            }

    for row in rows:
        close = safe_float(row.get("close"))
        if close is not None:
            entries_by_code[row["code"]][tracking_entry_key(as_of_date, "current")] = {
                "date": as_of_date,
                "close": close,
                "rank": row.get("rank"),
                "status_key": row.get("status_key"),
                "source": "current_pool",
            }

    current_date = parse_date_value(as_of_date)

    for row in rows:
        series = sorted(
            entries_by_code[row["code"]].values(),
            key=tracking_sort_key,
        )

        if not series:
            row["tracking"] = {
                "status": "待回访",
                "comment": "暂无历史推荐快照，等待下一次自动刷新后开始跟踪。",
            }
            continue

        first = series[0]
        latest = series[-1]
        first_close = safe_float(first.get("close"))
        latest_close = safe_float(latest.get("close"))
        max_entry = max(series, key=lambda item: safe_float(item.get("close")) or float("-inf"))
        min_entry = min(series, key=lambda item: safe_float(item.get("close")) or float("inf"))
        max_close = safe_float(max_entry.get("close"))
        min_close = safe_float(min_entry.get("close"))

        return_pct = None
        max_return_pct = None
        drawdown_from_peak_pct = None
        if first_close and latest_close is not None:
            return_pct = (latest_close / first_close - 1) * 100
        if first_close and max_close is not None:
            max_return_pct = (max_close / first_close - 1) * 100
        if latest_close is not None and max_close:
            drawdown_from_peak_pct = (latest_close / max_close - 1) * 100

        first_date = parse_date_value(first.get("date"))
        tracking_days = None
        if first_date and current_date:
            tracking_days = (current_date - first_date).days

        if return_pct is None:
            tracking_status = "待回访"
            comment = "缺少可用价格，暂不评价推荐后表现。"
        elif return_pct >= 5:
            tracking_status = "正反馈"
            comment = "推荐后已有正收益，后续重点跟踪是否继续跑赢自身观察区间。"
        elif return_pct <= -5:
            tracking_status = "负反馈"
            comment = "推荐后跌幅较明显，需要复核产业链逻辑、价格纪律和风控阈值。"
        else:
            tracking_status = "继续观察"
            comment = "推荐后涨跌幅仍在验证区间，继续观察催化剂和量价结构。"

        row["tracking"] = {
            "status": tracking_status,
            "first_recommend_date": first.get("date"),
            "first_recommend_price": round_or_none(first_close),
            "first_rank": first.get("rank"),
            "first_status_key": first.get("status_key"),
            "first_source": first.get("source"),
            "latest_date": latest.get("date"),
            "latest_price": round_or_none(latest_close),
            "tracking_days": tracking_days,
            "snapshot_count": len(series),
            "return_since_first_pct": round_or_none(return_pct),
            "max_return_since_first_pct": round_or_none(max_return_pct),
            "drawdown_from_peak_pct": round_or_none(drawdown_from_peak_pct),
            "best_close": round_or_none(max_close),
            "best_date": max_entry.get("date"),
            "worst_close": round_or_none(min_close),
            "worst_date": min_entry.get("date"),
            "comment": comment,
        }


def build_tracking_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tracked = [row for row in rows if row.get("tracking", {}).get("return_since_first_pct") is not None]
    if not tracked:
        return {
            "tracked_count": 0,
            "average_return_pct": None,
            "positive_count": 0,
            "negative_count": 0,
            "best": None,
            "worst": None,
        }

    def tracking_return(row: dict[str, Any]) -> float:
        return float(row["tracking"]["return_since_first_pct"])

    returns = [tracking_return(row) for row in tracked]
    best = max(tracked, key=tracking_return)
    worst = min(tracked, key=tracking_return)
    return {
        "tracked_count": len(tracked),
        "average_return_pct": round_or_none(sum(returns) / len(returns)),
        "positive_count": sum(1 for value in returns if value > 0),
        "negative_count": sum(1 for value in returns if value < 0),
        "best": {
            "code": best["code"],
            "name": best["name"],
            "return_since_first_pct": best["tracking"]["return_since_first_pct"],
        },
        "worst": {
            "code": worst["code"],
            "name": worst["name"],
            "return_since_first_pct": worst["tracking"]["return_since_first_pct"],
        },
    }


def theme_group(theme: Any) -> str:
    text = str(theme or "").strip()
    if "\ufffd" in text or (text.count("?") >= 2 and text.count("?") / max(1, len(text)) >= 0.25):
        return "未分组"
    groups = [
        ("AI半导体", ("AI", "半导体", "PCB", "封装", "光模块", "算力", "电子材料", "封测")),
        ("电力设备", ("电网", "电力", "特高压", "输变电", "变压器", "海缆", "能源装备", "燃机")),
        ("资源周期", ("铜", "黄金", "铝", "稀土", "金属", "矿", "锡", "资源")),
        ("高端制造", ("机器人", "汽车", "智能", "热管理", "精密传动", "控制电机")),
        ("消费医药", ("医药", "中药", "白酒", "家电", "乳制品", "消费")),
        ("大金融", ("银行", "证券", "保险", "金融")),
        ("军工船舶", ("船", "军", "航空", "发动机", "大飞机")),
    ]
    for label, keywords in groups:
        if any(keyword in text for keyword in keywords):
            return label
    return text.split("/")[0] if text else "未分组"


def layer_rank_bucket(rank: Any) -> str:
    value = safe_float(rank)
    if value is None:
        return "未入全主板排名"
    if value <= 50:
        return "前50"
    if value <= 120:
        return "前120"
    if value <= 300:
        return "前300"
    return "300名以后"


def entry_gap_bucket(gap_pct: Any) -> str:
    value = safe_float(gap_pct)
    if value is None:
        return "接入偏离未知"
    if value <= -3:
        return "低于接入价"
    if value <= 3:
        return "贴近接入价"
    if value <= 10:
        return "略高于接入价"
    return "明显高于接入价"


def update_phase_from_timestamp(value: Any = None) -> tuple[str, str]:
    scheduled_cron = (
        os.getenv("GITHUB_EVENT_SCHEDULE")
        or os.getenv("A_SHARE_LI_SCHEDULE_CRON")
        or ""
    ).strip()
    if scheduled_cron in SCHEDULE_PHASE_MAP:
        return SCHEDULE_PHASE_MAP[scheduled_cron]

    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            dt = None
    if dt is None:
        dt = datetime.now(CN_TZ)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CN_TZ)
    local = dt.astimezone(CN_TZ)
    minutes = local.hour * 60 + local.minute
    # Ad-hoc and recovery runs inherit the latest completed checkpoint instead
    # of being labeled as a future checkpoint.
    if minutes < 11 * 60 + 20:
        return "morning_entry", "10点早盘接入"
    if minutes < 13 * 60 + 30:
        return "morning_entry", "11:20午前买入复检"
    if minutes < 14 * 60 + 30:
        return "morning_entry", "13:30午后买入复检"
    if minutes < 20 * 60:
        return "afternoon_risk", "14:30尾盘风控"
    return "evening_watch", "20点次日关注"


def clean_feedback_value(value: Any) -> str:
    return str(value or "未知").replace("\n", " ").strip() or "未知"


def feedback_value_is_noisy(value: Any) -> bool:
    text = clean_feedback_value(value)
    if text in {"?", "??", "???", "????", "?????", "??????"}:
        return True
    if "\ufffd" in text or "锟斤拷" in text:
        return True
    question_count = text.count("?")
    return question_count >= 2 and question_count / max(1, len(text)) >= 0.25


def clean_display_text(value: Any, fallback: Any = "未知") -> Any:
    text = clean_feedback_value(value)
    if not text or feedback_value_is_noisy(text):
        return fallback
    return text


def chip_value_is_missing(value: Any) -> bool:
    text = clean_feedback_value(value)
    if feedback_value_is_noisy(text):
        return True
    return "暂缺" in text or text in {"未知", "筹码未知", "筹码数据暂缺"}


def feedback_factor_id(dimension: str, value: Any) -> str:
    return f"{dimension}:{clean_feedback_value(value)}"


def feedback_factor_label(dimension: str, value: Any) -> str:
    return f"{FEEDBACK_DIMENSION_LABELS.get(dimension, dimension)}={value or '未知'}"


def build_feedback_factor(dimension: str, value: Any) -> dict[str, str] | None:
    if dimension == "chip" and chip_value_is_missing(value):
        return None
    if feedback_value_is_noisy(value):
        return None
    clean_value = clean_feedback_value(value)
    return {
        "id": feedback_factor_id(dimension, clean_value),
        "dimension": dimension,
        "value": clean_value,
        "label": feedback_factor_label(dimension, clean_value),
    }


def stock_feedback_factors(stock: dict[str, Any]) -> list[dict[str, str]]:
    raw_factors = [
        ("theme_group", theme_group(stock.get("theme"))),
        ("buy_signal", stock.get("buy_signal_label") or stock.get("buy_signal_key") or "未知"),
        ("status", stock.get("intervention_status") or stock.get("status_key") or "未知"),
        ("fund_flow", stock.get("fund_flow_label") or "资金流暂缺"),
        ("chip", stock.get("chip_label") or "筹码暂缺"),
        ("layer_rank", layer_rank_bucket(stock.get("layer_one_rank"))),
        ("entry_gap", entry_gap_bucket(stock.get("entry_gap_pct"))),
        ("price_source", stock.get("price_source") or "未知"),
        ("market_regime", stock.get("feedback_market_regime") or stock.get("market_regime") or "unknown"),
        ("update_phase", stock.get("update_phase_label") or stock.get("update_phase") or "unknown"),
    ]
    factors: list[dict[str, str]] = []
    for dimension, value in raw_factors:
        factor = build_feedback_factor(dimension, value)
        if factor:
            factors.append(factor)
    return factors


def entry_effectiveness_factors(stock: dict[str, Any]) -> list[dict[str, str]]:
    return [
        factor
        for factor in stock_feedback_factors(stock)
        if factor["dimension"] in ENTRY_EFFECTIVENESS_DIMENSION_WEIGHTS
    ]


def entry_reference_price(stock: dict[str, Any]) -> float | None:
    meta = entry_reference_meta(stock, [])
    return safe_float(meta.get("price")) if meta else None


def entry_reference_meta(stock: dict[str, Any], future_lows: list[float]) -> dict[str, Any] | None:
    close = safe_float(stock.get("close"))
    buyable = safe_float(stock.get("buyable_price"))
    recommended = safe_float(stock.get("recommended_entry_price"))
    entry_gap = safe_float(stock.get("entry_gap_pct"))
    signal = stock.get("buy_signal_key")
    status = stock.get("status_key")

    if signal in {"pullback_buy", "breakout_buy"} and buyable:
        return {
            "price": buyable,
            "sample_type": "actual_buyable",
            "sample_type_label": "当时可买",
            "touched": True,
            "weight": 1.0,
        }
    if stock.get("is_buyable_now") and buyable:
        return {
            "price": buyable,
            "sample_type": "actual_buyable",
            "sample_type_label": "当时可买",
            "touched": True,
            "weight": 1.0,
        }
    if not recommended:
        return None

    touched = any(low <= recommended for low in future_lows if low is not None)
    if close is not None and close <= recommended * 1.005:
        touched = True
    if touched:
        return {
            "price": recommended,
            "sample_type": "touched_entry",
            "sample_type_label": "后续触达接入价",
            "touched": True,
            "weight": 0.75,
        }

    wait_weight = 0.35
    if status in {"watch", "breakout"} or (entry_gap is not None and entry_gap <= 8):
        wait_weight = 0.5
    return {
        "price": recommended,
        "sample_type": "untouched_wait",
        "sample_type_label": "未触达等待价",
        "touched": False,
        "weight": wait_weight,
    }


def future_closes_for_code(snapshots: list[dict[str, Any]], start_index: int, horizon: int, code: str) -> list[float]:
    closes: list[float] = []
    end_index = min(len(snapshots) - 1, start_index + horizon)
    for index in range(start_index + 1, end_index + 1):
        for stock in snapshots[index].get("stocks", []):
            if stock.get("code") != code:
                continue
            close = safe_float(stock.get("close"))
            if close is not None:
                closes.append(close)
            break
    return closes


def future_snapshot_index_for_horizon(snapshots: list[dict[str, Any]], start_index: int, horizon: int) -> int | None:
    start_date = parse_date_value(snapshots[start_index].get("as_of_date"))
    future_dates: list[Any] = []
    date_to_latest_index: dict[Any, int] = {}
    for index in range(start_index + 1, len(snapshots)):
        snapshot_date = parse_date_value(snapshots[index].get("as_of_date"))
        if snapshot_date is None:
            continue
        if start_date is not None and snapshot_date <= start_date:
            continue
        if snapshot_date not in date_to_latest_index:
            future_dates.append(snapshot_date)
        date_to_latest_index[snapshot_date] = index

    if len(future_dates) < horizon:
        return None
    return date_to_latest_index.get(future_dates[horizon - 1])


def future_lows_for_code(
    snapshots: list[dict[str, Any]],
    start_index: int,
    horizon: int,
    code: str,
    daily_cache: dict[str, pd.DataFrame],
) -> list[float]:
    start_date = parse_date_value(snapshots[start_index].get("as_of_date"))
    end_index = min(len(snapshots) - 1, start_index + horizon)
    end_date = parse_date_value(snapshots[end_index].get("as_of_date"))
    lows: list[float] = []

    if start_date and end_date:
        try:
            if code not in daily_cache:
                daily_cache[code] = load_daily(code)
            df = daily_cache[code]
            dates = pd.to_datetime(df["date"]).dt.date
            window = df[(dates > start_date) & (dates <= end_date)]
            lows = [
                value
                for value in (safe_float(item) for item in window.get("low", []))
                if value is not None
            ]
        except Exception:
            lows = []

    if not lows:
        lows = future_closes_for_code(snapshots, start_index, horizon, code)
    return lows


def snapshot_return(start_stock: dict[str, Any], future_stock: dict[str, Any]) -> float | None:
    start_close = safe_float(start_stock.get("close"))
    future_close = safe_float(future_stock.get("close"))
    if not start_close or future_close is None:
        return None
    return (future_close / start_close - 1) * 100


def snapshot_benchmark_return(start_stocks: list[dict[str, Any]], future_stocks: list[dict[str, Any]]) -> float:
    future_by_code = {stock.get("code"): stock for stock in future_stocks if stock.get("code")}
    returns: list[float] = []
    for stock in start_stocks:
        code = stock.get("code")
        if not code or code not in future_by_code:
            continue
        value = snapshot_return(stock, future_by_code[code])
        if value is not None:
            returns.append(value)
    return sum(returns) / len(returns) if returns else 0.0


def feedback_context_meta(
    market_environment: dict[str, Any] | None = None,
    generated_at: Any = None,
) -> dict[str, Any]:
    market_environment = market_environment or {}
    phase, phase_label = update_phase_from_timestamp(generated_at)
    return {
        "feedback_market_regime": market_environment.get("regime") or "unknown",
        "feedback_market_label": market_environment.get("label") or "市场阶段未知",
        "update_phase": phase,
        "update_phase_label": phase_label,
    }


def apply_feedback_context(rows: list[dict[str, Any]], context: dict[str, Any]) -> None:
    for row in rows:
        for key, value in context.items():
            row[key] = row.get(key) or value


def feedback_snapshot_rows(rows: list[dict[str, Any]], context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    context = context or {}
    for stock in rows:
        close = safe_float(stock.get("close"))
        code = stock.get("code")
        if not code or close is None:
            continue
        item = dict(stock)
        item["close"] = close
        for key, value in context.items():
            item[key] = item.get(key) or value
        cleaned.append(item)
    return cleaned


def build_feedback_snapshots(
    current_rows: list[dict[str, Any]],
    as_of_date: str,
    market_environment: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    snapshots_for_feedback: list[dict[str, Any]] = []
    current_date = parse_date_value(as_of_date)
    for snapshot in load_history_snapshots():
        snapshot_date = str(snapshot.get("as_of_date") or "")
        parsed = parse_date_value(snapshot_date)
        if not snapshot_date or (current_date and parsed and parsed > current_date):
            continue
        snapshot_market = (snapshot.get("universe_scan") or {}).get("market_environment") or {}
        snapshot_context = feedback_context_meta(snapshot_market, snapshot.get("generated_at") or snapshot_date)
        rows = feedback_snapshot_rows(snapshot.get("stocks", []), snapshot_context)
        if rows:
            snapshots_for_feedback.append(
                {
                    "as_of_date": snapshot_date,
                    "generated_at": snapshot.get("generated_at"),
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "stocks": rows,
                }
            )
    current_context = feedback_context_meta(market_environment, datetime.now(CN_TZ))
    current_cleaned = feedback_snapshot_rows(current_rows, current_context)
    if current_cleaned:
        current_id = datetime.now(CN_TZ).isoformat(timespec="seconds")
        snapshots_for_feedback.append(
            {
                "as_of_date": as_of_date,
                "generated_at": current_id,
                "snapshot_id": current_id,
                "stocks": current_cleaned,
            }
        )
    deduped: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots_for_feedback:
        key = f"{snapshot.get('as_of_date')}|{snapshot.get('generated_at') or snapshot.get('snapshot_id') or ''}"
        deduped[key] = snapshot
    return sorted(deduped.values(), key=snapshot_sort_key)


def finalize_feedback_factor(raw: dict[str, Any]) -> dict[str, Any]:
    weighted_count = raw["weighted_count"]
    raw_count = int(raw["raw_count"])
    avg_return = raw["return_sum"] / weighted_count if weighted_count else 0.0
    avg_excess = raw["excess_sum"] / weighted_count if weighted_count else 0.0
    hit_rate = raw["hit_weight"] / weighted_count * 100 if weighted_count else 0.0
    sample_shrink = raw_count / (raw_count + FEEDBACK_MIN_STRONG_SAMPLES)
    shrunk_excess = avg_excess * sample_shrink
    confidence = clamp(sample_shrink * min(1.0, weighted_count / FEEDBACK_MIN_STRONG_SAMPLES), 0.0, 1.0)
    score_effect = clamp(shrunk_excess / 4.0, -0.45, 0.45)
    return {
        "id": raw["id"],
        "dimension": raw["dimension"],
        "value": raw["value"],
        "label": raw["label"],
        "sample_count": raw_count,
        "weighted_sample_count": round_or_none(weighted_count),
        "avg_return_pct": round_or_none(avg_return),
        "avg_excess_return_pct": round_or_none(avg_excess),
        "shrunk_excess_return_pct": round_or_none(shrunk_excess),
        "hit_rate_pct": round_or_none(hit_rate),
        "confidence": round_or_none(confidence, 3),
        "score_effect": round_or_none(score_effect, 3),
        "horizons": sorted(raw["horizons"]),
    }


def finalize_entry_effectiveness_factor(raw: dict[str, Any]) -> dict[str, Any]:
    weighted_count = raw["weighted_count"]
    raw_count = int(raw["raw_count"])
    touched_weight = raw.get("touched_weight", 0.0)
    untouched_weight = raw.get("untouched_weight", 0.0)
    avg_entry_return = raw["entry_return_sum"] / weighted_count if weighted_count else 0.0
    avg_touch_return = raw.get("touch_return_sum", 0.0) / touched_weight if touched_weight else 0.0
    avg_missed_return = raw.get("missed_return_sum", 0.0) / untouched_weight if untouched_weight else 0.0
    avg_adverse_drawdown = raw.get("touch_adverse_drawdown_sum", 0.0) / touched_weight if touched_weight else 0.0
    hit_rate = raw["hit_weight"] / touched_weight * 100 if touched_weight else 0.0
    crash_rate = raw["crash_weight"] / touched_weight * 100 if touched_weight else 0.0
    touch_rate = touched_weight / weighted_count * 100 if weighted_count else 0.0
    untouched_rate = untouched_weight / weighted_count * 100 if weighted_count else 0.0
    sample_shrink = raw_count / (raw_count + ENTRY_FEEDBACK_MIN_SAMPLES)
    confidence = clamp(sample_shrink * min(1.0, weighted_count / ENTRY_FEEDBACK_MIN_SAMPLES), 0.0, 1.0)

    downside_penalty = 0.0
    if touched_weight:
        if avg_touch_return < 0:
            downside_penalty += abs(avg_touch_return) / 4.0
        if avg_adverse_drawdown < -3:
            downside_penalty += (abs(avg_adverse_drawdown) - 3) / 5.0
        downside_penalty += max(0.0, crash_rate - 15) / 35.0

    upside_credit = max(0.0, avg_touch_return) / 8.0 if touched_weight else 0.0
    upside_credit += max(0.0, hit_rate - 55) / 80.0
    if untouched_weight and avg_missed_return > 3:
        upside_credit += min(0.6, (avg_missed_return - 3) / 12.0) * (untouched_weight / weighted_count if weighted_count else 0)
    price_adjustment = clamp(
        (upside_credit - downside_penalty) * sample_shrink,
        ENTRY_FEEDBACK_PRICE_CAP_DOWN,
        ENTRY_FEEDBACK_PRICE_CAP_UP,
    )

    if touched_weight and (crash_rate >= 35 or avg_adverse_drawdown <= -8 or avg_touch_return <= -5):
        risk_level = "高"
    elif touched_weight and (crash_rate >= 20 or avg_adverse_drawdown <= -5 or avg_touch_return < 0):
        risk_level = "中"
    else:
        risk_level = "低"

    return {
        "id": raw["id"],
        "dimension": raw["dimension"],
        "value": raw["value"],
        "label": raw["label"],
        "sample_count": raw_count,
        "weighted_sample_count": round_or_none(weighted_count),
        "avg_entry_return_pct": round_or_none(avg_entry_return),
        "avg_touch_return_pct": round_or_none(avg_touch_return),
        "avg_missed_return_pct": round_or_none(avg_missed_return),
        "avg_adverse_drawdown_pct": round_or_none(avg_adverse_drawdown),
        "hit_rate_pct": round_or_none(hit_rate),
        "crash_rate_pct": round_or_none(crash_rate),
        "touch_rate_pct": round_or_none(touch_rate),
        "untouched_wait_rate_pct": round_or_none(untouched_rate),
        "actual_buyable_count": int(raw.get("actual_buyable_count", 0)),
        "touched_entry_count": int(raw.get("touched_entry_count", 0)),
        "untouched_wait_count": int(raw.get("untouched_wait_count", 0)),
        "confidence": round_or_none(confidence, 3),
        "price_adjustment_pct": round_or_none(price_adjustment, 3),
        "risk_level": risk_level,
        "horizons": sorted(raw["horizons"]),
    }


def build_model_feedback(
    current_rows: list[dict[str, Any]],
    as_of_date: str,
    market_environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshots = build_feedback_snapshots(current_rows, as_of_date, market_environment)
    aggregates: dict[str, dict[str, Any]] = {}
    entry_aggregates: dict[str, dict[str, Any]] = {}
    observations = 0
    entry_observations = 0
    entry_touched_observations = 0
    entry_untouched_observations = 0
    market_regime_counts: dict[str, int] = {}
    update_phase_counts: dict[str, int] = {}
    entry_market_regime_counts: dict[str, int] = {}
    entry_update_phase_counts: dict[str, int] = {}
    daily_cache: dict[str, pd.DataFrame] = {}

    for index, snapshot in enumerate(snapshots):
        start_stocks = snapshot.get("stocks", [])
        if not start_stocks:
            continue
        for horizon in FEEDBACK_HORIZONS:
            future_index = future_snapshot_index_for_horizon(snapshots, index, horizon)
            if future_index is None:
                continue
            future_stocks = snapshots[future_index].get("stocks", [])
            future_by_code = {stock.get("code"): stock for stock in future_stocks if stock.get("code")}
            if not future_by_code:
                continue
            benchmark = snapshot_benchmark_return(start_stocks, future_stocks)
            recency_weight = 0.88 ** max(0, len(snapshots) - future_index - 1)
            horizon_weight = 1 / math.sqrt(horizon)
            weight = recency_weight * horizon_weight
            for stock in start_stocks:
                code = stock.get("code")
                future_stock = future_by_code.get(code)
                if not code or future_stock is None:
                    continue
                return_pct = snapshot_return(stock, future_stock)
                if return_pct is None:
                    continue
                excess_return = return_pct - benchmark
                observations += 1
                market_key = clean_feedback_value(stock.get("feedback_market_regime") or stock.get("market_regime") or "unknown")
                phase_key = clean_feedback_value(stock.get("update_phase_label") or stock.get("update_phase") or "unknown")
                market_regime_counts[market_key] = market_regime_counts.get(market_key, 0) + 1
                update_phase_counts[phase_key] = update_phase_counts.get(phase_key, 0) + 1
                for factor in stock_feedback_factors(stock):
                    bucket = aggregates.setdefault(
                        factor["id"],
                        {
                            "id": factor["id"],
                            "dimension": factor["dimension"],
                            "value": factor["value"],
                            "label": factor["label"],
                            "raw_count": 0,
                            "weighted_count": 0.0,
                            "return_sum": 0.0,
                            "excess_sum": 0.0,
                            "hit_weight": 0.0,
                            "horizons": set(),
                        },
                    )
                    bucket["raw_count"] += 1
                    bucket["weighted_count"] += weight
                    bucket["return_sum"] += return_pct * weight
                    bucket["excess_sum"] += excess_return * weight
                    bucket["hit_weight"] += weight if excess_return > 0 else 0.0
                    bucket["horizons"].add(horizon)

                future_close = safe_float(future_stock.get("close"))
                if future_close is not None:
                    future_lows = future_lows_for_code(snapshots, index, horizon, code, daily_cache)
                    entry_meta = entry_reference_meta(stock, future_lows)
                    if entry_meta and future_lows:
                        entry_price = safe_float(entry_meta.get("price"))
                        sample_weight = weight * (safe_float(entry_meta.get("weight")) or 1.0)
                        sample_type = str(entry_meta.get("sample_type") or "unknown")
                        entry_return = (future_close / entry_price - 1) * 100 if entry_price else None
                        min_future_low = min(future_lows)
                        adverse_drawdown = (min_future_low / entry_price - 1) * 100 if entry_price else None
                        if entry_return is None or adverse_drawdown is None:
                            continue
                        touched = bool(entry_meta.get("touched"))
                        entry_observations += 1
                        if touched:
                            entry_touched_observations += 1
                        else:
                            entry_untouched_observations += 1
                        entry_market_regime_counts[market_key] = entry_market_regime_counts.get(market_key, 0) + 1
                        entry_update_phase_counts[phase_key] = entry_update_phase_counts.get(phase_key, 0) + 1

                        sample_type_factor = {
                            "id": feedback_factor_id("entry_sample_type", entry_meta.get("sample_type_label")),
                            "dimension": "entry_sample_type",
                            "value": str(entry_meta.get("sample_type_label") or sample_type),
                            "label": feedback_factor_label("entry_sample_type", entry_meta.get("sample_type_label")),
                        }
                        for factor in [*entry_effectiveness_factors(stock), sample_type_factor]:
                            entry_bucket = entry_aggregates.setdefault(
                                factor["id"],
                                {
                                    "id": factor["id"],
                                    "dimension": factor["dimension"],
                                    "value": factor["value"],
                                    "label": factor["label"],
                                    "raw_count": 0,
                                    "weighted_count": 0.0,
                                    "entry_return_sum": 0.0,
                                    "touch_return_sum": 0.0,
                                    "missed_return_sum": 0.0,
                                    "touch_adverse_drawdown_sum": 0.0,
                                    "touched_weight": 0.0,
                                    "untouched_weight": 0.0,
                                    "hit_weight": 0.0,
                                    "crash_weight": 0.0,
                                    "actual_buyable_count": 0,
                                    "touched_entry_count": 0,
                                    "untouched_wait_count": 0,
                                    "horizons": set(),
                                },
                            )
                            entry_bucket["raw_count"] += 1
                            entry_bucket["weighted_count"] += sample_weight
                            entry_bucket["entry_return_sum"] += entry_return * sample_weight
                            if sample_type == "actual_buyable":
                                entry_bucket["actual_buyable_count"] += 1
                            elif sample_type == "touched_entry":
                                entry_bucket["touched_entry_count"] += 1
                            elif sample_type == "untouched_wait":
                                entry_bucket["untouched_wait_count"] += 1

                            if touched:
                                entry_bucket["touched_weight"] += sample_weight
                                entry_bucket["touch_return_sum"] += entry_return * sample_weight
                                entry_bucket["touch_adverse_drawdown_sum"] += adverse_drawdown * sample_weight
                                if entry_return > 0 and adverse_drawdown > ENTRY_ADVERSE_DRAW_THRESHOLD:
                                    entry_bucket["hit_weight"] += sample_weight
                                if entry_return <= ENTRY_CRASH_RETURN_THRESHOLD or adverse_drawdown <= ENTRY_ADVERSE_DRAW_THRESHOLD:
                                    entry_bucket["crash_weight"] += sample_weight
                            else:
                                entry_bucket["untouched_weight"] += sample_weight
                                entry_bucket["missed_return_sum"] += entry_return * sample_weight
                            entry_bucket["horizons"].add(horizon)

    factor_stats = [finalize_feedback_factor(raw) for raw in aggregates.values()]
    factor_stats.sort(key=lambda item: (abs(item.get("score_effect") or 0), item.get("sample_count") or 0), reverse=True)
    positive = [item for item in factor_stats if (item.get("score_effect") or 0) > 0]
    negative = [item for item in factor_stats if (item.get("score_effect") or 0) < 0]
    entry_factor_stats = [finalize_entry_effectiveness_factor(raw) for raw in entry_aggregates.values()]
    entry_factor_stats.sort(
        key=lambda item: (
            item.get("risk_level") == "高",
            abs(item.get("price_adjustment_pct") or 0),
            item.get("sample_count") or 0,
        ),
        reverse=True,
    )
    entry_positive = [item for item in entry_factor_stats if (item.get("price_adjustment_pct") or 0) > 0]
    entry_negative = [item for item in entry_factor_stats if (item.get("price_adjustment_pct") or 0) < 0]
    confidence = "高" if observations >= 80 else "中" if observations >= 30 else "低"
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "as_of_date": as_of_date,
        "method": "历史推荐快照归因：按主题、买入信号、状态、资金流、有效筹码、全主板排名、接入价偏离、市场阶段、更新时间段等维度，计算后续收益相对同期推荐池均值的超额收益；样本少时做收缩，单股反馈分封顶；乱码/异常因子与筹码缺失因子不参与惩罚。",
        "horizons": list(FEEDBACK_HORIZONS),
        "snapshot_count": len(snapshots),
        "observation_count": observations,
        "confidence": confidence,
        "score_cap": FEEDBACK_SCORE_CAP,
        "min_strong_samples": FEEDBACK_MIN_STRONG_SAMPLES,
        "cleaning_policy": {
            "skip_noisy_factor_values": True,
            "chip_missing_is_neutral": True,
            "uses_market_regime_segments": True,
            "uses_update_phase_segments": True,
        },
        "segmentation": {
            "market_regime_counts": dict(sorted(market_regime_counts.items())),
            "update_phase_counts": dict(sorted(update_phase_counts.items())),
            "entry_market_regime_counts": dict(sorted(entry_market_regime_counts.items())),
            "entry_update_phase_counts": dict(sorted(entry_update_phase_counts.items())),
        },
        "summary": {
            "factor_count": len(factor_stats),
            "positive_factor_count": len(positive),
            "negative_factor_count": len(negative),
            "top_positive": positive[:5],
            "top_negative": negative[:5],
            "note": "低样本阶段只做小幅修正；反馈分不是收益承诺，而是模型复盘后的排序校正。",
        },
        "entry_effectiveness": {
            "schema_version": "1.0",
            "method": "按历史快照中所有存在推荐接入价的样本复盘，并区分当时可买、后续触达接入价、未触达等待价三类；触达样本用于评估买入后回撤和暴跌风险，未触达样本用于评估接入价是否过于保守。",
            "observation_count": entry_observations,
            "touched_observation_count": entry_touched_observations,
            "untouched_wait_observation_count": entry_untouched_observations,
            "min_strong_samples": ENTRY_FEEDBACK_MIN_SAMPLES,
            "crash_return_threshold_pct": ENTRY_CRASH_RETURN_THRESHOLD,
            "adverse_drawdown_threshold_pct": ENTRY_ADVERSE_DRAW_THRESHOLD,
            "price_cap_down_pct": ENTRY_FEEDBACK_PRICE_CAP_DOWN,
            "price_cap_up_pct": ENTRY_FEEDBACK_PRICE_CAP_UP,
            "summary": {
                "factor_count": len(entry_factor_stats),
                "positive_factor_count": len(entry_positive),
                "negative_factor_count": len(entry_negative),
                "top_positive": entry_positive[:5],
                "top_negative": entry_negative[:5],
                "note": "价格安全反馈优先惩罚触达接入价后的负收益、最大不利回撤和暴跌率；未触达等待样本只小幅校正接入价过保守问题，不直接放大可买信号。",
            },
            "factor_stats": entry_factor_stats,
        },
        "factor_stats": factor_stats,
    }


def feedback_effect_for_row(row: dict[str, Any], feedback_payload: dict[str, Any]) -> dict[str, Any]:
    stats_by_id = {
        item.get("id"): item
        for item in feedback_payload.get("factor_stats", [])
        if item.get("id")
    }
    matched: list[dict[str, Any]] = []
    total = 0.0
    for factor in stock_feedback_factors(row):
        stat = stats_by_id.get(factor["id"])
        if not stat:
            continue
        effect = safe_float(stat.get("score_effect")) or 0.0
        confidence = safe_float(stat.get("confidence")) or 0.0
        dimension_weight = FEEDBACK_DIMENSION_WEIGHTS.get(factor["dimension"], 0.5)
        contribution = effect * confidence * dimension_weight
        if abs(contribution) < 0.005:
            continue
        matched.append(
            {
                "id": factor["id"],
                "label": factor["label"],
                "dimension": factor["dimension"],
                "sample_count": stat.get("sample_count"),
                "avg_excess_return_pct": stat.get("avg_excess_return_pct"),
                "confidence": stat.get("confidence"),
                "score_effect": round_or_none(contribution, 3),
            }
        )
        total += contribution

    total = clamp(total, -FEEDBACK_SCORE_CAP, FEEDBACK_SCORE_CAP)
    matched.sort(key=lambda item: abs(item.get("score_effect") or 0), reverse=True)
    if total > 0.15:
        label = "回访正反馈"
    elif total < -0.15:
        label = "回访负反馈"
    elif matched:
        label = "回访中性"
    else:
        label = "回访样本不足"
    note = "；".join(
        f"{item['label']}({item['score_effect']:+.2f}, 样本{item.get('sample_count')})"
        for item in matched[:3]
        if item.get("score_effect") is not None
    )
    if not note:
        note = "暂无足够历史样本，反馈分不参与或仅极小幅参与。"
    return {
        "feedback_bonus": round_or_none(total, 3),
        "feedback_label": label,
        "feedback_confidence": feedback_payload.get("confidence", "低"),
        "feedback_note": note,
        "feedback_factors": matched[:5],
    }

def format_optional_pct(value: Any) -> str:
    number = safe_float(value)
    if number is None:
        return "-"
    sign = "+" if number > 0 else ""
    return f"{sign}{number:.2f}%"


def entry_safety_effect_for_row(row: dict[str, Any], feedback_payload: dict[str, Any]) -> dict[str, Any]:
    entry_payload = feedback_payload.get("entry_effectiveness") or {}
    stats_by_id = {
        item.get("id"): item
        for item in entry_payload.get("factor_stats", [])
        if item.get("id")
    }
    matched: list[dict[str, Any]] = []
    total = 0.0
    high_risk_weight = 0.0
    max_crash_rate = 0.0

    for factor in entry_effectiveness_factors(row):
        stat = stats_by_id.get(factor["id"])
        if not stat:
            continue
        adjustment = safe_float(stat.get("price_adjustment_pct")) or 0.0
        confidence = safe_float(stat.get("confidence")) or 0.0
        dimension_weight = ENTRY_EFFECTIVENESS_DIMENSION_WEIGHTS.get(factor["dimension"], 0.5)
        contribution = adjustment * confidence * dimension_weight
        if abs(contribution) < 0.01:
            continue
        risk_level = stat.get("risk_level") or "未知"
        crash_rate = safe_float(stat.get("crash_rate_pct")) or 0.0
        max_crash_rate = max(max_crash_rate, crash_rate)
        if risk_level == "高":
            high_risk_weight += abs(contribution)
        matched.append(
            {
                "id": factor["id"],
                "label": factor["label"],
                "dimension": factor["dimension"],
                "sample_count": stat.get("sample_count"),
                "avg_entry_return_pct": stat.get("avg_entry_return_pct"),
                "avg_touch_return_pct": stat.get("avg_touch_return_pct"),
                "avg_missed_return_pct": stat.get("avg_missed_return_pct"),
                "avg_adverse_drawdown_pct": stat.get("avg_adverse_drawdown_pct"),
                "crash_rate_pct": stat.get("crash_rate_pct"),
                "touch_rate_pct": stat.get("touch_rate_pct"),
                "untouched_wait_rate_pct": stat.get("untouched_wait_rate_pct"),
                "actual_buyable_count": stat.get("actual_buyable_count"),
                "touched_entry_count": stat.get("touched_entry_count"),
                "untouched_wait_count": stat.get("untouched_wait_count"),
                "confidence": stat.get("confidence"),
                "risk_level": risk_level,
                "price_adjustment_pct": round_or_none(contribution, 3),
            }
        )
        total += contribution

    total = clamp(total, ENTRY_FEEDBACK_PRICE_CAP_DOWN, ENTRY_FEEDBACK_PRICE_CAP_UP)
    matched.sort(key=lambda item: abs(item.get("price_adjustment_pct") or 0), reverse=True)

    high_risk = total <= -1.0 or high_risk_weight >= 0.7 or (row.get("is_buyable_now") and total <= -0.25 and max_crash_rate >= 20)
    block_buy = bool(row.get("is_buyable_now") and high_risk)

    if high_risk:
        label = "接入风险高"
    elif total <= -0.35:
        label = "接入偏谨慎"
    elif total >= 0.25:
        label = "接入验证较好"
    elif matched:
        label = "接入中性"
    else:
        label = "接入样本不足"

    note = "；".join(
        (
            f"{item['label']}({item['price_adjustment_pct']:+.2f}%, "
            f"触达后{format_optional_pct(item.get('avg_touch_return_pct'))}, "
            f"未触达错过{format_optional_pct(item.get('avg_missed_return_pct'))}, "
            f"触达率{format_optional_pct(item.get('touch_rate_pct'))}, "
            f"回撤{format_optional_pct(item.get('avg_adverse_drawdown_pct'))}, "
            f"暴跌率{format_optional_pct(item.get('crash_rate_pct'))}, "
            f"样本{item.get('sample_count')})"
        )
        for item in matched[:3]
        if item.get("price_adjustment_pct") is not None
    )
    if not note:
        note = "历史接入价样本不足，本轮不额外放宽可买价。"

    return {
        "entry_safety_adjustment_pct": round_or_none(total, 3),
        "entry_safety_label": label,
        "entry_safety_note": note,
        "entry_safety_factors": matched[:5],
        "entry_safety_risk_flag": high_risk,
        "entry_safety_block_buy": block_buy,
        "entry_safety_observation_count": entry_payload.get("observation_count", 0),
    }


def adjusted_price(value: Any, adjustment_pct: float, floor: Any = None, ceiling: Any = None) -> float | None:
    base = safe_float(value)
    if base is None:
        return None
    updated = base * (1 + adjustment_pct / 100)
    floor_value = safe_float(floor)
    ceiling_value = safe_float(ceiling)
    if floor_value is not None:
        updated = max(updated, floor_value)
    if ceiling_value is not None:
        updated = min(updated, ceiling_value)
    return updated


def apply_feedback_price_adjustment(
    row: dict[str, Any],
    feedback_meta: dict[str, Any],
    entry_safety_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    feedback_bonus = safe_float(feedback_meta.get("feedback_bonus")) or 0.0
    # Feedback score is already sample-shrunk. Convert it to a small price-discipline adjustment.
    factor_adjustment_pct = clamp(feedback_bonus * 1.2, -1.2, 1.0)
    entry_safety_meta = entry_safety_meta or {}
    safety_adjustment_pct = safe_float(entry_safety_meta.get("entry_safety_adjustment_pct")) or 0.0
    adjustment_pct = clamp(factor_adjustment_pct + safety_adjustment_pct, -3.0, 1.0)
    if abs(factor_adjustment_pct) < 0.01:
        factor_adjustment_pct = 0.0
    if abs(safety_adjustment_pct) < 0.01:
        safety_adjustment_pct = 0.0
    if abs(adjustment_pct) < 0.01:
        adjustment_pct = 0.0

    auditable_keys = [
        "recommended_entry_price",
        "entry_price_lower",
        "entry_price_upper",
        "buyable_price",
        "buyable_price_lower",
        "buyable_price_upper",
        "next_buy_trigger_price",
        "breakout_buy_upper_price",
        "entry_gap_pct",
        "watch_zone",
    ]
    for key in auditable_keys:
        row[f"base_{key}"] = row.get(key)

    row["factor_price_feedback_adjustment_pct"] = round_or_none(factor_adjustment_pct, 3)
    row["entry_safety_adjustment_pct"] = round_or_none(safety_adjustment_pct, 3)

    if adjustment_pct == 0.0 and not entry_safety_meta.get("entry_safety_block_buy"):
        label = "价格纪律不变"
        note = "回访样本或反馈强度不足，推荐接入价和可买价保持原模型结果。"
        row.update(
            {
                "price_feedback_adjustment_pct": 0.0,
                "price_feedback_label": label,
                "price_feedback_note": note,
            }
        )
        return row

    close = safe_float(row.get("close"))
    invalid = safe_float(row.get("invalid_price"))
    no_chase = safe_float(row.get("no_chase_price"))
    breakout_confirm = safe_float(row.get("breakout_confirm_price"))
    price_ceiling = no_chase * 0.985 if no_chase else None

    entry_lower = adjusted_price(row.get("entry_price_lower"), adjustment_pct, invalid, price_ceiling)
    entry_upper = adjusted_price(row.get("entry_price_upper"), adjustment_pct, invalid, price_ceiling)
    if entry_lower is not None and entry_upper is not None and entry_lower > entry_upper:
        entry_lower = entry_upper
    recommended_entry = adjusted_price(row.get("recommended_entry_price"), adjustment_pct, entry_lower, entry_upper)

    if entry_lower is not None:
        row["entry_price_lower"] = round_or_none(entry_lower)
    if entry_upper is not None:
        row["entry_price_upper"] = round_or_none(entry_upper)
    if recommended_entry is not None:
        row["recommended_entry_price"] = round_or_none(recommended_entry)
        row["entry_gap_pct"] = round_or_none((close / recommended_entry - 1) * 100 if close else None)
    if entry_lower is not None and entry_upper is not None:
        row["watch_zone"] = f"{entry_lower:.2f}-{entry_upper:.2f}"

    path = str(row.get("buy_price_path") or "")
    if "突破" in path:
        upper_floor = breakout_confirm
        upper_ceiling = no_chase
    else:
        upper_floor = invalid
        upper_ceiling = entry_upper

    for key in ("buyable_price_lower", "buyable_price_upper", "buyable_price", "breakout_buy_upper_price"):
        updated = adjusted_price(row.get(key), adjustment_pct, upper_floor if key != "buyable_price_upper" else None, upper_ceiling)
        if updated is not None:
            row[key] = round_or_none(updated)

    next_price = row.get("next_buy_trigger_price")
    if "突破" in path:
        updated_next = adjusted_price(next_price, adjustment_pct, breakout_confirm, no_chase)
    else:
        updated_next = adjusted_price(next_price, adjustment_pct, invalid, entry_upper)
    if updated_next is not None:
        row["next_buy_trigger_price"] = round_or_none(updated_next)

    if adjustment_pct > 0:
        label = "价格纪律略放宽"
        direction = "上移"
    else:
        label = "价格纪律收紧"
        direction = "下压"

    row["price_feedback_adjustment_pct"] = round_or_none(adjustment_pct, 3)
    row["price_feedback_label"] = label
    row["price_feedback_note"] = (
        f"根据回访归因反馈，推荐接入价和可买价相对原模型{direction}{abs(adjustment_pct):.2f}%。"
        "调整幅度已做样本收缩和上限控制，不改变不追高线。"
    )
    safety_note = entry_safety_meta.get("entry_safety_note")
    if safety_note:
        row["price_feedback_note"] = (
            f"{row['price_feedback_note']} 接入有效性反馈：{entry_safety_meta.get('entry_safety_label', '-')}"
            f"；安全调整{format_optional_pct(safety_adjustment_pct)}；{safety_note}"
        )

    if entry_safety_meta.get("entry_safety_block_buy") and row.get("is_buyable_now"):
        row["risk_adjusted_buyable_price"] = row.get("buyable_price")
        row["is_buyable_now"] = False
        row["base_buy_signal_key"] = row.get("buy_signal_key")
        row["base_buy_signal_label"] = row.get("buy_signal_label")
        row["buy_signal_key"] = "risk_wait"
        row["buy_signal_label"] = "回访风控等待"
        row["buy_price_path"] = "等待更低价格+量价确认"
        risk_trigger = safe_float(row.get("entry_price_upper")) or safe_float(row.get("recommended_entry_price")) or safe_float(row.get("next_buy_trigger_price"))
        row["next_buy_trigger_price"] = round_or_none(risk_trigger)
        row["buyable_price"] = None
        row["buy_price_note"] = (
            f"{row.get('buy_price_note') or ''} 历史接入有效性风险偏高，当前取消可买入标记；"
            "需等待价格低于校准触发价且量价重新企稳后再复核。"
        ).strip()

    row["entry_price_note"] = f"{row.get('entry_price_note') or ''} {row['price_feedback_note']}".strip()
    row["buy_price_note"] = f"{row.get('buy_price_note') or ''} {row['price_feedback_note']}".strip()
    return row


def apply_market_theme_context(row: dict[str, Any], market_environment: dict[str, Any]) -> dict[str, Any]:
    market_score = safe_float(market_environment.get("temperature_score")) or 0.0
    market_bonus = safe_float(market_environment.get("score_bonus")) or 0.0
    market_price_adjustment = safe_float(market_environment.get("price_adjustment_pct")) or 0.0
    market_regime = str(market_environment.get("regime") or "unknown")
    market_label = str(market_environment.get("label") or "市场温度未知")
    theme_bonus = safe_float(row.get("theme_strength_bonus")) or 0.0
    theme_score = safe_float(row.get("theme_strength_score"))
    theme_label = str(row.get("theme_strength_label") or "主题强度未知")
    context_bonus = clamp(market_bonus * 0.65 + theme_bonus * 0.75, -1.2, 1.1)
    context_price_adjustment = clamp(market_price_adjustment * 0.55 + theme_bonus * 0.22, -1.1, 0.55)
    if abs(context_bonus) < 0.01:
        context_bonus = 0.0
    if abs(context_price_adjustment) < 0.01:
        context_price_adjustment = 0.0

    row["market_temperature_score"] = market_environment.get("temperature_score")
    row["market_temperature_label"] = market_label
    row["market_regime"] = market_regime
    row["market_risk_appetite"] = market_environment.get("risk_appetite")
    row["market_context_score_bonus"] = round_or_none(context_bonus, 3)
    row["market_context_price_adjustment_pct"] = round_or_none(context_price_adjustment, 3)
    row["market_context_note"] = f"市场温度：{market_label}；主题强度：{theme_label}。{market_environment.get('note') or ''}"

    if context_price_adjustment != 0:
        for key in (
            "recommended_entry_price",
            "entry_price_lower",
            "entry_price_upper",
            "buyable_price",
            "buyable_price_lower",
            "buyable_price_upper",
            "next_buy_trigger_price",
            "breakout_buy_upper_price",
        ):
            row.setdefault(f"base_market_context_{key}", row.get(key))

        invalid = row.get("invalid_price")
        no_chase = row.get("no_chase_price")
        entry_upper = adjusted_price(row.get("entry_price_upper"), context_price_adjustment, invalid, no_chase)
        entry_lower = adjusted_price(row.get("entry_price_lower"), context_price_adjustment, invalid, entry_upper)
        recommended_entry = adjusted_price(row.get("recommended_entry_price"), context_price_adjustment, entry_lower, entry_upper)
        if entry_lower is not None:
            row["entry_price_lower"] = round_or_none(entry_lower)
        if entry_upper is not None:
            row["entry_price_upper"] = round_or_none(entry_upper)
        if recommended_entry is not None:
            row["recommended_entry_price"] = round_or_none(recommended_entry)
            close = safe_float(row.get("close"))
            row["entry_gap_pct"] = round_or_none((close / recommended_entry - 1) * 100 if close else None)
        if entry_lower is not None and entry_upper is not None:
            row["watch_zone"] = f"{entry_lower:.2f}-{entry_upper:.2f}"

        for key in ("buyable_price", "buyable_price_lower", "buyable_price_upper", "breakout_buy_upper_price", "next_buy_trigger_price"):
            updated = adjusted_price(row.get(key), context_price_adjustment, invalid, no_chase)
            if updated is not None:
                row[key] = round_or_none(updated)

    market_block = False
    if row.get("is_buyable_now") and market_regime in {"defensive", "cautious"}:
        theme_is_strong = theme_score is not None and theme_score >= 58
        if market_regime == "defensive" or row.get("buy_signal_key") == "breakout_buy" or not theme_is_strong:
            market_block = True

    if market_block:
        row["market_context_block_buy"] = True
        row["risk_adjusted_buyable_price"] = row.get("buyable_price")
        row["is_buyable_now"] = False
        row.setdefault("base_buy_signal_key", row.get("buy_signal_key"))
        row.setdefault("base_buy_signal_label", row.get("buy_signal_label"))
        row["buy_signal_key"] = "market_wait"
        row["buy_signal_label"] = "市场温度等待"
        row["buy_price_path"] = "等待市场温度修复+个股重新确认"
        row["next_buy_trigger_price"] = row.get("recommended_entry_price") or row.get("next_buy_trigger_price")
        row["buyable_price"] = None
        row["buy_price_note"] = (
            f"{row.get('buy_price_note') or ''} 市场环境层认为当前不宜放大试错，"
            "先取消可买观察信号，等待市场温度或主题扩散修复。"
        ).strip()
    else:
        row["market_context_block_buy"] = False

    if row.get("is_buyable_now") and not row.get("entry_safety_risk_flag") and market_regime in {"strong", "warm", "neutral"}:
        grade = "A"
        grade_label = "A级：可小仓试错"
    elif row.get("is_buyable_now"):
        grade = "B"
        grade_label = "B级：低仓验证"
    elif row.get("market_context_block_buy") or row.get("entry_safety_risk_flag"):
        grade = "C"
        grade_label = "C级：有逻辑但价格/环境不安全"
    elif row.get("status_key") in {"watch", "breakout"} or (theme_score is not None and theme_score >= 58):
        grade = "B"
        grade_label = "B级：观察等待触发"
    elif row.get("status_key") == "avoid":
        grade = "D"
        grade_label = "D级：暂不追高"
    else:
        grade = "C"
        grade_label = "C级：等待价格接近"

    row["decision_grade"] = grade
    row["decision_grade_label"] = grade_label
    row["score"] = round((safe_float(row.get("score")) or 0.0) + context_bonus, 1)
    row["position_hint"] = f"{grade_label}。{row.get('position_hint') or ''} {row['market_context_note']}"
    return row


def apply_portfolio_concentration_control(rows: list[dict[str, Any]], preferred_max_per_theme: int = 2) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: safe_float(row.get("score")) or 0.0, reverse=True)
    group_counts: dict[str, int] = {}
    penalties: list[dict[str, Any]] = []
    for row in ordered:
        group = row.get("theme_group") or theme_group(row.get("theme"))
        before = group_counts.get(group, 0)
        group_counts[group] = before + 1
        overflow = max(0, before + 1 - preferred_max_per_theme)
        penalty = clamp(overflow * 0.35, 0.0, 1.05)
        row["portfolio_theme_group"] = group
        row["portfolio_theme_count_before"] = before
        row["portfolio_concentration_penalty"] = round_or_none(penalty, 3)
        if penalty:
            row["score"] = round((safe_float(row.get("score")) or 0.0) - penalty, 1)
            row["portfolio_concentration_note"] = f"{group}在候选池中已较集中，本股排序轻微降权{penalty:.2f}分，降低同主题联动回撤。"
            penalties.append(
                {
                    "code": row.get("code"),
                    "name": row.get("name"),
                    "theme_group": group,
                    "penalty": round_or_none(penalty, 3),
                    "theme_count_before": before,
                }
            )
        else:
            row["portfolio_concentration_note"] = "主题集中度未触发降权。"

    return {
        "schema_version": "1.0",
        "preferred_max_per_theme": preferred_max_per_theme,
        "candidate_theme_counts": dict(sorted(group_counts.items())),
        "penalized_count": len(penalties),
        "penalized": penalties[:12],
        "note": "组合层约束只做排序降权，不直接否定个股逻辑；目标是减少同一主题拥挤导致的回撤联动。",
    }


def summarize_theme_exposure(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        group = row.get("portfolio_theme_group") or row.get("theme_group") or theme_group(row.get("theme"))
        counts[group] = counts.get(group, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def merge_review_record(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = {**existing, **incoming}
    first_keys = (
        "first_recommend_date",
        "first_recommend_price",
        "first_rank",
        "first_status_key",
        "first_source",
        "first_recommended_entry_price",
        "first_entry_price_lower",
        "first_entry_price_upper",
        "first_buyable_price",
        "first_buy_signal_key",
        "first_buy_signal_label",
        "first_is_buyable_now",
        "first_entry_gap_pct",
        "first_entry_safety_adjustment_pct",
        "first_entry_safety_label",
        "first_entry_safety_risk_flag",
        "first_entry_safety_block_buy",
        "first_entry_reference_type",
        "entry_return_from_first_entry_pct",
        "entry_drawdown_from_first_entry_pct",
    )

    existing_first = parse_date_value(existing.get("first_recommend_date"))
    incoming_first = parse_date_value(incoming.get("first_recommend_date"))
    if incoming_first and (existing_first is None or incoming_first < existing_first):
        for key in first_keys:
            merged[key] = incoming.get(key)
    else:
        for key in first_keys:
            if existing.get(key) is not None:
                merged[key] = existing.get(key)

    existing_latest = parse_date_value(existing.get("latest_date"))
    incoming_latest = parse_date_value(incoming.get("latest_date"))
    if existing_latest and (incoming_latest is None or existing_latest >= incoming_latest):
        for key in ("latest_date", "latest_price", "current_rank", "current_status_key", "last_seen_rank", "last_seen_in_pool_date", "latest_source"):
            if existing.get(key) is not None:
                merged[key] = existing.get(key)

    return merged


def load_existing_review_records() -> list[dict[str, Any]]:
    records_by_code: dict[str, dict[str, Any]] = {}

    def absorb(raw: bytes | str) -> None:
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        for record in data.get("records", []):
            code = record.get("code")
            if code:
                if code in records_by_code:
                    records_by_code[code] = merge_review_record(records_by_code[code], record)
                else:
                    records_by_code[code] = record

    if REVIEW_PATH.exists():
        try:
            absorb(REVIEW_PATH.read_text(encoding="utf-8"))
        except OSError:
            pass

    try:
        absorb(subprocess.check_output(["git", "show", "HEAD:data/review.json"], cwd=ROOT, stderr=subprocess.DEVNULL))
    except Exception:
        pass

    return list(records_by_code.values())


def classify_review_attribution(
    *,
    active: bool,
    theme: Any,
    first_close: Any,
    first_entry_reference: Any,
    first_entry_gap_pct: Any,
    first_buy_signal_key: Any,
    first_entry_safety_risk_flag: Any,
    return_pct: Any,
    max_return_pct: Any,
    drawdown_from_peak_pct: Any,
    entry_return_pct: Any,
    entry_drawdown_pct: Any,
    latest_source: Any,
) -> dict[str, Any]:
    ret = safe_float(return_pct)
    max_ret = safe_float(max_return_pct)
    drawdown = safe_float(drawdown_from_peak_pct)
    entry_ret = safe_float(entry_return_pct)
    entry_drawdown = safe_float(entry_drawdown_pct)
    first_price = safe_float(first_close)
    entry_ref = safe_float(first_entry_reference)
    first_gap = safe_float(first_entry_gap_pct)
    signal = str(first_buy_signal_key or "")
    group = theme_group(theme)
    factors: list[str] = []

    if ret is None:
        primary = "pending_data"
        factors.append("latest_price_missing")
        model_action = "等待最新行情补齐后再纳入反馈。"
    elif ret >= 5:
        primary = "positive_validation"
        if entry_ret is not None and entry_ret > ret + 3:
            factors.append("entry_price_better_than_first_recommend")
            model_action = "方向验证为正，同时保留接入价纪律，避免首推价过高。"
        else:
            factors.append("direction_validated")
            model_action = "正反馈保留，但不提高到确定性结论。"
    elif ret <= -5:
        primary = "negative_review"
        if entry_ref is None:
            factors.append("missing_entry_reference")
        if first_price is not None and entry_ref:
            first_gap_from_entry = (first_price / entry_ref - 1) * 100
            if first_gap_from_entry >= 10:
                factors.append("first_recommend_price_far_above_entry")
        if first_gap is not None and first_gap >= 10:
            factors.append("entry_gap_too_high")
        if signal == "breakout_buy":
            factors.append("breakout_signal_failed")
        if entry_drawdown is not None and entry_drawdown <= -7:
            factors.append("entry_price_adverse_drawdown")
        if entry_ret is not None and entry_ret <= -5:
            factors.append("entry_price_failed")
        elif entry_ret is not None and entry_ret > 0:
            factors.append("entry_price_better_than_first_recommend")
        if drawdown is not None and drawdown <= -10:
            factors.append("post_recommend_drawdown_too_deep")
        if max_ret is not None and max_ret >= 5 and drawdown is not None and drawdown <= -8:
            factors.append("profit_not_protected")
        if bool(first_entry_safety_risk_flag):
            factors.append("entry_safety_warning_was_present")
        if group in {"资源周期", "高端制造"}:
            factors.append("theme_volatility_or_rotation")
        if active:
            factors.append("still_active_negative_feedback")
        else:
            factors.append("exited_negative_feedback")

        if "entry_price_failed" in factors or "entry_price_adverse_drawdown" in factors:
            model_action = "收紧接入价并提高触达后回撤惩罚。"
        elif "first_recommend_price_far_above_entry" in factors or "entry_gap_too_high" in factors:
            model_action = "降低首推价偏离过高样本权重，强化等待纪律。"
        elif "profit_not_protected" in factors:
            model_action = "增加冲高回落后的止盈/调出反馈权重。"
        elif "breakout_signal_failed" in factors:
            model_action = "突破试探需叠加市场和主题扩散确认。"
        else:
            model_action = "降低同类主题/信号权重并继续积累样本。"
    else:
        primary = "neutral_tracking"
        factors.append("inside_validation_band")
        model_action = "继续观察，不作强反馈。"

    if latest_source == "history_snapshot":
        factors.append("latest_quote_from_history_snapshot")

    return {
        "primary": primary,
        "theme_group": group,
        "factors": sorted(set(factors)),
        "model_action": model_action,
    }


def build_review_center(
    rows: list[dict[str, Any]],
    as_of_date: str,
    review_quotes_by_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    current_by_code = {row["code"]: row for row in rows}
    review_quotes_by_code = review_quotes_by_code or {}
    entries_by_code: dict[str, dict[str, dict[str, Any]]] = {}

    for record in load_existing_review_records():
        code = record.get("code")
        if not code:
            continue
        entries_by_code.setdefault(code, {})
        first_price = safe_float(record.get("first_recommend_price"))
        if record.get("first_recommend_date") and first_price is not None:
            entries_by_code[code][tracking_entry_key(record["first_recommend_date"], "review_first")] = {
                "date": str(record["first_recommend_date"]),
                "close": first_price,
                "rank": record.get("first_rank"),
                "status_key": record.get("first_status_key"),
                "name": record.get("name"),
                "theme": record.get("theme"),
                "board": record.get("board"),
                "source": "review_first",
                "recommended_entry_price": record.get("first_recommended_entry_price"),
                "entry_price_lower": record.get("first_entry_price_lower"),
                "entry_price_upper": record.get("first_entry_price_upper"),
                "buyable_price": record.get("first_buyable_price"),
                "buy_signal_key": record.get("first_buy_signal_key"),
                "buy_signal_label": record.get("first_buy_signal_label"),
                "is_buyable_now": record.get("first_is_buyable_now"),
                "entry_gap_pct": record.get("first_entry_gap_pct"),
                "entry_safety_adjustment_pct": record.get("first_entry_safety_adjustment_pct"),
                "entry_safety_label": record.get("first_entry_safety_label"),
                "entry_safety_risk_flag": record.get("first_entry_safety_risk_flag"),
                "entry_safety_block_buy": record.get("first_entry_safety_block_buy"),
            }
        latest_price = safe_float(record.get("latest_price"))
        if record.get("latest_date") and latest_price is not None:
            entries_by_code[code][tracking_entry_key(record["latest_date"], "review_latest")] = {
                "date": str(record["latest_date"]),
                "close": latest_price,
                "rank": record.get("last_seen_rank"),
                "status_key": record.get("current_status_key"),
                "name": record.get("name"),
                "theme": record.get("theme"),
                "board": record.get("board"),
                "source": "review_latest",
            }

    for snapshot in load_history_snapshots():
        snapshot_date = snapshot.get("as_of_date")
        if not snapshot_date:
            continue
        snapshot_id = snapshot.get("snapshot_id") or snapshot.get("generated_at")
        for stock in snapshot.get("stocks", []):
            code = stock.get("code")
            close = safe_float(stock.get("close"))
            if not code or close is None:
                continue
            entries_by_code.setdefault(code, {})[tracking_entry_key(snapshot_date, "history", snapshot_id)] = {
                "date": str(snapshot_date),
                "generated_at": snapshot.get("generated_at"),
                "close": close,
                "rank": stock.get("rank"),
                "status_key": stock.get("status_key"),
                "name": stock.get("name"),
                "theme": stock.get("theme"),
                "board": stock.get("board"),
                "source": "history_snapshot",
                "recommended_entry_price": stock.get("recommended_entry_price"),
                "entry_price_lower": stock.get("entry_price_lower"),
                "entry_price_upper": stock.get("entry_price_upper"),
                "buyable_price": stock.get("buyable_price"),
                "buy_signal_key": stock.get("buy_signal_key"),
                "buy_signal_label": stock.get("buy_signal_label"),
                "is_buyable_now": stock.get("is_buyable_now"),
                "entry_gap_pct": stock.get("entry_gap_pct"),
                "entry_safety_adjustment_pct": stock.get("entry_safety_adjustment_pct"),
                "entry_safety_label": stock.get("entry_safety_label"),
                "entry_safety_risk_flag": stock.get("entry_safety_risk_flag"),
                "entry_safety_block_buy": stock.get("entry_safety_block_buy"),
            }

    for row in rows:
        close = safe_float(row.get("close"))
        if close is None:
            continue
        entries_by_code.setdefault(row["code"], {})[tracking_entry_key(as_of_date, "current")] = {
            "date": as_of_date,
            "close": close,
            "rank": row.get("rank"),
            "status_key": row.get("status_key"),
            "name": row.get("name"),
            "theme": row.get("theme"),
            "board": row.get("board"),
            "source": "current_pool",
            "recommended_entry_price": row.get("recommended_entry_price"),
            "entry_price_lower": row.get("entry_price_lower"),
            "entry_price_upper": row.get("entry_price_upper"),
            "buyable_price": row.get("buyable_price"),
            "buy_signal_key": row.get("buy_signal_key"),
            "buy_signal_label": row.get("buy_signal_label"),
            "is_buyable_now": row.get("is_buyable_now"),
            "entry_gap_pct": row.get("entry_gap_pct"),
            "entry_safety_adjustment_pct": row.get("entry_safety_adjustment_pct"),
            "entry_safety_label": row.get("entry_safety_label"),
            "entry_safety_risk_flag": row.get("entry_safety_risk_flag"),
            "entry_safety_block_buy": row.get("entry_safety_block_buy"),
        }

    records: list[dict[str, Any]] = []
    current_date = parse_date_value(as_of_date)

    for code, entries in sorted(entries_by_code.items()):
        current_row = current_by_code.get(code)
        quote_error = None
        latest_quote: dict[str, Any] | None = None

        if current_row:
            latest_quote = {
                "date": as_of_date,
                "close": safe_float(current_row.get("close")),
                "rank": current_row.get("rank"),
                "status_key": current_row.get("status_key"),
                "name": current_row.get("name"),
                "theme": current_row.get("theme"),
                "board": current_row.get("board"),
                "source": "current_pool",
            }
        else:
            live_quote = review_quotes_by_code.get(code, {})
            live_close = safe_float(live_quote.get("live_close"))
            live_quote_date = live_quote.get("live_quote_date")
            if live_close is not None and live_close > 0 and live_quote_date:
                latest_quote = {
                    "date": str(live_quote_date),
                    "close": live_close,
                    "rank": None,
                    "status_key": "exited",
                    "name": next((item.get("name") for item in entries.values() if item.get("name")), code),
                    "theme": next((item.get("theme") for item in entries.values() if item.get("theme")), ""),
                    "board": board_for(code),
                    "source": "latest_quote",
                }
            else:
                try:
                    df = load_daily(code)
                    latest_quote = {
                        "date": df["date"].iloc[-1].strftime("%Y-%m-%d"),
                        "close": float(df["close"].astype(float).iloc[-1]),
                        "rank": None,
                        "status_key": "exited",
                        "name": next((item.get("name") for item in entries.values() if item.get("name")), code),
                        "theme": next((item.get("theme") for item in entries.values() if item.get("theme")), ""),
                        "board": board_for(code),
                        "source": "latest_quote",
                    }
                except Exception as exc:  # keep historical review available even if a quote source fails
                    quote_error = str(exc)

        if latest_quote and latest_quote.get("close") is not None:
            entries[tracking_entry_key(latest_quote["date"], "latest_quote")] = latest_quote

        series = sorted(
            entries.values(),
            key=tracking_sort_key,
        )
        if not series:
            continue

        first = series[0]
        latest = series[-1]
        last_seen = max(
            [item for item in series if item.get("rank") is not None],
            key=tracking_sort_key,
            default=latest,
        )

        first_close = safe_float(first.get("close"))
        latest_close = safe_float(latest.get("close"))
        max_entry = max(series, key=lambda item: safe_float(item.get("close")) or float("-inf"))
        min_entry = min(series, key=lambda item: safe_float(item.get("close")) or float("inf"))
        max_close = safe_float(max_entry.get("close"))
        min_close = safe_float(min_entry.get("close"))

        return_pct = None
        max_return_pct = None
        drawdown_from_peak_pct = None
        if first_close and latest_close is not None:
            return_pct = (latest_close / first_close - 1) * 100
        if first_close and max_close is not None:
            max_return_pct = (max_close / first_close - 1) * 100
        if latest_close is not None and max_close:
            drawdown_from_peak_pct = (latest_close / max_close - 1) * 100

        first_date = parse_date_value(first.get("date"))
        tracking_days = None
        if first_date and current_date:
            tracking_days = (current_date - first_date).days

        active = code in current_by_code
        if return_pct is None:
            review_status = "待回访"
            comment = "缺少最新价格，暂不评价推荐后表现。"
        elif active and return_pct >= 5:
            review_status = "当前池中-正反馈"
            comment = "仍在最新推荐池中，且推荐后已有正收益。"
        elif active and return_pct <= -5:
            review_status = "当前池中-负反馈"
            comment = "仍在最新推荐池中，但推荐后回撤较明显，需要复核逻辑和风控。"
        elif active:
            review_status = "当前池中-继续观察"
            comment = "仍在最新推荐池中，表现暂处于验证区间。"
        elif return_pct >= 0:
            review_status = "已调出-正收益"
            comment = "不在最新推荐池中，但历史推荐目前仍为正收益，继续保留回访记录。"
        else:
            review_status = "已调出-负收益"
            comment = "不在最新推荐池中，历史推荐目前为负收益，后续用于模型复盘。"

        if quote_error:
            comment = f"{comment} 最新行情刷新失败，暂用历史快照：{quote_error}"

        first_entry_source = first
        if safe_float(first_entry_source.get("recommended_entry_price")) is None and safe_float(first_entry_source.get("buyable_price")) is None:
            first_entry_source = next(
                (
                    item
                    for item in series
                    if item.get("date") == first.get("date")
                    and (safe_float(item.get("recommended_entry_price")) is not None or safe_float(item.get("buyable_price")) is not None)
                ),
                first_entry_source,
            )
        if safe_float(first_entry_source.get("recommended_entry_price")) is None and safe_float(first_entry_source.get("buyable_price")) is None:
            first_entry_source = next(
                (
                    item
                    for item in series
                    if safe_float(item.get("recommended_entry_price")) is not None or safe_float(item.get("buyable_price")) is not None
                ),
                first_entry_source,
            )

        first_recommended_entry = safe_float(first_entry_source.get("recommended_entry_price"))
        first_buyable_price = safe_float(first_entry_source.get("buyable_price"))
        first_entry_reference = first_buyable_price or first_recommended_entry
        first_entry_reference_type = "buyable_price" if first_buyable_price else "recommended_entry_price" if first_recommended_entry else None
        entry_return_pct = None
        entry_drawdown_pct = None
        if first_entry_reference and latest_close is not None:
            entry_return_pct = (latest_close / first_entry_reference - 1) * 100
        if first_entry_reference and min_close is not None:
            entry_drawdown_pct = (min_close / first_entry_reference - 1) * 100

        identity = current_row or latest or first
        identity_name = clean_display_text(identity.get("name"), code)
        identity_theme = clean_display_text(identity.get("theme") or first.get("theme"), "")
        first_buy_signal_label = clean_display_text(first_entry_source.get("buy_signal_label"), None)
        review_attribution = classify_review_attribution(
            active=active,
            theme=identity_theme,
            first_close=first_close,
            first_entry_reference=first_entry_reference,
            first_entry_gap_pct=first_entry_source.get("entry_gap_pct"),
            first_buy_signal_key=first_entry_source.get("buy_signal_key"),
            first_entry_safety_risk_flag=first_entry_source.get("entry_safety_risk_flag"),
            return_pct=return_pct,
            max_return_pct=max_return_pct,
            drawdown_from_peak_pct=drawdown_from_peak_pct,
            entry_return_pct=entry_return_pct,
            entry_drawdown_pct=entry_drawdown_pct,
            latest_source=latest.get("source"),
        )
        records.append(
            {
                "code": code,
                "name": identity_name,
                "theme": identity_theme,
                "board": identity.get("board") or board_for(code),
                "active_in_current_pool": active,
                "current_rank": current_row.get("rank") if current_row else None,
                "current_status_key": current_row.get("status_key") if current_row else None,
                "first_recommend_date": first.get("date"),
                "first_recommend_price": round_or_none(first_close),
                "first_rank": first.get("rank"),
                "first_status_key": first.get("status_key"),
                "first_source": first.get("source"),
                "first_recommended_entry_price": round_or_none(first_recommended_entry),
                "first_entry_price_lower": round_or_none(first_entry_source.get("entry_price_lower")),
                "first_entry_price_upper": round_or_none(first_entry_source.get("entry_price_upper")),
                "first_buyable_price": round_or_none(first_buyable_price),
                "first_buy_signal_key": first_entry_source.get("buy_signal_key"),
                "first_buy_signal_label": first_buy_signal_label,
                "first_is_buyable_now": first_entry_source.get("is_buyable_now"),
                "first_entry_gap_pct": round_or_none(first_entry_source.get("entry_gap_pct")),
                "first_entry_safety_adjustment_pct": round_or_none(first_entry_source.get("entry_safety_adjustment_pct"), 3),
                "first_entry_safety_label": first_entry_source.get("entry_safety_label"),
                "first_entry_safety_risk_flag": first_entry_source.get("entry_safety_risk_flag"),
                "first_entry_safety_block_buy": first_entry_source.get("entry_safety_block_buy"),
                "first_entry_reference_type": first_entry_reference_type,
                "entry_return_from_first_entry_pct": round_or_none(entry_return_pct),
                "entry_drawdown_from_first_entry_pct": round_or_none(entry_drawdown_pct),
                "last_seen_in_pool_date": last_seen.get("date"),
                "last_seen_rank": last_seen.get("rank"),
                "latest_date": latest.get("date"),
                "latest_price": round_or_none(latest_close),
                "latest_source": latest.get("source"),
                "tracking_days": tracking_days,
                "snapshot_count": len(series),
                "return_since_first_pct": round_or_none(return_pct),
                "max_return_since_first_pct": round_or_none(max_return_pct),
                "drawdown_from_peak_pct": round_or_none(drawdown_from_peak_pct),
                "best_close": round_or_none(max_close),
                "best_date": max_entry.get("date"),
                "worst_close": round_or_none(min_close),
                "worst_date": min_entry.get("date"),
                "review_status": review_status,
                "review_attribution": review_attribution,
                "review_primary_attribution": review_attribution.get("primary"),
                "review_model_action": review_attribution.get("model_action"),
                "comment": comment,
            }
        )

    tracked = [record for record in records if record.get("return_since_first_pct") is not None]
    attribution_counts: dict[str, int] = {}
    model_action_counts: dict[str, int] = {}
    for record in records:
        primary = record.get("review_primary_attribution") or "unknown"
        attribution_counts[primary] = attribution_counts.get(primary, 0) + 1
        action = record.get("review_model_action") or "unknown"
        model_action_counts[action] = model_action_counts.get(action, 0) + 1

    def record_return(record: dict[str, Any]) -> float:
        return float(record["return_since_first_pct"])

    if tracked:
        returns = [record_return(record) for record in tracked]
        best = max(tracked, key=record_return)
        worst = min(tracked, key=record_return)
        summary = {
            "tracked_count": len(tracked),
            "active_count": sum(1 for record in records if record["active_in_current_pool"]),
            "exited_count": sum(1 for record in records if not record["active_in_current_pool"]),
            "average_return_pct": round_or_none(sum(returns) / len(returns)),
            "positive_count": sum(1 for value in returns if value > 0),
            "negative_count": sum(1 for value in returns if value < 0),
            "attribution_counts": dict(sorted(attribution_counts.items())),
            "model_action_counts": dict(sorted(model_action_counts.items(), key=lambda item: (-item[1], item[0]))),
            "best": {
                "code": best["code"],
                "name": best["name"],
                "return_since_first_pct": best["return_since_first_pct"],
            },
            "worst": {
                "code": worst["code"],
                "name": worst["name"],
                "return_since_first_pct": worst["return_since_first_pct"],
            },
        }
    else:
        summary = {
            "tracked_count": 0,
            "active_count": sum(1 for record in records if record["active_in_current_pool"]),
            "exited_count": sum(1 for record in records if not record["active_in_current_pool"]),
            "average_return_pct": None,
            "positive_count": 0,
            "negative_count": 0,
            "attribution_counts": dict(sorted(attribution_counts.items())),
            "model_action_counts": dict(sorted(model_action_counts.items(), key=lambda item: (-item[1], item[0]))),
            "best": None,
            "worst": None,
        }

    records.sort(
        key=lambda record: (
            record.get("return_since_first_pct") is None,
            -(record.get("return_since_first_pct") or 0),
            record["code"],
        )
    )
    for index, record in enumerate(records, start=1):
        record["review_rank"] = index

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "as_of_date": as_of_date,
        "summary": summary,
        "records": records,
    }


def load_daily(code: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_daily(symbol=ak_symbol(code), adjust="qfq")
    if df.empty:
        raise RuntimeError(f"{code} daily data is empty")

    df = df.rename(columns={col: col.lower() for col in df.columns})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").tail(140).reset_index(drop=True)
    return df


def analyze_candidate(candidate: Candidate, live_quote: dict[str, Any] | None = None) -> dict[str, Any]:
    df = load_daily(candidate.code)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    daily_latest_close = float(close.iloc[-1])
    daily_as_of_date = df["date"].iloc[-1].strftime("%Y-%m-%d")
    latest_close = daily_latest_close
    as_of_date = daily_as_of_date
    price_source = "daily_qfq"
    live_quote = live_quote or {}
    live_close = safe_float(live_quote.get("live_close"))
    live_quote_date = live_quote.get("live_quote_date")
    if live_close is not None and live_close > 0 and live_quote_date:
        live_date = parse_date_value(live_quote_date)
        daily_date = parse_date_value(daily_as_of_date)
        if live_date is None or daily_date is None or live_date >= daily_date:
            latest_close = live_close
            as_of_date = str(live_quote_date)
            price_source = "spot_snapshot"
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else ma20
    high20 = float(high.tail(20).max())
    low20 = float(low.tail(20).min())
    previous_high_window = high.iloc[-21:-1] if len(high) > 1 else high.tail(1)
    previous_high20 = float(previous_high_window.max()) if not previous_high_window.empty else high20
    pct60 = (latest_close / float(close.iloc[-60]) - 1) * 100 if len(close) >= 60 else None

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = float(tr.tail(14).mean())
    volatility20 = float(close.pct_change().tail(20).std() * np.sqrt(20) * 100)

    aggressive = min(ma20, latest_close - atr14 * 0.7)
    stable = max(ma60, low20 + (high20 - low20) * 0.25)
    if stable > aggressive:
        stable = aggressive * 0.94

    breakout_confirm = max(previous_high20 * 1.01, ma20 + atr14 * 0.6)
    breakout_gap_pct = (breakout_confirm / latest_close - 1) * 100 if latest_close else None
    no_chase = max(aggressive * 1.08, breakout_confirm * 1.05, ma20 + atr14 * 1.8)
    invalid = min(ma60 * 0.92, latest_close - atr14 * 3.0)
    recommended_entry = stable + (aggressive - stable) * 0.4
    entry_gap_pct = (latest_close / recommended_entry - 1) * 100 if recommended_entry else None
    pullback_buy_lower = max(stable, invalid)
    pullback_buy_upper = aggressive
    breakout_buy_upper = min(no_chase, breakout_confirm * 1.025, breakout_confirm + atr14 * 0.8)

    overheat_penalty = 0.0
    risk_notes = list(candidate.risks)
    if pct60 is not None and pct60 > 60:
        overheat_penalty += 0.5
        if "60日涨幅偏高" not in risk_notes:
            risk_notes.insert(0, "60日涨幅偏高")
    if latest_close > ma20 + atr14 * 1.5:
        overheat_penalty += 0.3
        if "短线偏离均线" not in risk_notes:
            risk_notes.insert(0, "短线偏离均线")
    if volatility20 > 25:
        overheat_penalty += 0.2
        if "波动偏高" not in risk_notes:
            risk_notes.insert(0, "波动偏高")

    score = max(0.0, candidate.base_score - overheat_penalty)

    if latest_close > no_chase:
        status_key = "avoid"
        intervention_status = "不追高"
        position_hint = "价格高于不追高线，等待涨幅和换手降温。"
        trigger = "先等待回到观察区间，或重新形成新的平台后再复核突破价。"
        entry_note = "当前高于不追高线，不按现价追入；等回落到推荐接入价附近再复核。"
        breakout_note = "突破已经偏离合理追踪区，不把突破当成追高理由。"
    elif latest_close >= breakout_confirm and latest_close >= ma20:
        status_key = "breakout"
        intervention_status = "突破确认"
        position_hint = "只适合小仓位试探，后续必须用不跌回突破价和成交量延续来验证。"
        trigger = "已站上突破确认价；若回踩不破突破价且量能不明显萎缩，可继续观察强趋势接入。"
        entry_note = "未给出理想回撤，但价格已触发突破路径；回撤接入价仍作为更稳健的加仓纪律。"
        breakout_note = "突破路径已触发，重点看收盘能否守住突破价，以及次日是否放量/缩量不破。"
    elif latest_close <= aggressive and latest_close >= invalid:
        status_key = "watch"
        intervention_status = "观察区"
        position_hint = "可进入观察清单，只适合小仓位分批验证。"
        trigger = "价格落入观察区间后，等待缩量企稳或重新放量确认。"
        entry_note = "已进入观察区，接入价仅作为分批纪律参考，仍需看量价确认。"
        breakout_note = "尚未触发突破路径；若放量站上突破确认价，可按强趋势路径重新评估。"
    else:
        status_key = "wait"
        intervention_status = "等回踩"
        position_hint = "暂以观察为主，等价格接近观察区间再考虑。"
        trigger = "接近观察区间且产业链证据未变弱时，再重新评估。"
        entry_note = "当前未到理想接入位置，优先等待价格回落到推荐接入价附近。"
        breakout_note = "若不回踩而直接站上突破确认价，可转入小仓位突破验证路径。"

    buy_signal_key = "wait"
    buy_signal_label = "等待触发"
    is_buyable_now = False
    buyable_price = None
    buyable_lower = None
    buyable_upper = None
    next_buy_price = None
    buy_price_path = "等待"
    buy_price_note = "当前没有触发可买入观察信号，只保留跟踪。"

    if status_key == "watch":
        buy_signal_key = "pullback_buy"
        buy_signal_label = "可小仓低吸"
        is_buyable_now = True
        buyable_price = pullback_buy_upper
        buyable_lower = pullback_buy_lower
        buyable_upper = pullback_buy_upper
        next_buy_price = buyable_price
        buy_price_path = "回撤接入"
        buy_price_note = "价格已落入回撤观察区，研究口径允许小仓位分批验证；理想价靠近推荐接入价，最高不超过可买价。"
    elif status_key == "breakout":
        if latest_close <= breakout_buy_upper:
            buy_signal_key = "breakout_buy"
            buy_signal_label = "可突破试探"
            is_buyable_now = True
            buyable_price = breakout_buy_upper
            buyable_lower = breakout_confirm
            buyable_upper = breakout_buy_upper
            next_buy_price = breakout_confirm
            buy_price_path = "突破确认"
            buy_price_note = "价格已站上突破确认价但未明显追高，只适合小仓位试探；后续必须用不跌回突破价和量能延续验证。"
        else:
            buy_signal_key = "breakout_wait"
            buy_signal_label = "突破偏高"
            buyable_price = None
            buyable_lower = breakout_confirm
            buyable_upper = breakout_buy_upper
            next_buy_price = breakout_buy_upper
            buy_price_path = "等突破回踩"
            buy_price_note = "已经突破但偏离可接受上限，等待回踩到突破可买区间再复核。"
    elif status_key == "avoid":
        buy_signal_key = "avoid"
        buy_signal_label = "不可追高"
        next_buy_price = pullback_buy_upper
        buy_price_path = "等降温回撤"
        buy_price_note = "价格高于不追高线，不给当前可买价；优先等回撤到观察区间上沿以内。"
    else:
        pullback_gap = abs(latest_close / pullback_buy_upper - 1) if pullback_buy_upper else float("inf")
        breakout_gap = abs(breakout_confirm / latest_close - 1) if breakout_confirm >= latest_close else float("inf")
        if breakout_gap < pullback_gap:
            next_buy_price = breakout_confirm
            buy_price_path = "等突破确认"
            buy_price_note = "当前未触发买入观察信号；若放量站上突破确认价，可转入突破试探路径。"
        else:
            next_buy_price = pullback_buy_upper
            buy_price_path = "等回撤接入"
            buy_price_note = "当前未触发买入观察信号；优先等待回撤到观察区间上沿以内。"

    try:
        chip_meta = calculate_chip_metrics(
            daily_chip_records(df),
            latest_close,
            "Sina daily qfq turnover + local CYQ estimate",
        )
    except Exception as exc:
        chip_meta = empty_chip_meta()
        chip_meta["chip_note"] = f"筹码计算暂缺：{compact_error(exc)}"

    return {
        "code": candidate.code,
        "name": candidate.name,
        "board": board_for(candidate.code),
        "theme": candidate.theme,
        "logic": candidate.logic,
        "score": round(score, 1),
        "close": round_or_none(latest_close),
        "price_source": price_source,
        "daily_as_of_date": daily_as_of_date,
        "daily_close": round_or_none(daily_latest_close),
        "live_quote_date": live_quote_date,
        "live_quote_snapshot_at": live_quote.get("live_quote_snapshot_at"),
        "live_quote_price": round_or_none(live_close),
        "ma20": round_or_none(ma20),
        "ma60": round_or_none(ma60),
        "atr14": round_or_none(atr14),
        "pct60": round_or_none(pct60),
        "volatility20": round_or_none(volatility20),
        "aggressive_price": round_or_none(aggressive),
        "stable_price": round_or_none(stable),
        "recommended_entry_price": round_or_none(recommended_entry),
        "entry_price_lower": round_or_none(stable),
        "entry_price_upper": round_or_none(aggressive),
        "entry_gap_pct": round_or_none(entry_gap_pct),
        "entry_price_note": entry_note,
        "breakout_confirm_price": round_or_none(breakout_confirm),
        "breakout_buy_upper_price": round_or_none(breakout_buy_upper),
        "breakout_gap_pct": round_or_none(breakout_gap_pct),
        "breakout_price_note": breakout_note,
        "resistance_price": round_or_none(previous_high20),
        "is_buyable_now": is_buyable_now,
        "buy_signal_key": buy_signal_key,
        "buy_signal_label": buy_signal_label,
        "buyable_price": round_or_none(buyable_price),
        "buyable_price_lower": round_or_none(buyable_lower),
        "buyable_price_upper": round_or_none(buyable_upper),
        "next_buy_trigger_price": round_or_none(next_buy_price),
        "buy_price_path": buy_price_path,
        "buy_price_note": buy_price_note,
        "watch_zone": f"{stable:.2f}-{aggressive:.2f}",
        "no_chase_price": round_or_none(no_chase),
        "invalid_price": round_or_none(invalid),
        "status_key": status_key,
        "intervention_status": intervention_status,
        "trigger_condition": trigger,
        "position_hint": position_hint,
        "catalysts": candidate.catalysts,
        "risks": risk_notes,
        "as_of_date": as_of_date,
        **chip_meta,
    }


def fallback_latest(error: Exception) -> dict[str, Any]:
    if not LATEST_PATH.exists():
        raise error
    data = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    data["generated_at"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
    data["source_status"] = {
        "quotes": "fallback seeded/latest json",
        "fallback": True,
        "note": f"自动刷新失败，沿用上一版数据。错误：{error}",
    }
    return data


def build_payload() -> dict[str, Any]:
    rows = []
    errors = []
    candidate_library = all_candidates()
    selected_candidates, candidate_meta, universe_payload, universe_warnings = select_candidates_from_universe(candidate_library)
    errors.extend(universe_warnings)

    for candidate in selected_candidates:
        try:
            meta = candidate_meta.get(candidate.code, {})
            row = analyze_candidate(candidate, meta)
            layer_one_bonus = safe_float(meta.get("layer_one_bonus")) or 0.0
            fund_flow_bonus = safe_float(meta.get("fund_flow_bonus")) or 0.0
            chip_bonus = safe_float(row.get("chip_bonus")) or 0.0
            row["score"] = round(row["score"] + layer_one_bonus + fund_flow_bonus + chip_bonus, 1)
            row["candidate_source"] = meta.get("candidate_source") or "主题库"
            row["layer_one_score"] = meta.get("layer_one_score")
            row["layer_one_rank"] = meta.get("layer_one_rank")
            row["layer_one_pct_chg"] = meta.get("layer_one_pct_chg")
            row["layer_one_amount"] = meta.get("layer_one_amount")
            row["theme_group"] = meta.get("theme_group") or theme_group(candidate.theme)
            row["theme_strength_score"] = meta.get("theme_strength_score")
            row["theme_strength_label"] = meta.get("theme_strength_label")
            row["theme_strength_rank"] = meta.get("theme_strength_rank")
            row["theme_strength_bonus"] = meta.get("theme_strength_bonus")
            for key in FUND_FLOW_KEYS:
                row[key] = meta.get(key)
            rows.append(row)
        except Exception as exc:  # network/free data sources are not always stable
            errors.append(f"{candidate.code} {candidate.name}: {exc}")

    if not rows:
        raise RuntimeError("; ".join(errors) or "no rows generated")

    as_of_date = max(row["as_of_date"] for row in rows)
    generated_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    market_environment = universe_payload.get("market_environment") or {}
    current_feedback_context = feedback_context_meta(market_environment, generated_at)
    apply_feedback_context(rows, current_feedback_context)
    universe_payload["update_phase"] = current_feedback_context["update_phase"]
    universe_payload["update_phase_label"] = current_feedback_context["update_phase_label"]
    feedback_payload = build_model_feedback(rows, as_of_date, market_environment)
    for row in rows:
        feedback_meta = feedback_effect_for_row(row, feedback_payload)
        entry_safety_meta = entry_safety_effect_for_row(row, feedback_payload)
        row.update(feedback_meta)
        row.update(entry_safety_meta)
        apply_feedback_price_adjustment(row, feedback_meta, entry_safety_meta)
        feedback_bonus = safe_float(feedback_meta.get("feedback_bonus")) or 0.0
        row["score"] = round(row["score"] + feedback_bonus, 1)
        apply_market_theme_context(row, market_environment)

    concentration_payload = apply_portfolio_concentration_control(rows)
    rows.sort(key=lambda row: row["score"], reverse=True)
    rows = rows[:FINAL_POOL_SIZE]
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    attach_tracking(rows, as_of_date)
    tracking_summary = build_tracking_summary(rows)
    review_quotes_by_code = {
        code: meta
        for code, meta in candidate_meta.items()
        if safe_float(meta.get("live_close")) is not None and meta.get("live_quote_date")
    }
    review_payload = build_review_center(rows, as_of_date, review_quotes_by_code)
    counts = {
        "total": len(rows),
        "watch": sum(1 for row in rows if row["status_key"] == "watch"),
        "breakout": sum(1 for row in rows if row["status_key"] == "breakout"),
        "wait": sum(1 for row in rows if row["status_key"] == "wait"),
        "avoid": sum(1 for row in rows if row["status_key"] == "avoid"),
        "buyable": sum(1 for row in rows if row.get("is_buyable_now")),
        "buyable_pullback": sum(1 for row in rows if row.get("buy_signal_key") == "pullback_buy"),
        "buyable_breakout": sum(1 for row in rows if row.get("buy_signal_key") == "breakout_buy"),
        "entry_risk_flagged": sum(1 for row in rows if row.get("entry_safety_risk_flag")),
        "buy_signal_blocked": sum(1 for row in rows if row.get("entry_safety_block_buy")),
        "market_context_blocked": sum(1 for row in rows if row.get("market_context_block_buy")),
        "decision_grade_a": sum(1 for row in rows if row.get("decision_grade") == "A"),
        "decision_grade_b": sum(1 for row in rows if row.get("decision_grade") == "B"),
        "decision_grade_c": sum(1 for row in rows if row.get("decision_grade") == "C"),
        "decision_grade_d": sum(1 for row in rows if row.get("decision_grade") == "D"),
        "concentration_penalized": sum(1 for row in rows if safe_float(row.get("portfolio_concentration_penalty"))),
    }
    theme_exposure = summarize_theme_exposure(rows)
    counts["risk_gated"] = counts["buy_signal_blocked"]
    if counts["market_context_blocked"]:
        overall_signal = f"{counts['market_context_blocked']}只可买信号被市场环境层降级"
    elif counts["buy_signal_blocked"]:
        overall_signal = f"{counts['buy_signal_blocked']}只可买信号被回访接入风控拦截"
    elif counts["buyable"]:
        risk_suffix = f"，{counts['entry_risk_flagged']}只带接入风险标记" if counts["entry_risk_flagged"] else ""
        overall_signal = f"{counts['buyable']}只触发可买入观察信号{risk_suffix}"
    elif counts["entry_risk_flagged"]:
        overall_signal = f"{counts['entry_risk_flagged']}只观察标的带接入风险标记"
    elif counts["watch"]:
        overall_signal = "有少量标的进入观察区"
    elif counts["breakout"]:
        overall_signal = "部分标的进入突破确认"
    elif counts["avoid"] >= len(rows) / 2:
        overall_signal = "整体偏热，谨慎追高"
    else:
        overall_signal = "以等待回踩为主"

    return {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "refresh_trigger": os.getenv("GITHUB_TRIGGER_SOURCE") or os.getenv("GITHUB_EVENT_NAME") or "local",
        "update_phase": current_feedback_context["update_phase"],
        "update_phase_label": current_feedback_context["update_phase_label"],
        "market": "A股主板",
        "source_status": {
            "quotes": "akshare.stock_zh_a_spot intraday snapshot + stock_zh_a_daily qfq / Sina + Eastmoney/Tonghuashun fund flow + Sina turnover chip estimate",
            "fallback": False,
            "note": "免费数据源可能延迟或限流；关键决策请复核实时行情。",
            "warnings": errors,
        },
        "model": {
            "name": "Two-layer Serenity main-board screen",
            "description": "第一层扫描全A股主板的趋势、动量、流动性和主力资金流；第二层再做产业链瓶颈、供需验证、催化剂、筹码结构和估值重估筛选；价格纪律拆成回撤接入和突破确认两条路径。",
            "board_filter": "000/001/002/003/600/601/603/605",
            "universe_layer": "全主板行情快照粗筛",
            "serenity_layer": "战略主题库 + 产业链瓶颈深度打分",
            "chip_factor": "筹码结构用于辅助确认成本分布和兑现压力，不单独构成买卖依据。",
            "feedback_factor": "回访中心历史表现会按信号归因形成反馈分；低样本阶段强制收缩并限制单股影响。",
            "price_feedback_factor": "推荐接入价和可买价会跟随回访反馈做小幅纪律校正；正反馈略放宽，负反馈收紧，不改变不追高线。",
            "entry_safety_factor": "接入有效性层会复盘历史可买/触达/未触达接入价样本的后续收益、最大不利回撤和暴跌率；风险偏高时先标记接入风险并下压接入价，只有原本可买的信号才会被取消为 risk_wait。",
            "market_context_factor": "市场环境层会根据全主板涨跌扩散、强弱股数量、涨停跌停差和收盘位置计算市场温度；主题强度层会按战略主题组的排名、资金流和扩散度修正排序、仓位等级和可买信号。",
            "feedback_enhancement_factor": "反馈增强层会过滤乱码/异常因子，将筹码缺失视为数据质量而非负面信号，并按市场阶段、更新时间段、回访归因和主题拥挤度进行小幅参数校准。",
            "candidates": [asdict(candidate) for candidate in candidate_library],
        },
        "universe_scan": universe_payload,
        "model_feedback": feedback_payload,
        "portfolio_concentration": concentration_payload,
        "summary": {**counts, "overall_signal": overall_signal, "tracking": tracking_summary, "theme_exposure": theme_exposure},
        "review": review_payload,
        "stocks": rows,
    }


def history_snapshot_filename(payload: dict[str, Any]) -> str:
    dt = parse_datetime_value(payload.get("generated_at")) or datetime.now(CN_TZ)
    return f"{dt.strftime('%Y-%m-%dT%H%M%S%z')}.json"


def prune_history_snapshots() -> None:
    if not HISTORY_SNAPSHOT_DIR.exists():
        return
    files = sorted(
        HISTORY_SNAPSHOT_DIR.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[HISTORY_SNAPSHOT_RETENTION:]:
        try:
            path.unlink()
        except OSError:
            pass


def write_payload(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    HISTORY_DIR.mkdir(exist_ok=True)
    HISTORY_SNAPSHOT_DIR.mkdir(exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    LATEST_PATH.write_text(text + "\n", encoding="utf-8")
    review_payload = payload.get("review")
    if isinstance(review_payload, dict):
        REVIEW_PATH.write_text(json.dumps(review_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    universe_payload = payload.get("universe_scan")
    if isinstance(universe_payload, dict):
        UNIVERSE_PATH.write_text(json.dumps(universe_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    feedback_payload = payload.get("model_feedback")
    if isinstance(feedback_payload, dict):
        MODEL_FEEDBACK_PATH.write_text(json.dumps(feedback_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    history_path = HISTORY_DIR / f"{payload['as_of_date']}.json"
    history_path.write_text(text + "\n", encoding="utf-8")
    snapshot_path = HISTORY_SNAPSHOT_DIR / history_snapshot_filename(payload)
    snapshot_path.write_text(text + "\n", encoding="utf-8")
    prune_history_snapshots()


def main() -> None:
    try:
        payload = build_payload()
    except Exception as exc:
        payload = fallback_latest(exc)
    write_payload(payload)
    print(f"wrote {LATEST_PATH}")
    print(f"as_of_date={payload.get('as_of_date')} fallback={payload.get('source_status', {}).get('fallback')}")


if __name__ == "__main__":
    main()
