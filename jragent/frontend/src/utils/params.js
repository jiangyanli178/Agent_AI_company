// 参数的中文化与解释，降低新手的理解门槛
// key 与后端 param_schema 里的字段名一一对应

export const PARAM_LABELS = {
  fast: { label: '快线天数', hint: '短期均线，比如 7 就是最近 7 天的平均价。越小越灵敏，信号也越多' },
  slow: { label: '慢线天数', hint: '长期均线，比如 25 就是最近 25 天的平均价。越大越迟钝，但更稳' },
  n: { label: '计算周期', hint: '用最近多少天的数据来计算指标，常见的是 14 或 20 天' },
  k: { label: '标准差倍数', hint: '布林带宽度，越大越不容易触发信号，常用 2 倍' },
  lookback: { label: '回看天数', hint: '看过去多少天的涨跌幅来判断动量强弱' },
  threshold: { label: '动量阈值', hint: '涨幅超过这个数才进场，0.03 就是 3%' },
  low: { label: '超卖线', hint: 'RSI 低于这个值认为"跌过头了"，可能反弹，常见 30' },
  high: { label: '超买线', hint: 'RSI 高于这个值认为"涨过头了"，可能回落，常见 70' },
  signal: { label: '信号线周期', hint: '对 DIF 再做一次平滑得到 DEA，两条线交叉才算信号，常用 9' },
  buy_below: { label: '买入上限（K 值）', hint: '只有 K 低于这个数才认金叉。调低买得更便宜，但可能很久不触发' },
  sell_above: { label: '卖出下限（K 值）', hint: 'K 高于这个数就离场，避免在超买区硬扛' },
  entry: { label: '突破几日新高买入', hint: '收盘价超过过去这么多天的最高价才买，海龟用 20 或 55' },
  exit: { label: '跌破几日新低卖出', hint: '收盘价跌破过去这么多天的最低价就走，通常比 buy 短' },
  zero_filter: { label: '只在零轴上方买', hint: '打开后只认 DIF>0 的金叉，躲掉下跌趋势里的反弹假信号' },
}

// spec 是后端 param_schema 里的那一项，可能自带 label。
// 优先用它，这样后台新增策略类型时前端不用跟着改。
export function paramLabel(key, spec) {
  return spec?.label || PARAM_LABELS[key]?.label || key
}

export function paramHint(key, spec) {
  return PARAM_LABELS[key]?.hint || ''
}

// 让标题跟随真实参数变化，而不是一直显示策略库里的默认名
export function displayName(strategy, params) {
  const p = { ...(strategy?.default_params || {}), ...(params || {}) }
  const type = strategy?.strategy_type
  const name = strategy?.name || ''

  if (type === 'ma_cross') return `${p.fast}/${p.slow} 均线金叉`
  if (type === 'boll') return `${p.n} 日布林带（${p.k} 倍）`
  if (type === 'boll_break') return `${p.n} 日布林突破`
  if (type === 'momentum') return `${p.lookback} 日动量`
  if (type === 'rsi') return `RSI ${p.n}`
  if (type === 'macd_cross') {
    return `MACD ${p.fast}/${p.slow}/${p.signal}${p.zero_filter ? '（零轴上方）' : ''}`
  }
  if (type === 'kdj_cross') return `KDJ ${p.n}（K<${p.buy_below} 买入）`
  if (type === 'breakout') return `${p.entry}/${p.exit} 唐奇安突破`
  if (type === 'trix_cross') return `TRIX ${p.n}`
  return name
}

// 给新手的一句话策略解释
export function strategyExplain(strategy, params) {
  const p = { ...(strategy?.default_params || {}), ...(params || {}) }
  switch (strategy?.strategy_type) {
    case 'ma_cross':
      return `当 ${p.fast} 日均线从下往上穿过 ${p.slow} 日均线时买入（叫金叉），从上往下穿过时卖出（叫死叉）。`
    case 'boll':
      return `价格跌破 ${p.n} 日布林带下轨时买入，回到中轨时卖出，赌它"跌多了会弹回来"。`
    case 'boll_break':
      return `价格突破 ${p.n} 日布林带上轨时买入，跌破下轨时卖出，赌它"突破后会继续涨"。`
    case 'momentum':
      return `过去 ${p.lookback} 天涨幅超过 ${(p.threshold * 100).toFixed(0)}% 就持有，否则空仓，赌"强者恒强"。`
    case 'rsi':
      return `RSI 低于 ${p.low} 时买入（超卖反弹），高于 ${p.high} 时卖出（超买回落）。`
    case 'macd_cross':
      return `DIF（快慢均线之差）${p.zero_filter ? '在零轴上方且' : ''}上穿 DEA 时买入，下穿时卖出。` +
        (p.zero_filter ? '加了零轴条件，只做上涨趋势里的回调结束，躲掉下跌中继的假金叉。' : '')
    case 'kdj_cross':
      return `K 线在 ${p.buy_below} 以下上穿 D 线时买入，K 超过 ${p.sell_above} 或死叉时卖出。`
    case 'breakout':
      return `收盘价突破过去 ${p.entry} 天最高价时买入，跌破过去 ${p.exit} 天最低价时卖出（海龟交易法）。`
    case 'trix_cross':
      return `收盘价连做三次平滑后的变化率上穿信号线时买入，下穿时卖出。信号很少但很干净。`
    default:
      return strategy?.summary || ''
  }
}
