(() => {
  "use strict";
  const data = JSON.parse(document.getElementById("snapshot").textContent);
  const $ = (id) => document.getElementById(id);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const finite = (value) => typeof value === "number" && Number.isFinite(value);
  const format = (value, digits = 2) => finite(value) ? value.toLocaleString("zh-CN", {minimumFractionDigits:digits, maximumFractionDigits:digits}) : "—";
  const signed = (value, digits = 2) => finite(value) ? `${value > 0 ? "+" : ""}${format(value, digits)}` : "—";
  const percent = (value) => finite(value) ? value * 100 : null;
  const digits = (metric) => metric.unit === "张" ? 0 : 2;
  const stamp = (iso) => Date.parse(`${iso}T00:00:00Z`);
  const months = {"3m":3, "1y":12, "3y":36, "4y":48, "5y":60, "10y":120};
  const rangeName = {"3m":"3 个月", "1y":"1 年", "3y":"3 年", "4y":"4 年", "5y":"5 年", "10y":"10 年"};
  const frequency = {daily:"日频", weekly:"周频", monthly:"月频"};
  const stateName = {supportive:"方向支持", headwind:"方向压力", neutral:"方向中性", context:"背景观察", missing:"未计入"};
  const maxGap = {daily:7, weekly:12, monthly:45};
  const metricMap = Object.fromEntries(data.metrics.map((metric) => [metric.id, metric]));
  const coreIds = ["real_rate", "dollar", "inflation_expectation", "official_reserves", "etf", "cot"];
  let goldRange = "1y", coreRange = "1y", contextRange = "1y", reserveRange = "3y", liquidityRange = "1y", referenceRange = "1y";
  let selected = "real_rate", detailRange = "3y", activeView = "self", returnTarget = null, relationHorizon = "medium";
  const enabledMA = new Set(["ma20", "ma60"]);

  function bounds(range) {
    const end = new Date(`${data.snapshotDate}T00:00:00Z`);
    const year = end.getUTCFullYear(), month = end.getUTCMonth() - months[range];
    const first = new Date(Date.UTC(year, month, 1));
    const lastDay = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
    const start = Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), Math.min(end.getUTCDate(), lastDay));
    return [start, end.getTime()];
  }

  function rangeRows(metric, range) {
    const [start, end] = bounds(range);
    return metric.rows.filter((row) => stamp(row[0]) >= start && stamp(row[0]) <= end);
  }

  function axisNumber(value, metric, step) {
    const scale = metric.unit === "张" && Math.abs(value) >= 10000 ? 10000 : 1;
    const baseDigits = scale > 1 ? 1 : metric.unit === "%" ? 2 : Math.abs(value) >= 100 ? 0 : 1;
    const neededDigits = Math.max(0, Math.ceil(-Math.log10(step / scale)));
    return `${format(value / scale, Math.max(baseDigits, Math.min(6, neededDigits)))}${scale > 1 ? "万" : ""}`;
  }

  function drawChart(host, metric, range, options = {}) {
    const width = Math.max(140, Math.min(options.wide ? 980 : 450, host.clientWidth || (options.wide ? 980 : 350)));
    const height = options.wide ? Math.max(200, Math.min(265, width * .34)) : metric.researchId === "assets" ? 140 : 190;
    const pad = {left:metric.unit === "张" ? 46 : 47, right:15, top:14, bottom:29};
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const [start, end] = bounds(range);
    const rows = rangeRows(metric, range);
    const valid = rows.filter((row) => finite(row[1]));
    if (!valid.length) {
      host.innerHTML = '<div class="notice">当前图表区间没有有效观测；未以旧值或零填充。</div>';
      return;
    }
    const additions = options.series || [];
    const pool = [...valid.map((row) => row[1]), ...additions.flatMap((item) => item.rows.filter((row) => stamp(row[0]) >= start && stamp(row[0]) <= end && finite(row[1])).map((row) => row[1]))];
    let low = Math.min(...pool), high = Math.max(...pool);
    if (metric.kind === "bar" || options.zeroBaseline) { low = Math.min(0, low); high = Math.max(0, high); }
    if (options.fixedAxis) [low, high] = options.fixedAxis;
    else {
      const margin = (high - low || Math.abs(high) * .05 || 1) * .12;
      if (metric.kind === "bar") { if (low < 0) low -= margin; if (high > 0) high += margin; if (low === high) high = 1; }
      else { low -= margin; high += margin; }
    }
    const x = (iso) => pad.left + (stamp(iso) - start) / (end - start) * plotWidth;
    const y = (value) => pad.top + (high - value) / (high - low) * plotHeight;
    const dateText = (time) => new Date(time).toISOString().slice(range === "3m" ? 5 : 0, range === "3m" ? 10 : 7);
    const yTicks = [high, (high + low) / 2, low];
    let content = yTicks.map((value) => `<line x1="${pad.left}" y1="${y(value).toFixed(2)}" x2="${width - pad.right}" y2="${y(value).toFixed(2)}" class="chart-grid"/><text x="${pad.left - 7}" y="${(y(value) + 3).toFixed(2)}" text-anchor="end" class="chart-axis">${axisNumber(value, metric, (high - low) / 2)}</text>`).join("");
    if (low <= 0 && high >= 0) content += `<line x1="${pad.left}" y1="${y(0)}" x2="${width - pad.right}" y2="${y(0)}" class="chart-zero"/>`;
    const tickCount = width < 260 ? 2 : options.wide && width >= 600 ? 5 : 3;
    for (let i = 0; i < tickCount; i++) {
      const ratio = i / (tickCount - 1), time = start + (end - start) * ratio;
      const anchor = i === 0 ? "start" : i === tickCount - 1 ? "end" : "middle";
      content += `<text x="${pad.left + plotWidth * ratio}" y="${height - 8}" text-anchor="${anchor}" class="chart-axis">${dateText(time)}</text>`;
    }
    const color = options.color || "#377d72";
    function linePath(input, stroke, dash = "") {
      let path = "", previous = null;
      for (const row of input) {
        if (!finite(row[1])) { previous = null; continue; }
        const gap = previous && (stamp(row[0]) - stamp(previous[0])) / 86400000 > maxGap[metric.frequency];
        path += `${previous && !gap ? "L" : "M"}${x(row[0]).toFixed(2)},${y(row[1]).toFixed(2)} `;
        previous = row;
      }
      return `<path d="${path}" fill="none" stroke="${stroke}" stroke-width="${options.wide ? 1.9 : 2}" ${dash ? `stroke-dasharray="${dash}"` : ""} stroke-linecap="round" stroke-linejoin="round"/>`;
    }
    if (metric.kind === "bar") {
      const barWidth = Math.max(1.5, Math.min(options.wide ? 18 : 13, plotWidth * 22 * 86400000 / (end - start)));
      for (const row of valid) {
        const base = y(0), valueY = y(row[1]);
        content += `<rect x="${x(row[0]) - barWidth / 2}" y="${Math.min(base, valueY)}" width="${barWidth}" height="${Math.max(0, Math.abs(base - valueY))}" rx="1.4" fill="${row[1] < 0 ? "#ae7461" : color}"><title>${row[0]} · ${format(row[1], digits(metric))} ${escape(metric.unit)}</title></rect>`;
      }
    } else content += linePath(rows, color);
    for (const extra of additions) content += linePath(extra.rows.filter((row) => stamp(row[0]) >= start && stamp(row[0]) <= end), extra.color, extra.dash ?? "4 4");
    const latest = valid[valid.length - 1];
    if (metric.kind !== "bar") content += `<circle cx="${x(latest[0])}" cy="${y(latest[1])}" r="3" fill="${color}" stroke="#fffefa" stroke-width="1.5"/>`;
    content += `<line data-crosshair x1="${x(latest[0])}" x2="${x(latest[0])}" y1="${pad.top}" y2="${height - pad.bottom}" stroke="#9ea99f" stroke-dasharray="2 3" opacity="0"/><circle data-dot cx="${x(latest[0])}" cy="${y(latest[1])}" r="3" fill="${color}" opacity="0"/><rect data-hit x="${pad.left}" y="${pad.top}" width="${plotWidth}" height="${plotHeight}" fill="transparent"/>`;
    const partialHistory = stamp(valid[0][0]) - start > maxGap[metric.frequency] * 86400000;
    const coverage = partialHistory ? `<p class="tiny chart-coverage">本区间实际观测始于 ${valid[0][0]}</p>` : "";
    host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="${escape(options.ariaLabel || metric.name)}，${rangeName[range]}走势，单位${escape(metric.unit)}，方向未反转。可使用左右方向键查看观测。">${content}</svg><div class="chart-tooltip" aria-live="polite"></div>${coverage}`;
    const svg = host.querySelector("svg"), tooltip = host.querySelector(".chart-tooltip");
    const crosshair = host.querySelector("[data-crosshair]"), dot = host.querySelector("[data-dot]");
    let cursor = valid.length - 1;
    function show(index, active = true) {
      cursor = Math.max(0, Math.min(valid.length - 1, index));
      const row = valid[cursor], parts = [`${row[0]} · <strong>${options.label ? `${escape(options.label)} ` : ""}${format(row[1], digits(metric))} ${escape(metric.unit)}</strong>`];
      for (const extra of additions) {
        const pair = extra.rows.find((point) => point[0] === row[0]);
        if (pair && finite(pair[1])) parts.push(`${extra.label} ${format(pair[1])}${extra.unit ? ` ${escape(extra.unit)}` : ""}`);
      }
      tooltip.innerHTML = parts.join(" · ");
      crosshair.setAttribute("x1", x(row[0])); crosshair.setAttribute("x2", x(row[0]));
      crosshair.setAttribute("opacity", active ? "1" : "0");
      dot.setAttribute("cx", x(row[0])); dot.setAttribute("cy", y(row[1])); dot.setAttribute("opacity", active ? "1" : "0");
    }
    svg.addEventListener("pointermove", (event) => {
      const rect = svg.getBoundingClientRect(), svgX = (event.clientX - rect.left) / rect.width * width;
      const time = start + (svgX - pad.left) / plotWidth * (end - start);
      let best = 0, distance = Infinity;
      valid.forEach((row, index) => { const diff = Math.abs(stamp(row[0]) - time); if (diff < distance) { distance = diff; best = index; } });
      show(best);
    });
    svg.addEventListener("pointerleave", () => show(valid.length - 1, false));
    svg.addEventListener("focus", () => show(cursor));
    svg.addEventListener("blur", () => show(valid.length - 1, false));
    svg.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      show(event.key === "Home" ? 0 : event.key === "End" ? valid.length - 1 : cursor + (event.key === "ArrowLeft" ? -1 : 1));
    });
    show(valid.length - 1, false);
  }

  function renderGold() {
    const series = [...enabledMA].map((key) => ({rows:data.gold.averages[key], color:{ma20:"#6b9981",ma60:"#7d99a5",ma200:"#a99aa2"}[key], label:key.toUpperCase()}));
    drawChart($("gold-chart"), data.gold, goldRange, {wide:true, color:"#b88a36", series});
  }

  function freshness(metric) {
    if (!metric.date) return "缺失";
    return `${frequency[metric.frequency]} · 观测 ${metric.date} · 距今 ${metric.lag} 天${metric.quality === "very-stale" ? " · 严重滞后" : metric.quality === "stale" ? " · 滞后" : ""}`;
  }

  function growthChartOptions() {
    return {series:[{rows:data.liquidityComparison.goldRows, color:"#b88a36", label:"黄金同比", unit:"%", dash:""}], label:"M2 同比", zeroBaseline:true, ariaLabel:"黄金同比与固定汇率美中欧 M2 同比，同月、同一百分比纵轴"};
  }

  function renderCard(metric, range, groupNumber = null) {
    const deltaUnit = metric.unit === "%" ? "个百分点" : metric.unit;
    let change = finite(metric.delta) ? `<strong>${signed(metric.delta, digits(metric))} ${deltaUnit}</strong> · ${escape(metric.window)}` : metric.id === "official_reserves" ? "月度净增 · 以零为基准" : `${frequency[metric.frequency]} · ${metric.frequency === "monthly" && ["epu","gpr"].includes(metric.id) ? "完整月均值" : "指标自身水平"}`;
    if (metric.changeText) change = escape(metric.changeText);
    let extra = "";
    if (metric.id === "official_reserves") extra = `储备总量 ${format(metricMap.china_stock.value)} 吨`;
    if (metric.id === "cot") extra = `当前净多在近 156 个周度观测中的分位 ${format(percent(data.cotPercentile), 1)}%`;
    const warning = ["stale", "very-stale", "missing"].includes(metric.quality) ? " quality-warning" : "";
    const excluded = metric.state !== "context" && ["missing", "very-stale"].includes(metric.quality);
    const badge = excluded ? "历史方向 · 未计入" : metric.researchId === "assets" ? "走势参考" : metric.researchId === "liquidity" ? "研究背景" : stateName[metric.state] || "未计入";
    const category = groupNumber ? `${String(groupNumber).padStart(2, "0")} · ${metric.category} · 独立计 1 组` : ["etf", "cot"].includes(metric.id) ? `组内指标 · ${metric.category}` : metric.category;
    const comparison = metric.id === "liquidity_yoy";
    if (comparison) change = `共同统计期 ${data.liquidityComparison.date.slice(0,7)} · 相对 12 个月前`;
    const heading = comparison ? "黄金与 M2 · 同比增速" : metric.name;
    const value = comparison ? `<div class="growth-values"><div class="growth-gold"><small>黄金同比</small><strong>${signed(data.liquidityComparison.goldValue)}<span>%</span></strong></div><div class="growth-money"><small>固定汇率 M2 同比</small><strong>${signed(metric.value)}<span>%</span></strong></div></div>` : `<p class="metric-value">${metric.kind === "bar" ? signed(metric.value) : format(metric.value, digits(metric))}<span>${escape(metric.unit)}</span></p>`;
    const legend = comparison ? '<div class="growth-legend"><span class="growth-gold">黄金同比</span><span class="growth-money">固定汇率 M2 同比</span><small>同一纵轴 · 零线参照</small></div>' : "";
    const cardNote = comparison ? "本月相对十二个月前的变化。M2 采用固定汇率；两条线按共同统计月对齐，详细口径见研究。" : metric.note;
    return `<article class="metric-card" data-metric-id="${metric.id}"><div class="metric-top"><div><p class="metric-category">${escape(category)}</p><h3>${escape(heading)}</h3></div><span class="badge ${excluded ? "missing" : metric.state}">${badge}</span></div>${value}<p class="metric-change">${change}</p><p class="metric-extra">${extra || "&nbsp;"}</p><div class="chart-host metric-chart" id="chart-${metric.id}"></div>${legend}<p class="metric-note">${escape(cardNote)}</p><div class="metric-foot"><div><p class="${warning}">${freshness(metric)}</p><p>${escape(metric.source)}</p></div><a href="#explorer" data-explore="${metric.id}" aria-label="研究${escape(metric.name)}">展开研究 ↗</a></div></article>`;
  }

  function renderGroup(host, ids, range) {
    if (host.id === "core-grid") {
      const group = data.environment.groups.positioning_technical;
      const labels = {supportive:"支持", headwind:"压力", neutral:"中性", missing:"未计入"};
      const subLabel = (id) => labels[["missing", "very-stale"].includes(metricMap[id].quality) ? "missing" : metricMap[id].state];
      host.innerHTML = ids.slice(0,4).map((id, index) => renderCard(metricMap[id], range, index + 1)).join("") + `<div class="positioning-group"><div class="positioning-heading"><div><p class="metric-category">05 · 资金与仓位 · 两张图合为 1 组</p><h3>ETF 与 CFTC</h3></div><span class="badge ${group.state}">本组${labels[group.state]}</span></div><p class="positioning-summary">ETF：${subLabel("etf")} · CFTC：${subLabel("cot")} → 合成${labels[group.state]}。上方只计这一组。</p><div class="positioning-cards">${ids.slice(4).map((id) => renderCard(metricMap[id], range)).join("")}</div></div>`;
    } else host.innerHTML = ids.map((id) => renderCard(metricMap[id], range)).join("");
    ids.forEach((id) => drawChart($("chart-" + id), metricMap[id], range, id === "liquidity_yoy" ? growthChartOptions() : {}));
    host.querySelectorAll("[data-explore]").forEach((link) => link.addEventListener("click", (event) => {
      event.preventDefault(); returnTarget = link.dataset.explore; openExplorer(link.dataset.explore);
    }));
  }

  function openExplorer(id) {
    selected = id; activeView = metricMap[id].researchId ? "research" : "self"; relationHorizon = "medium";
    $("explorer").hidden = false;
    $("metric-select").value = selected;
    renderExplorer();
    $("explorer").scrollIntoView({behavior:"smooth", block:"start"});
    $(`view-${activeView}`).focus({preventScroll:true});
  }

  function researchTable(headers, rows) {
    return `<div class="research-table"><table><thead><tr>${headers.map((v) => `<th>${escape(v)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((v) => `<td>${escape(v)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function renderResearch(metric) {
    if (metric.researchId === "liquidity") {
      const research = data.research.liquidity, sources = data.research.liquiditySources, latest = sources.latest;
      const gold = research.results.gold;
      const yoy = data.research.yoyStudy.overlapping;
      const yoyRows = [data.research.yoyStudy.overlapping, data.research.yoyStudy.annual].map((r,index) => [index === 0 ? "每月同比 · 重叠" : "每年十二月 · 非重叠", `${r.start}—${r.end}`,r.n,format(r.usd.correlation),format(r.fixed.correlation),format(r.fixed.partial_dollar)]);
      const yoyBlock = `<h3 class="research-subtitle">主图同比对照的复核</h3>${researchTable(["方法","样本区间","样本数","当期汇率","固定汇率","固定＋控美元"],yoyRows)}<p class="tiny">算术同比（%）＝（本月值 ÷ 十二个月前值 − 1）× 100。相邻同比共享十一段月变化，${yoy.n} 个同比点不能当成 ${yoy.n} 个独立观察；每年十二月的对照不重叠，但只有 ${data.research.yoyStudy.annual.n} 个年度样本。以下另用非重叠月度变化检验，方法与同比图区分开标注。</p>`;
      const interval = gold.fixed.interval;
      const uncertainty = interval ? `非重叠月度偏相关的 95% 分块重采样区间 ${format(interval[0])} 至 ${format(interval[1])}` : "样本不足以估计区间";
      const rows = Object.values(research.results).filter((r) => r.fixed.n).map((r) => [r.name, `${r.fixed.start}—${r.fixed.end}`, r.fixed.n, format(r.usd.correlation), format(r.fixed.correlation), format(r.fixed.partial_dollar), r.fixed.interval ? `${format(r.fixed.interval[0])} 至 ${format(r.fixed.interval[1])}` : "—"]);
      const lagRows = research.lags.gold.map((r) => [r.lag === 0 ? "同期" : `货币领先 ${r.lag} 个月`, `${r.fixed.start}—${r.fixed.end}`, r.fixed.n, format(r.usd.correlation), format(r.fixed.correlation), format(r.fixed.partial_dollar)]);
      const periodRows = research.periods.gold.map((r) => [r.label, r.fixed.n, format(r.usd.correlation), format(r.fixed.correlation), format(r.fixed.partial_dollar)]);
      const old = data.research.oldValuation;
      return `<div class="research-lead"><p class="overline">先看结论</p><h3>美元折算后的关联，不能直接当作独立货币信号。</h3><p>主图的黄金与 M2 同比相关：当期汇率 <strong>${format(yoy.usd.correlation)}</strong> → 固定汇率 <strong>${format(yoy.fixed.correlation)}</strong>；固定汇率再控制美元后 <strong>${format(yoy.fixed.partial_dollar)}</strong>。另用非重叠月度变化复核：固定汇率相关 ${format(gold.fixed.correlation)}，控制美元后的偏相关 ${format(gold.fixed.partial_dollar)}；${uncertainty}。</p><p>这是本样本的描述性结果，不排除其他流动性渠道；本指标暂不加入驱动评分。</p></div><div class="research-columns"><div><h3>这里的 M2 是什么</h3><p>美国、中国、欧元区各自公布的 M2：现金、存款及部分货币工具的余额。各地定义不同，这里是三大经济体研究组合，<strong>不是全球总量</strong>。</p><p>不加入央行资产负债表，不扣 TGA 或逆回购，不加任意权重。美国为月均，中欧为月末；均使用非季调数据。</p></div><div><h3>怎样换成美元</h3><p>当期口径＝美国 M2＋中国 M2 × 美元/人民币＋欧元区 M2 × 美元/欧元。固定口径把两种汇率固定在 2020-12。</p><p>${latest.month} 当期规模 <strong>${format(latest.total_usd_bn / 1000)} 万亿美元</strong>，同比 ${signed(latest.m2_current_fx_yoy_pct)}%；精确拆为本币余额 ${signed(latest.local_contribution_pct)} 个百分点、汇率折算 ${signed(latest.fx_contribution_pct)} 个百分点。</p></div></div>${yoyBlock}<h3 class="research-subtitle">货币增长与资产月收益</h3><p class="tiny">使用非重叠月度对数变化；不同资产起始历史分别标示。控制美元后的数字是偏相关，不能据此认定因果。</p>${researchTable(["资产","样本区间","月份数","当期汇率","固定汇率","固定＋控美元","偏相关 95% 区间"], rows)}<p class="tiny">美国月均余额与中欧月末余额按月份近似对齐；区间采用 6 个月循环分块重采样、600 次。非季调敏感性：去除全样本的日历月份均值（非官方季调），黄金偏相关为 ${format(gold.fixed.partial_dollar_calendar)}。统计区间固定，图区选择不重算本表。</p><details class="research-more"><summary>领先关系与分段稳健性</summary><p class="tiny">事先选定 0、1、3、6 个月，不挑最吻合时滞。各时滞使用相同的目标月份样本。当前修订版数据未按历史披露日回放，因此这些统计领先不等于可交易预测。</p>${researchTable(["关系","样本区间","月份数","当期汇率","固定汇率","固定＋控美元"],lagRows)}${researchTable(["黄金样本段","月份数","当期汇率","固定汇率","固定＋控美元"],periodRows)}</details><details class="research-more"><summary>旧“金价 / M2 分位”为何停用</summary><p>原表分子为${escape(old.numerator)}，分母为${escape(old.denominator)}。2024-01 之前分母只使用美国 M2，之后改成五地加权组合；旧历史分位混合了两种口径。</p><p>${escape(old.unitConcern)}。原始记录保留在研究底稿，预览不再显示该历史分位。</p><p>当前比值图使用全程统一的美中欧 M2 与 2020-12 基期。它含金价自身，不用与同期金价的相关自证预测能力，也不称公允价值。</p></details><details class="research-more"><summary>概念边界与开源实现核查</summary><p>M2 是银行体系的货币余额；央行资产表是央行持有的资产；全球融资条件还涉及信用、市场流动性与融资价格。三者不能直接混加。</p><p>一个开源脚本把当期汇率加总后的曲线人为移位。另一个仓库披露：所用 FRED 中国 M2 序列止于 2019-08，近期合成只含原五项中的四项，另一组合还混合 M2 与央行资产。这是所用数据源的覆盖限制，不能理解成中国官方停止公布 M2。这里保留汇率拆解与完整共同月份，不以移位后的价格图证明预测能力。</p><p class="tiny"><a href="https://www.bis.org/publications/global-liquidity-indicators-background-and-interpretation" target="_blank" rel="noopener">BIS 全球流动性定义</a> · <a href="https://github.com/adhamajid/Tradeview_Code/blob/main/M2_Global_Liquidity_Index.pine" target="_blank" rel="noopener">开源美元加总与曲线移位示例</a> · <a href="https://github.com/iansummerlin/global-liquidity-analysis" target="_blank" rel="noopener">开源数据覆盖与混合口径示例</a></p></details>`;
    }
    const study = data.research.assetStudy;
    const pairs = ["gold_silver","gold_bitcoin"];
    const rows = pairs.map((key) => {const p = study[key].weekly.fixed; return [key === "gold_silver" ? "黄金 / 白银" : "黄金 / 比特币", format(p["1y"].pearson), format(p["3y"].pearson), format(p["5y"].pearson), format(p["10y"].pearson), p["1y"].n];});
    const silver = study.gold_silver.weekly.fixed["1y"], btc = study.gold_bitcoin.weekly.fixed["1y"];
    return `<div class="research-lead"><p class="overline">走势参考，不参与黄金评分</p><h3>白银联动较强，比特币联动较弱且随阶段变化。</h3><p>近一年周度收益相关：黄金与白银 <strong>${format(silver.pearson)}</strong>，黄金与比特币 <strong>${format(btc.pearson)}</strong>。两者均为 ${silver.n} 个样本，截至 ${silver.end}。</p></div><h3 class="research-subtitle">与黄金的周度收益相关</h3>${researchTable(["配对","1 年","3 年","5 年","10 年","1 年样本数"],rows)}<p class="tiny">共同周五价格，缺失时使用共同周四；非重叠对数收益，不比较价格水平。黄金与白银收盘报价的精确钟点尚未核实，比特币为 5 PM PST，同日标签不保证同刻。统计表使用固定窗口，图区选择不重算。</p><div class="research-columns"><div><h3>金银比怎样看</h3><p>同日、同单位的黄金价格除以白银价格；上行表示黄金相对白银更强。两种资产的供需结构不同，不能把偏离某个均值直接解释成套利机会。</p></div><div><h3>铜为什么只看走势</h3><p>本页使用 IMF / FRED 的铜月度均价，非日度报价。它不加入这张周度相关表，也不添加黄金驱动票数。</p><p>白银、铜、比特币均保留自己的最新观测日；金银比只用金银共同日期，不用新银价配旧金价。</p></div></div><details class="research-more"><summary>阶段变化与报价日切敏感性</summary>${researchTable(["年份","黄金 / 白银","黄金 / 比特币"], Object.keys(study.gold_silver.annual_weekly).map((year) => [year,format(study.gold_silver.annual_weekly[year].pearson),format(study.gold_bitcoin.annual_weekly[year]?.pearson)]))}<p class="tiny">BTC 日度配对收益相关在前后移一个共同观测时分别为 ${format(study.gold_bitcoin.daily_lag_sensitivity["-1"].pearson)} / ${format(study.gold_bitcoin.daily_lag_sensitivity["1"].pearson)}，同期为 ${format(study.gold_bitcoin.daily_lag_sensitivity["0"].pearson)}；仅作日切敏感性，不能解释为预测因果。</p></details>`;
  }

  function setView(view) {
    activeView = view;
    $("detail-tabs").querySelectorAll("[data-view]").forEach((button) => {
      const active = button.dataset.view === view;
      button.setAttribute("aria-selected", String(active)); button.tabIndex = active ? 0 : -1;
    });
    $("view-panel").setAttribute("aria-labelledby", `view-${view}`);
  }

  function renderExplorer() {
    const metric = metricMap[selected];
    $("view-research").hidden = !metric.researchId;
    if (activeView === "research" && !metric.researchId) activeView = "self";
    $("explorer-title").textContent = metric.name;
    setView(activeView);
    $("detail-range").hidden = activeView === "research";
    const period = rangeRows(metric, detailRange).filter((row) => finite(row[1]));
    const coverage = period.length ? `${period[0][0]} — ${period[period.length - 1][0]} · ${period.length} 个有效观测` : "当前区间无有效观测";
    if (activeView === "research") {
      $("view-panel").innerHTML = renderResearch(metric);
    } else if (activeView === "self") {
      $("view-panel").innerHTML = `<p class="detail-description">${escape(metric.note)}</p><div class="detail-kpis"><span>最新水平 <strong>${format(metric.value, digits(metric))} ${escape(metric.unit)}</strong></span><span>观测日 <strong>${metric.date || "—"}</strong></span></div><div class="detail-chart-heading"><span>${escape(metric.name)} · ${escape(metric.unit)}</span><span class="tiny">自身纵轴 · 真实方向</span></div><div class="chart-host detail-chart" id="detail-self"></div><p class="tiny">${coverage}。各区间按真实日历日期显示。</p>`;
      drawChart($("detail-self"), metric, detailRange, {wide:true});
    } else if (activeView === "compare") {
      if (selected === "liquidity_yoy") {
        $("view-panel").innerHTML = '<p class="detail-description">黄金与固定汇率美中欧 M2 的算术同比（%）＝（本月值 ÷ 十二个月前值 − 1）× 100。共同月份、同一百分比纵轴，以零线为参照；没有平移曲线或使用双轴。</p><div class="chart-host detail-chart" id="detail-yoy"></div><div class="growth-legend"><span class="growth-gold">黄金同比</span><span class="growth-money">固定汇率 M2 同比</span></div>';
        drawChart($("detail-yoy"), metric, detailRange, {...growthChartOptions(), wide:true});
      } else {
      $("view-panel").innerHTML = `<p class="detail-description">上下图使用同一个日历区间，纵轴分别保留指标和黄金的原始单位。分别使用各自观测，未插值填补；同向或反向变化仅供观察。</p><div class="detail-chart-heading"><span>${escape(metric.name)} · ${escape(metric.unit)}</span><span class="tiny">${frequency[metric.frequency]} · 轴未反转</span></div><div class="chart-host" id="detail-factor"></div><div class="detail-chart-heading"><span>黄金现货 · 美元 / 盎司</span><span class="tiny">日频 · 独立纵轴</span></div><div class="chart-host" id="detail-gold"></div>`;
      drawChart($("detail-factor"), metric, detailRange, {wide:true});
      drawChart($("detail-gold"), data.gold, detailRange, {wide:true, color:"#b88a36"});
      }
    } else {
      const relation = relationHorizon === "short" && metric.relation?.short ? metric.relation.short : metric.relation;
      if (!relation) $("view-panel").innerHTML = `<div class="notice">${selected === "liquidity_yoy" ? "同比观察按共同月份对齐。相邻同比存在重叠，当前未增设滚动相关；固定样本的同比与年度检验见「研究结论」。" : selected === "gold_liquidity" ? "此比值含有金价自身，未用与同期金价的相关自证有效性。预测检验需要未来收益，当前尚未开展。" : selected === "gold_silver_ratio" ? "金银比含金价自身，不用与同期黄金的相关证明预测能力。可查看自身走势和关联资产研究。" : selected === "copper" ? "铜使用月度均价，未与黄金日收盘混算周度相关。可查看自身走势及原始单位对照。" : `当前没有这一口径的独立关系图。可查看自身历史与黄金对照${metric.researchId ? "，研究结论另列" : ""}；未借用其他口径的相关系数。`}</div>`;
      else {
        const descriptor = !finite(relation.value) ? "样本不足" : Math.abs(relation.value) < .1 ? "接近零" : relation.value < 0 ? "负向相关" : "正向相关";
        const horizonControl = metric.relation.short ? `<label class="tiny">检验期限 <select id="relation-horizon" aria-label="关系检验期限"><option value="medium" ${relationHorizon === "medium" ? "selected" : ""}>中期</option><option value="short" ${relationHorizon === "short" ? "selected" : ""}>短期</option></select></label>` : "";
        $("view-panel").innerHTML = `<div class="detail-chart-heading"><h3>描述性滚动相关</h3>${horizonControl}</div><p class="relation-stat">${format(relation.value)}<span>${descriptor} · ${selected === "price_trend" ? "成熟信号" : "检验数据"}截至 ${relation.date || "—"}</span></p><p class="detail-description">${escape(relation.window)}。纵轴固定为 −1 至 +1。</p>${selected === "cot" ? '<p class="notice">现有检验使用 21 个周度配对观测的变化，与首页近 4 个周度观测的方向判断不同。这里据实保留原计算，尚未调整研究窗口。</p>' : selected === "price_trend" ? '<p class="notice">未来收益需要等待完整观察期，因此成熟信号日期早于最新金价；末尾尚未成熟的信号不补零、不计入相关。</p>' : ""}<div class="chart-host detail-chart" id="detail-relation"></div><p class="tiny">相关正负描述历史样本中的同向或反向变化，不等同于因果、统计显著性或预测能力；重叠样本会影响统计推断。</p>`;
        const corrMetric = {name:`${metric.name}滚动相关`, unit:"相关系数", kind:"line", frequency:relation.frequency || metric.frequency, rows:relation.rows};
        drawChart($("detail-relation"), corrMetric, detailRange, {wide:true, fixedAxis:[-1,1], color:"#64808b"});
        if ($("relation-horizon")) $("relation-horizon").addEventListener("change", () => {relationHorizon = $("relation-horizon").value; renderExplorer();});
      }
    }
    const sources = metric.sourceUrl ? `<a href="${escape(metric.sourceUrl)}" target="_blank" rel="noopener">${escape(metric.source)}</a>` : escape(metric.source);
    const goldGrowth = selected === "liquidity_yoy" ? Object.fromEntries(data.liquidityComparison.goldRows) : null;
    const table = rangeRows(metric, detailRange).map((row) => `<tr><td>${row[0]}</td><td>${finite(row[1]) ? format(row[1], digits(metric)) : "缺失"}</td>${goldGrowth ? `<td>${finite(goldGrowth[row[0]]) ? format(goldGrowth[row[0]]) : "缺失"}</td>` : ""}<td>${escape(metric.unit)}</td></tr>`).join("");
    const specific = metric.researchId === "liquidity" ? "美国 M2NS（十亿美元、月均）、中国 M0001384（亿元转十亿元、月末）、ECB M2（百万欧元转十亿欧元、月末），全部非季调。汇率采用 Fed H.10 各月最后有效观测；固定汇率基月 2020-12。数据为当前修订版，各国定义与历史统计范围不完全一致。" : metric.researchId === "assets" ? metric.note : selected === "etf" ? "合计要求 SPDR 与 iShares 在同一观测日都有有效持仓，单只缺失时不计算合计。" : selected === "official_reserves" || selected === "china_stock" ? "中国储备 2025-12 起使用 SAFE 手工权威序列；更早历史沿用已保存工作簿。本地最新 2026-09 数据来源记录为 2026-10-07 披露。" : selected === "global_stock" ? "历史工作簿与 Wind 补充序列合并，全球观测不借用中国的日期。" : ["epu", "gpr"].includes(selected) ? "完整日历月均值，历史工作簿与 Wind 补充序列合并；不显示未完成月份。" : "使用项目已保存的有效观测。";
    $("source-content").innerHTML = `<div class="source-meta"><p>来源：${sources}。${freshness(metric)}。</p><p>${escape(specific)}</p><p>定义：${escape(metric.note)}${metric.since && finite(metric.delta) ? ` 近期变化比较 ${metric.since} 与 ${metric.date}，${escape(metric.window)}。` : ""}</p><p>本地输入：<code>${escape(metric.file)}</code> · 原始字段 <code>${escape(metric.sourceKey || metric.key)}</code>${metric.transform ? `。展示变换：${escape(metric.transform)}；导出字段 <code>${escape(metric.key)}</code>` : ""}。明细显示最多近 10 年已保存历史，当前明细覆盖 ${coverage}。</p></div><button class="text-button" id="export-series">导出当前指标区间 CSV ↓</button><div class="data-scroll"><table><thead><tr><th>观测日期</th><th>指标值</th><th>单位</th></tr></thead><tbody>${table || '<tr><td colspan="3">当前区间无记录</td></tr>'}</tbody></table></div>`;
    if (goldGrowth) {
      $("source-content").querySelector("thead").innerHTML = '<tr><th>共同统计期</th><th>固定汇率 M2 同比</th><th>黄金同比</th><th>单位</th></tr>';
      $("export-series").textContent = "导出当前区间同比对照 CSV ↓";
      $("source-content").querySelector('.source-meta').insertAdjacentHTML('beforeend', `<p>黄金同比原始字段：<code>gold_price</code>（<code>data/market/wind_daily.csv</code>）；月底七天内最后有效报价，同比公式相同，导出字段 <code>gold_yoy_pct</code>。只使用共同完整统计月。</p>`);
    }
    $("export-series").addEventListener("click", () => {
      const output = goldGrowth ? [["date", metric.key, "gold_yoy_pct", "unit"], ...rangeRows(metric, detailRange).map((row) => [row[0], finite(row[1]) ? row[1] : "", finite(goldGrowth[row[0]]) ? goldGrowth[row[0]] : "", metric.unit])] : [["date", metric.key, "unit"], ...rangeRows(metric, detailRange).map((row) => [row[0], finite(row[1]) ? row[1] : "", metric.unit])];
      const csv = "\uFEFF" + output.map((row) => row.map((cell) => `"${String(cell).replaceAll('"','""')}"`).join(",")).join("\r\n");
      const url = URL.createObjectURL(new Blob([csv], {type:"text/csv;charset=utf-8"}));
      const link = document.createElement("a"); link.href = url; link.download = `${data.snapshotDate}_${metric.name}_${detailRange}.csv`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }

  function bindRanges(id, callback) {
    $(id).querySelectorAll("[data-range]").forEach((button) => button.addEventListener("click", () => {
      $(id).querySelectorAll("[data-range]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      callback(button.dataset.range);
    }));
  }

  $("as-of").textContent = `黄金观测截至 ${data.asOf} · 本地快照 · 各指标与参考资产日期分别标注`;
  $("gold-value").textContent = format(data.gold.value);
  $("gold-return").textContent = `近 5 个有效观测 ${signed(percent(data.gold.latest.return_5d), 1)}% · 观测 ${data.gold.date}`;
  $("environment-value").textContent = data.environment.label;
  $("environment-counts").innerHTML = `<span><b>${data.environment.headwind}</b> 组压力</span><span><b>${data.environment.supportive}</b> 组支持</span><span><b>${data.environment.neutral}</b> 组中性</span>`;
  $("core-counts").textContent = "下方对应上方的 5 组驱动；前四张卡片各计 1 组，框内的 ETF 与 CFTC 合成后计 1 组。";
  $("technical-title").textContent = `${data.gold.technical.medium_term} · ${data.gold.technical.short_term}`;
  $("technical-alignment").textContent = data.gold.technical.alignment;
  $("ma-values").innerHTML = ["ma20", "ma60", "ma200"].map((key) => `<div><dt>${key.toUpperCase()}</dt><dd>${format(data.gold.latest[key])} <span class="tiny">${signed(percent(data.gold.latest[`gap_${key}`]), 1)}%</span></dd></div>`).join("");
  $("technical-trigger").textContent = `判断改变条件：${data.gold.technical.trigger}`;
  $("changes").innerHTML = data.changes.map((item) => `<article class="${item.tone}"><small>${escape(item.label)}</small><h3>${escape(item.headline.replace("中国央行购金加速", "中国储备净增扩大"))}</h3><p>${escape(item.detail.replaceAll("pct", " 个百分点"))}</p></article>`).join("");
  $("metric-select").innerHTML = data.metrics.map((metric) => `<option value="${metric.id}">${escape(metric.name)}</option>`).join("");
  $("metric-select").addEventListener("change", () => {selected = $("metric-select").value; relationHorizon = "medium"; renderExplorer();});
  $("trend-research").addEventListener("click", (event) => {event.preventDefault(); returnTarget = "trend-research"; openExplorer("price_trend");});
  $("close-explorer").addEventListener("click", () => {
    $("explorer").hidden = true;
    const target = returnTarget === "trend-research" ? $("trend-research") : document.querySelector(`[data-explore="${returnTarget || selected}"]`);
    if (target) target.focus();
  });
  $("detail-tabs").querySelectorAll("[data-view]").forEach((button, index, buttons) => {
    button.addEventListener("click", () => {activeView = button.dataset.view; renderExplorer();});
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const visible = [...buttons].filter((item) => !item.hidden);
      const current = visible.indexOf(button);
      const next = event.key === "Home" ? 0 : event.key === "End" ? visible.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + visible.length) % visible.length;
      visible[next].click(); visible[next].focus();
    });
  });
  bindRanges("gold-range", (range) => {goldRange = range; renderGold();});
  bindRanges("driver-range", (range) => {coreRange = range; renderGroup($("core-grid"), coreIds, coreRange);});
  bindRanges("context-range", (range) => {contextRange = range; renderGroup($("context-grid"), ["gvz","epu","gpr"], contextRange);});
  bindRanges("reserve-range", (range) => {reserveRange = range; renderGroup($("reserve-grid"), ["china_stock","global_stock"], reserveRange);});
  bindRanges("liquidity-range", (range) => {liquidityRange = range; renderGroup($("liquidity-grid"), ["liquidity_yoy","gold_liquidity"], liquidityRange);});
  bindRanges("reference-range", (range) => {referenceRange = range; renderGroup($("reference-grid"), ["silver","gold_silver_ratio","copper","bitcoin"], referenceRange);});
  bindRanges("detail-range", (range) => {detailRange = range; renderExplorer();});
  $("ma-controls").querySelectorAll("[data-ma]").forEach((button) => button.addEventListener("click", () => {
    const key = button.dataset.ma; enabledMA.has(key) ? enabledMA.delete(key) : enabledMA.add(key);
    button.setAttribute("aria-pressed", String(enabledMA.has(key))); renderGold();
  }));
  const moneyLatest = data.research.liquiditySources.latest;
  $("liquidity-summary").textContent = `统计期 ${moneyLatest.month}；美元折算总额 ${format(moneyLatest.total_usd_bn/1000)} 万亿美元。主图按共同月份比较黄金与固定汇率 M2 的同比；余额移入研究详情，比值以 2020-12＝100，详细口径见「展开研究」。`;
  const referenceSilver = data.research.assetStudy.gold_silver.weekly.fixed["1y"], referenceBTC = data.research.assetStudy.gold_bitcoin.weekly.fixed["1y"];
  $("reference-summary").textContent = `近一年周度收益与黄金相关：白银 ${format(referenceSilver.pearson)} · 比特币 ${format(referenceBTC.pearson)}（${referenceSilver.n} 个非重叠样本，截至 ${referenceSilver.end}）。金银比仅用共同日期；铜为月频，不混入周度相关。`;
  $("method-rules").innerHTML = data.rules.map((rule) => `<li>${escape(rule)}</li>`).join("");
  $("footer").textContent = `设计整理：2026-10-08 · 本地预览生成 ${data.builtAt} · 黄金驱动规则沿用现有项目；新货币与关联资产仅作研究背景，各自观测日期分别标示。`;
  renderGold();
  renderGroup($("core-grid"), coreIds, coreRange);
  renderGroup($("context-grid"), ["gvz","epu","gpr"], contextRange);
  renderGroup($("reserve-grid"), ["china_stock","global_stock"], reserveRange);
  renderGroup($("liquidity-grid"), ["liquidity_yoy","gold_liquidity"], liquidityRange);
  renderGroup($("reference-grid"), ["silver","gold_silver_ratio","copper","bitcoin"], referenceRange);
  let resizeFrame;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      renderGold();
      renderGroup($("core-grid"), coreIds, coreRange);
      renderGroup($("context-grid"), ["gvz","epu","gpr"], contextRange);
      renderGroup($("reserve-grid"), ["china_stock","global_stock"], reserveRange);
      renderGroup($("liquidity-grid"), ["liquidity_yoy","gold_liquidity"], liquidityRange);
      renderGroup($("reference-grid"), ["silver","gold_silver_ratio","copper","bitcoin"], referenceRange);
      if (!$("explorer").hidden) renderExplorer();
    });
  });
})();
