# A-share-LI 回访反馈模型说明

本文档用于审查 `scripts/generate_pool.py` 中的回访反馈机制。模型输出不是投资建议，只是把历史推荐结果转化为可审计的排序修正。

## 目标

旧版回访中心只展示推荐后收益，不能反向改善模型。新版反馈模型做三件事：

1. 记录每次推荐当时的信号快照。
2. 按信号维度归因后续表现。
3. 用经过收缩和封顶的反馈分修正下一轮候选排序。

## 参与归因的信号

当前按以下维度做归因：

- 主题分组：例如 AI半导体、电力设备、资源周期、大金融。
- 买入信号：可小仓低吸、可突破试探、等待触发、不可追高等。
- 状态：观察区、突破确认、等回踩、不追高。
- 资金流：明显流入、温和流入、中性、流出或暂缺。
- 筹码：筹码较健康、中性偏好、中性、压力较大等。
- 全主板排名：前50、前120、前300、300名以后。
- 接入价偏离：低于接入价、贴近接入价、略高于接入价、明显高于接入价。
- 价格源：日线或盘中快照。

## 收益评价

模型用历史快照比较同一只股票在后续快照中的价格变化：

- 个股收益 = 后续价格 / 推荐时价格 - 1。
- 同期基准 = 同期推荐池可匹配股票的平均收益。
- 超额收益 = 个股收益 - 同期基准。

反馈模型看的是超额收益，而不是单纯涨跌。这样可以减少大盘或板块整体波动造成的误判。

## 防过拟合机制

样本少时，模型不会大幅调整排序。

- 样本收缩：`raw_count / (raw_count + 8)`。
- 时间衰减：越新的验证样本权重越高。
- 周期衰减：越远的 horizon 权重越低。
- 单股反馈分封顶：默认 `±0.8` 分。
- 低样本阶段明确标记低置信。

## 主要输出

`data/model_feedback.json` 是独立审查入口：

- `observation_count`：有效验证样本数。
- `confidence`：总体反馈置信度。
- `summary.top_positive`：历史表现较好的信号。
- `summary.top_negative`：历史表现较差的信号。
- `factor_stats`：每个信号维度的样本数、平均收益、平均超额收益、命中率、置信度、分数效果。

`data/latest.json` 中每只股票新增：

- `feedback_bonus`：本轮回访反馈修正分。
- `feedback_label`：正反馈、负反馈、中性或样本不足。
- `feedback_confidence`：整体置信度。
- `feedback_note`：影响最大的几个历史信号。
- `feedback_factors`：可机器审查的因子贡献列表。

## 价格反馈调整

反馈模型不仅影响排序，也会小幅调整价格纪律：

- 正反馈：历史上类似信号组合表现较好时，推荐接入价和可买价会轻微上移。
- 负反馈：历史上类似信号组合表现较差时，推荐接入价和可买价会下压，要求更便宜的位置。
- 不追高线不被反馈放宽，避免把历史反馈变成追高理由。
- 调整幅度来自 `feedback_bonus * 1.2`，并限制在 `-1.2%` 到 `+1.0%`。
- 每只股票保留原始价格字段，例如 `base_recommended_entry_price`，方便审查调整前后差异。

相关输出字段：

- `price_feedback_adjustment_pct`：价格纪律调整比例。
- `price_feedback_label`：价格纪律略放宽、收紧或不变。
- `price_feedback_note`：调整原因。
- `base_recommended_entry_price` / `base_buyable_price`：调整前价格。

## 当前限制

- 历史样本仍少，早期反馈分会很小，这是刻意设计。
- 归因只能说明历史相关性，不能证明因果。
- 当前仍以规则模型为主，反馈模型是排序校正层。
- 后续可以增加更细的分时快照、指数基准、行业基准、调出后收益衰减和因子稳定性检验。

## 自动更新

GitHub Actions 会在工作日北京时间 10:00、11:20、13:30、14:30、20:00 自动运行 `scripts/generate_pool.py`。其中每个快照都会同时观察买入和卖出；10:00、11:20、13:30 属于正常买入复检和卖出预警窗口，14:30 属于尾盘卖出/风控和严格买入复检窗口，20:00 属于次日关注计划。每次运行都会刷新：

- `data/latest.json`
- `data/review.json`
- `data/universe_scan.json`
- `data/model_feedback.json`
- `data/history/{日期}.json`

工作流会先同步远端最新 `main`，生成数据后提交 `data/` 目录，推送前再 rebase 一次，降低自动刷新和人工提交之间的冲突概率。

同一只股票如果调出后再次进入观察池，不会重置首次推荐锚点。回访链条会优先保留 `review.json` 中最早的 `first_recommend_date` 和 `first_recommend_price`；同一天多次刷新时，内部使用“日期+来源”记录，避免晚间重新入池价格覆盖上午首次推荐价。

## 卖出后有效性反馈

模拟账户的每一笔卖出会生成独立的 `exitReviews` 记录，并按卖出成交价跟踪卖出后第 3、5、20、30 个交易日收盘收益。卖出当日不计入样本，同一天多个分时快照只保留最新值；20 点快照直接结算当日样本，若缺少晚间快照，则由下一交易日快照补结算前一日。股票重新进入观察池或再次成交不会覆盖旧卖出记录。

- 卖出后收益为正：表示卖出后的机会成本，可能卖早。
- 卖出后收益为负：表示卖出后避开的损失，退出方向有效。
- 每笔记录保留卖出原因、成交价、已实现盈亏、阶段收益、最大反弹和最大续跌。
- 参数反馈按退出原因分别归因，避免把止损、止盈、评分退出混为同一类样本。
- 同类退出至少 3 笔达到对应周期后才允许调参，且每次只做小幅、有边界的调整。

反馈可影响后续新仓的硬止损、目标止盈，以及持仓的移动止盈、评分退出阈值和最长持有期。网页“卖出后回访”展示当前成熟度、实际生效参数和调参原因；`data/latest.json.model_feedback.exit_effectiveness` 提供机器可审计入口。

## 接入价有效性反馈

新增 `entry_effectiveness` 层，专门回答“按当时推荐接入价/可买价介入后，是否容易出现较大回撤”：

- 样本来源：历史 `data/history/*.json` 和 `data/history/snapshots/*.json` 快照中，所有存在 `recommended_entry_price` 的记录都会进入接入价复盘。
- 样本分层：`actual_buyable` 表示当时已经给出可买价，`touched_entry` 表示后续最低价触达推荐接入价，`untouched_wait` 表示后续没有触达、属于等待价。
- 计算口径：`actual_buyable` 优先使用当时 `buyable_price`，其余使用 `recommended_entry_price` 作为接入参考价。
- 复盘指标：总体接入价收益、触达后收益、未触达错过收益、触达率、最大不利回撤、命中率、暴跌率。
- 暴跌定义：后续收益低于 `-5%`，或期间最大不利回撤低于 `-7%`。
- 使用方式：触达样本用于判断“按价买入后是否容易暴跌”；未触达等待样本只用于判断接入价是否过于保守，不直接放大可买入信号。
- 风控方式：风险偏高时先写入 `entry_safety_risk_flag` 并下压推荐接入价和可买价；只有原本处于 `is_buyable_now=true` 的股票，才会进一步写入 `entry_safety_block_buy` 并改为 `risk_wait`。

新增字段：

- `model_feedback.entry_effectiveness`：全局接入价有效性统计。
- `model_feedback.entry_effectiveness.touched_observation_count`：触达接入价或当时可买的样本数。
- `model_feedback.entry_effectiveness.untouched_wait_observation_count`：未触达等待价的样本数。
- `entry_safety_adjustment_pct`：单股接入安全层带来的价格调整。
- `entry_safety_label` / `entry_safety_note`：单股接入风险解释。
- `entry_safety_risk_flag`：是否带接入风险标记。它说明当前接入位置偏谨慎，不等于原本有可买信号。
- `entry_safety_factors[].avg_touch_return_pct`：同类触达样本买入后的平均收益。
- `entry_safety_factors[].avg_missed_return_pct`：同类未触达等待样本后续涨跌，用来判断接入价是否过保守。
- `entry_safety_factors[].touch_rate_pct`：同类样本触达推荐接入价的比例。
- `entry_safety_block_buy`：是否因历史接入风险取消当前可买入标记。新版中它只统计真正“原本可买、后被取消”的样本。
- `summary.entry_risk_flagged`：当前股票池中带接入风险标记的数量。
- `summary.buy_signal_blocked`：当前股票池中可买信号被接入风控取消的数量；`summary.risk_gated` 保留为兼容别名。
- `review.records[].entry_return_from_first_entry_pct`：按首次接入参考价计算的回访收益。
- `review.records[].entry_drawdown_from_first_entry_pct`：按首次接入参考价计算的回访最大不利回撤。

## 市场环境与主题强度层

新增 `market_environment` 与 `theme_strength` 两层，用来回答“今天适不适合买股票、适合买哪条线”：

- `universe_scan.market_environment`：基于全主板涨跌扩散、强弱股数量、涨停跌停差、收盘位置和成交额计算市场温度。
- `universe_scan.theme_strength`：按战略主题组统计主题库股票的全主板排名、涨跌扩散、强势股占比和资金流，形成主题温度榜。
- `market_context_score_bonus`：市场温度和主题强度给单股排序带来的上下文修正。
- `market_context_price_adjustment_pct`：市场/主题环境对接入价纪律的轻微修正。
- `market_context_block_buy`：弱市或防守市中，原本可买但环境不支持放大试错时，临时降级为等待。
- `decision_grade` / `decision_grade_label`：把单股输出拆成 A/B/C/D 观察等级，减少“可买/不可买”的二元误读。
- `summary.market_context_blocked`：当前股票池中被市场环境层降级的可买信号数量。

优先级关系：

1. 全主板市场温度决定当天风险偏好。
2. 主题强度决定同一股票逻辑是否处于主线扩散阶段。
3. 接入价有效性决定当前价格是否安全。
4. 三层共同影响最终排序、价格纪律、可买信号和仓位提示。

## 上涨趋势交易层

趋势层不再只检查现价距离 MA20 的远近，而是把交易资格拆成四个同时满足的条件：

- 现价高于 MA20，避免在短期均线下方接下跌惯性。
- MA20 高于 MA60，要求中短期均线已经形成多头排列。
- MA20 近 5 个交易日斜率为正，过滤均线仍向下但价格短暂反弹的情况。
- MA60 近 10 个交易日下行不超过 1%，允许长期均线在刚转强时轻微滞后，但不接受长期趋势持续恶化。

满足条件的股票标记为 `trend_trade_eligible=true`，在最终池排序中优先，并获得有限趋势加分。未满足条件的股票仍可进入研究观察池，但会被标记为 `trend_block_buy=true`，不生成或执行模拟买入计划；已有待买计划在后续快照趋势转弱时自动取消。趋势标签同时进入普通回访和接入价有效性反馈，后续可比较上涨趋势、反弹修复、震荡走弱和下降趋势的真实样本表现。

## 资金热度与全球市场层

- `universe_scan.market_fund_heat` 把全主板换手率中位数、主力净流入股票占比、5日净流入扩散、总主力净额和成交集中度合成为资金热度。它与涨跌家数形成的市场温度分开保存，避免把“上涨”直接等同于“有增量资金”。
- `universe_scan.global_market` 跟踪富时中国A50期货、恒生、日经、纳斯达克、标普500、道琼斯、美股指数期货和在岸人民币，形成面向下一交易日开盘的影响分。
- 正常波动只小幅调整接入价和模拟仓位，不放宽原有上限；只有外盘广泛下跌、影响分显著为负且出现至少一个极端跌幅时，才设置 `global_market_hard_risk=true` 并取消新买入。
- 全球数据或资金流缺失时保持中性，同时记录来源、置信度和警告，不能把接口失败解释为利空。
- 08:50和23:10快照属于计划复核，不使用A股上一收盘价成交；20点形成初始预案，23:10观察欧美开盘，08:50使用隔夜收盘结果再校正，10点按A股实际开盘后的快照执行。

## 有限放宽与风险试探

- 接入安全层只有买入信号、个股状态或接入价偏离这三类直接入场因子，才能形成硬拦截；同时要求真实可买/触达样本不少于8个、触达占比不少于8%，且触达后收益、回撤或暴跌率达到严重门槛。市场阶段、排名和主题等宽泛因子只能降仓。
- 历史风险偏高但硬拦截证据不足时，评分不低于8.8、满足上涨趋势且个股资金流评分不低于5的原始可买信号，降为最高5%仓位的 `risk_probe`，而不是完全取消。
- `risk_probe` 不放宽失效价、不追高线、趋势门槛、市场硬风险或全球极端风险；它只把宽泛历史因子的绝对否决改为有限仓位验证。
- 股票调出最新推荐池后，旧买入计划立即取消一次，不再反复计入未成交等待。

主要字段包括 `trend_key`、`trend_label`、`trend_score`、`ma20_slope_5d_pct`、`ma60_slope_10d_pct`、`trend_trade_eligible`、`trend_block_buy` 和 `trend_action`。

## 反馈增强层

本轮新增四个模型自检与优化点：

- `model_feedback.cleaning_policy`：声明反馈清洗策略。乱码/异常值因子会被过滤，`筹码暂缺` 只作为数据质量问题，不再直接当负面筹码因子惩罚。
- `model_feedback.segmentation`：把历史反馈按 `market_regime` 和 `update_phase` 分层统计，避免早盘、尾盘、晚间关注池混用同一组反馈权重。
- `universe_scan.update_phase` / `update_phase_label`：标记本次更新属于 `10点早盘接入`、`11:20午前买入复检`、`13:30午后买入复检`、`14:30尾盘风控` 或 `20点次日关注`。
- `data/history/snapshots/`：保留最近 120 个按生成时间命名的分时快照，避免同一天多次更新互相覆盖；反馈层会按 `generated_at` 排序并保留快照原始阶段。
- 反馈周期按未来交易日计算，跳过同一天内的重复分时快照，避免把晚间/备用刷新误当成 1/3/5/10 日后表现。
- GitHub Actions 定时任务可能排队延迟或偶发漏跑，因此工作流使用主时点 + 备用时点，并把计划 cron 传入模型；模型优先按计划时段分层，避免实际执行完成时间过晚导致阶段反馈错配。
- `portfolio_concentration`：组合层主题拥挤度约束。候选池内同一主题过度集中时，后续同主题股票只做轻微排序降权，不直接否定个股逻辑。

新增单股字段：

- `portfolio_concentration_penalty`：因为同主题拥挤被扣的分数。
- `portfolio_concentration_note`：本次是否触发组合拥挤度降权的原因。
- `feedback_market_regime`：该样本进入反馈时的市场阶段。
- `update_phase_label`：该样本进入反馈时的更新时段。

新增回访字段：

- `review.records[].review_attribution.primary`：回访主归因，例如正反馈、负反馈、等待数据或中性观察。
- `review.records[].review_attribution.factors`：结构化失败/成功原因，如首推价过高、接入价失效、突破失败、冲高未保护等。
- `review.records[].review_model_action`：模型下一步应该怎么调，例如收紧接入价、降低突破试探权重、增加止盈/调出反馈权重。
- `review.summary.attribution_counts` / `model_action_counts`：回访中心对归因和调参动作的汇总。
