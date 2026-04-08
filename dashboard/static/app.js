/* ── QuantDesk — Dashboard Application ───────────────────────────────────── */

const REFRESH_MS = 30_000;
const PRICE_MS   = 1_000;

let _prevBtcPrice = null;
let _scoreHistory = [];   // [{score, ts}]
let _sparkTf      = '1m';
let _liqLevels    = [];   // liquidation heatmap levels
let _liqApiError  = null; // última mensagem de erro da API de liquidações
let _liqImgUrl    = null; // URL da screenshot Apify (fallback quando sem dados estruturados)

// TradingView Lightweight Charts — estado global
let _tvChart        = null;
let _tvCandleSeries = null;
let _tvVolSeries    = null;
let _tvChartCont    = null;   // referência ao container DOM atual
let _tvPriceLines   = [];     // referências às price lines ativas
let _chartLevels    = {};     // {entry, stop, tp1, tp2, tp3} do card BTC

// Responsive resize — atualiza largura do chart quando janela muda
window.addEventListener('resize', () => {
  if (!_tvChart) return;
  const el = document.getElementById('sparklineChart');
  if (el) _tvChart.applyOptions({ width: el.clientWidth });
});

// ── Helpers ───────────────────────────────────────────────────────────────

function scoreColor(s) {
  if (s >= 75) return 'var(--green)';
  if (s >= 60) return 'var(--yellow)';
  if (s >= 50) return 'var(--text-muted)';
  return 'var(--red)';
}

function scoreLabel(s) {
  if (s >= 90) return 'Long Muito Forte 🔥';
  if (s >= 75) return 'Long Forte';
  if (s >= 60) return 'Long Moderado';
  if (s >= 40) return 'Aguardando';
  return 'Short Favorável';
}

function marketRegime(score, direction) {
  if (score >= 65 || direction === 'LONG')  return { label: 'Bull Market', cls: 'regime-bull', icon: '🟢' };
  if (score <= 35 || direction === 'SHORT') return { label: 'Bear Market', cls: 'regime-bear', icon: '🔴' };
  return { label: 'Sideways', cls: 'regime-side', icon: '🟡' };
}

function volatilityLabel(atrPct, squeeze) {
  if (atrPct >= 3 || squeeze) return { label: 'Alta 🔥',  color: 'var(--red)' };
  if (atrPct >= 1)            return { label: 'Média ⚡', color: 'var(--yellow)' };
  return                             { label: 'Baixa 💤', color: 'var(--text-muted)' };
}

function probabilities(score) {
  return { long: Math.round(score), short: 100 - Math.round(score) };
}

function scoreDelta() {
  if (_scoreHistory.length < 2) return null;
  return _scoreHistory[_scoreHistory.length - 1].score - _scoreHistory[0].score;
}

function fmtPrice(p) {
  if (p == null) return '—';
  return '$' + Number(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v, decimals = 1) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + Number(v).toFixed(decimals) + '%';
}

function fmtDateTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
       + ' ' + d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

function timeAgo(iso) {
  if (!iso) return '';
  const s = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (s < 60)   return `${s}s atrás`;
  if (s < 3600) return `${Math.floor(s / 60)}min atrás`;
  return `${Math.floor(s / 3600)}h atrás`;
}

function dataAgeSeconds(iso) {
  if (!iso) return Infinity;
  return Math.floor((Date.now() - new Date(iso)) / 1000);
}

// Sinal expirado: entry muito distante do preço atual (>1.5%) ou dados > 2h
function isSignalStale(entry, currentPrice, lastUpdated) {
  const ageS = dataAgeSeconds(lastUpdated);
  if (ageS > 7200) return true;   // > 2 horas
  if (!entry || !currentPrice) return false;
  return Math.abs(entry - currentPrice) / currentPrice > 0.015;
}

function statusBadge(dir, score) {
  if (!dir || dir === 'AGUARDANDO') {
    return `<div class="status-main status-wait"><span>●</span> NEUTRO</div>`;
  }
  if (dir.includes('LONG')) {
    const lbl = score >= 75 ? 'LONG FORTE' : 'LONG FAVORÁVEL';
    return `<div class="status-main status-long"><span>▲</span> ${lbl}</div>`;
  }
  if (dir.includes('SHORT')) {
    const lbl = score <= 30 ? 'SHORT FORTE' : 'SHORT FAVORÁVEL';
    return `<div class="status-main status-short"><span>▼</span> ${lbl}</div>`;
  }
  return `<div class="status-main status-wait"><span>●</span> AGUARDANDO</div>`;
}

function biasClass(bias) {
  if (!bias) return 'c-muted';
  if (bias === 'BULLISH' || bias.includes('LONG'))  return 'c-green';
  if (bias === 'BEARISH' || bias.includes('SHORT')) return 'c-red';
  return 'c-muted';
}

function dotBias(val) {
  if (!val) return 'neutral';
  const v = String(val).toUpperCase();
  if (v.includes('BULL') || v.includes('LONG') || v === 'ALTA') return 'bull';
  if (v.includes('BEAR') || v.includes('SHORT'))                return 'bear';
  return 'neutral';
}

function confluenceDots(btc) {
  const items = [
    { label: 'Structure',   val: btc.market_structure },
    { label: 'Liquidity',   val: (btc.sweep_low || btc.sweep_high) ? 'BULLISH' : 'NEUTRO' },
    { label: 'Volume',      val: (btc.high_volume && btc.cvd_trending_up) ? 'BULLISH' : btc.high_volume ? 'NEUTRO' : 'BEARISH' },
    { label: 'Trend',       val: btc.ema_signal },
    { label: 'Correlation', val: btc.correlation_bias },
    { label: 'Volatility',  val: (btc.atr_pct || 0) > 2 ? 'ALTA' : 'NORMAL' },
  ];
  return items.map(item => {
    const bias  = dotBias(item.val);
    const color = bias === 'bull' ? 'var(--green)' : bias === 'bear' ? 'var(--red)' : 'var(--yellow)';
    return `<div class="conf-item">
      <span class="conf-dot" style="background:${color};box-shadow:0 0 6px ${color}55"></span>
      <span class="conf-label">${item.label}</span>
    </div>`;
  }).join('');
}

function liquidityZones(btc) {
  const price = btc.price || 0;
  const levels = [
    { label: 'TP 3',  val: btc.tp3,   color: 'var(--green)' },
    { label: 'TP 2',  val: btc.tp2,   color: 'var(--green)' },
    { label: 'TP 1',  val: btc.tp1,   color: 'var(--green)' },
    { label: 'Entry', val: btc.entry, color: 'var(--yellow)' },
    { label: 'Stop',  val: btc.stop,  color: 'var(--red)' },
  ].filter(l => l.val);

  if (!levels.length || !price) return '';

  const rows = levels.map(l => {
    const dist    = ((l.val - price) / price * 100);
    const distStr = dist >= 0 ? `+${dist.toFixed(2)}%` : `${dist.toFixed(2)}%`;
    return `<div class="liq-row">
      <span class="liq-label" style="color:${l.color}">${l.label}</span>
      <div class="liq-line" style="border-color:${l.color}40"></div>
      <span class="liq-price" style="color:${l.color}">${fmtPrice(l.val)}</span>
      <span class="liq-dist c-muted">${distStr}</span>
    </div>`;
  }).join('');

  return `<div class="card-liquidity">
    <div class="layers-header">ZONAS DE LIQUIDEZ</div>
    <div class="liq-map">${rows}</div>
  </div>`;
}

// ── TradingView Lightweight Charts ────────────────────────────────────────

function initTVChart(el) {
  // Destrói chart anterior se existir
  if (_tvChart) {
    try { _tvChart.remove(); } catch (_) {}
  }
  _tvChart = null; _tvCandleSeries = null; _tvVolSeries = null; _tvPriceLines = [];

  const chart = LightweightCharts.createChart(el, {
    width:  el.clientWidth || 400,
    height: 240,
    layout: {
      background: { type: 'solid', color: '#0B0F14' },
      textColor:  '#8B98A5',
      fontSize:   10,
      fontFamily: "JetBrains Mono, monospace",
    },
    grid: {
      vertLines: { color: 'rgba(26, 34, 45, 0.8)' },
      horzLines: { color: 'rgba(26, 34, 45, 0.6)' },
    },
    crosshair: {
      mode: 1,   // Normal
      vertLine: { color: 'rgba(139,152,165,0.35)', labelBackgroundColor: '#161C25' },
      horzLine: { color: 'rgba(139,152,165,0.35)', labelBackgroundColor: '#161C25' },
    },
    rightPriceScale: {
      borderColor: '#1A222D',
      textColor:   '#8B98A5',
      scaleMargins: { top: 0.08, bottom: 0.22 },
    },
    timeScale: {
      borderColor:     '#1A222D',
      timeVisible:     true,
      secondsVisible:  false,
      rightOffset:     6,
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
    handleScale:  { mouseWheel: true, pinch: true },
  });

  // Candlestick series
  const candleSeries = chart.addCandlestickSeries({
    upColor:         '#00FFB2',
    downColor:       '#FF4D4F',
    borderUpColor:   '#00FFB2',
    borderDownColor: '#FF4D4F',
    wickUpColor:     'rgba(0, 255, 178, 0.65)',
    wickDownColor:   'rgba(255, 77, 79, 0.65)',
  });

  // Volume histogram — ocupa os 20% inferiores
  const volSeries = chart.addHistogramSeries({
    priceFormat:  { type: 'volume' },
    priceScaleId: 'vol',
    lastValueVisible: false,
    priceLineVisible: false,
  });
  chart.priceScale('vol').applyOptions({
    scaleMargins: { top: 0.80, bottom: 0 },
  });

  _tvChart        = chart;
  _tvCandleSeries = candleSeries;
  _tvVolSeries    = volSeries;
  _tvChartCont    = el;
}

async function updateTVChart() {
  const el = document.getElementById('sparklineChart');
  if (!el) return;

  // Se o container DOM mudou (card re-renderizou), reinicializa o chart
  if (_tvChartCont !== el) initTVChart(el);

  try {
    const candles = await fetch(`/api/candles?interval=${_sparkTf}&limit=150`).then(r => r.json());
    if (!Array.isArray(candles) || candles.length < 2) return;

    // Formata dados para Lightweight Charts (tempo em segundos)
    const candleData = candles.map(c => ({
      time:  Math.floor(c.t / 1000),
      open:  c.o,
      high:  c.h,
      low:   c.l,
      close: c.c,
    }));
    const volData = candles.map(c => ({
      time:  Math.floor(c.t / 1000),
      value: c.v,
      color: c.c >= c.o ? 'rgba(0,255,178,0.18)' : 'rgba(255,77,79,0.18)',
    }));

    _tvCandleSeries.setData(candleData);
    _tvVolSeries.setData(volData);

    // Remove price lines anteriores e adiciona novas (Entry/Stop/TP)
    _tvPriceLines.forEach(pl => {
      try { _tvCandleSeries.removePriceLine(pl); } catch (_) {}
    });
    _tvPriceLines = [];

    const LEVELS = [
      { key: 'tp3',   label: 'TP3',   color: 'rgba(0,255,178,0.45)' },
      { key: 'tp2',   label: 'TP2',   color: 'rgba(0,255,178,0.65)' },
      { key: 'tp1',   label: 'TP1',   color: 'rgba(0,255,178,0.90)' },
      { key: 'entry', label: 'Entry', color: '#F0B429' },
      { key: 'stop',  label: 'Stop',  color: 'rgba(255,77,79,0.90)' },
    ];
    for (const cfg of LEVELS) {
      const price = _chartLevels[cfg.key];
      if (price && price > 0) {
        _tvPriceLines.push(_tvCandleSeries.createPriceLine({
          price,
          color:            cfg.color,
          lineWidth:        1,
          lineStyle:        2,   // Dashed
          axisLabelVisible: true,
          title:            cfg.label,
        }));
      }
    }

    _tvChart.timeScale().fitContent();

  } catch (e) {
    console.warn('[Trinity] TV chart error:', e);
  }
}

// Alias para compatibilidade com chamadas existentes
const updateSparkline = updateTVChart;

function onSparkTf(tf) {
  _sparkTf = tf;
  document.querySelectorAll('.spark-tf-btn').forEach(b => b.classList.toggle('active', b.dataset.tf === tf));
  updateTVChart();
}

// ── Section 1: Radar ──────────────────────────────────────────────────────

function renderRadar(data) {
  const grid   = document.getElementById('radarGrid');
  const status = document.getElementById('radarStatusBar');

  if (!data || data.status === 'no_data') {
    grid.innerHTML   = '<p class="c-muted" style="padding:8px;grid-column:1/-1">Aguardando primeira análise...</p>';
    status.innerHTML = '<span class="c-muted">—</span>';
    return;
  }

  const btc   = data.btc || {};
  const score = btc.inst_score || 50;

  const items = [
    { label: 'BTC',            value: fmtPct(btc.btc_change),              desc: btc.btc_change >= 0 ? 'Alta 24h'       : 'Queda 24h',      bull: btc.btc_change >= 0 },
    { label: 'ETH',            value: fmtPct(btc.eth_change),              desc: btc.eth_change >= 0 ? 'Alta 24h'       : 'Queda 24h',      bull: btc.eth_change >= 0 },
    { label: 'BTC DOMINANCE',  value: (btc.btc_dominance  || 0).toFixed(1) + '%', desc: btc.btc_dominance > 55  ? 'Capital em BTC'  : 'Distribuído', bull: btc.btc_dominance > 55 },
    { label: 'USDT DOMINANCE', value: (btc.usdt_dominance || 0).toFixed(1) + '%', desc: btc.usdt_dominance > 8  ? 'Fuga detectada' : 'Normal',      bull: btc.usdt_dominance <= 8 },
  ];

  grid.innerHTML = items.map(i => `
    <div class="radar-item">
      <span class="radar-label">${i.label}</span>
      <span class="radar-value ${i.bull ? 'c-green' : 'c-red'}">${i.value}</span>
      <span class="radar-desc">${i.desc}</span>
    </div>`).join('');

  const sc     = scoreColor(score);
  const dir    = btc.direction || 'AGUARDANDO';
  const regime = marketRegime(score, dir);
  const prob   = probabilities(score);
  const delta  = scoreDelta();
  const deltaHtml = delta !== null
    ? `<span class="score-delta ${delta >= 0 ? 'c-green' : 'c-red'}">${delta >= 0 ? '↑' : '↓'} ${Math.abs(delta).toFixed(1)}% <span class="c-muted" style="font-size:10px">(5min)</span></span>`
    : '';

  status.innerHTML = `
    <div class="radar-bias-left">
      <span class="regime-tag ${regime.cls}">${regime.icon} ${regime.label}</span>
      <span class="radar-bias-detail c-muted">${btc.correlation_bias || ''}</span>
    </div>
    <div class="radar-prob">
      <div class="prob-bar-wrap">
        <div class="prob-seg prob-short-seg" style="width:${prob.short}%">
          <span class="prob-label">SHORT ${prob.short}%</span>
        </div>
        <div class="prob-seg prob-long-seg" style="width:${prob.long}%">
          <span class="prob-label">LONG ${prob.long}%</span>
        </div>
      </div>
    </div>
    <div class="radar-score-right">
      <div style="display:flex;align-items:baseline;gap:2px">
        <span class="radar-score-num" style="color:${sc}">${score.toFixed(0)}</span>
        <span class="radar-score-denom">%</span>
      </div>
      ${deltaHtml}
      <span style="font-size:10px;color:${sc};font-weight:600;margin-top:4px">${scoreLabel(score)}</span>
    </div>`;
}

// ── Section 2: Cards ──────────────────────────────────────────────────────

function renderBTCCard(data) {
  if (!data || data.status === 'no_data') {
    return `<div class="asset-card">
      <div class="card-stripe" style="background:var(--surface2)"></div>
      <div class="card-header">
        <div class="card-meta">
          <span class="card-name">BTC / USDT</span>
          <span class="card-price" id="btcPriceLive">—</span>
          <div class="price-ticker-row">
            <span class="price-tick"><span class="tick-dot" id="btcTickDot"></span><span class="tick-val" id="btcTickVal">—</span></span>
          </div>
        </div>
      </div>
      <div style="padding:48px;text-align:center;color:var(--text-muted);font-size:12px;">Aguardando análise institucional...</div>
    </div>`;
  }

  const btc       = data.btc;
  const score     = btc.inst_score || 50;
  const sc        = scoreColor(score);
  const breakdown = btc.breakdown || {};
  const layers    = btc.layer_scores || {};

  // Track score history for delta
  _scoreHistory.push({ score, ts: Date.now() });
  if (_scoreHistory.length > 10) _scoreHistory.shift();

  const delta     = scoreDelta();
  const deltaHtml = delta !== null
    ? `<span class="score-delta ${delta >= 0 ? 'c-green' : 'c-red'}">${delta >= 0 ? '↑' : '↓'} ${Math.abs(delta).toFixed(1)}%<span class="c-muted" style="font-size:9px"> (5min)</span></span>`
    : '';

  const stripeColor = score >= 60 ? 'var(--green)' : score <= 40 ? 'var(--red)' : 'var(--yellow)';
  const regime      = marketRegime(score, btc.direction);
  const vol         = volatilityLabel(btc.atr_pct || 0, btc.squeeze);
  const prob        = probabilities(score);

  const LAYERS = [
    { key: 'market_structure', label: 'Market Structure' },
    { key: 'liquidity',        label: 'Liquidity' },
    { key: 'volume',           label: 'Volume' },
    { key: 'trend',            label: 'Trend' },
    { key: 'correlation',      label: 'Correlation' },
    { key: 'volatility',       label: 'Volatility' },
  ];

  const layerRows = LAYERS.map(l => {
    const s    = layers[l.key] || 50;
    const bias = breakdown[l.key]?.bias || 'NEUTRO';
    return `<div class="layer-row">
      <span class="layer-name">${l.label}</span>
      <div class="layer-track"><div class="layer-fill" style="width:${s}%;background:${scoreColor(s)}"></div></div>
      <span class="layer-bias ${biasClass(bias)}">${bias}</span>
    </div>`;
  }).join('');

  const msDetail = [
    btc.bos_bull   ? '🟢 BOS Bull'   : '',
    btc.bos_bear   ? '🔴 BOS Bear'   : '',
    btc.choch      ? '⚡ CHOCH'       : '',
    btc.sweep_low  ? '↓ Sweep Low'   : '',
    btc.sweep_high ? '↑ Sweep High'  : '',
  ].filter(Boolean).join(' · ') || '—';

  const dirCls   = btc.direction?.includes('LONG') ? 'dir-long' : btc.direction?.includes('SHORT') ? 'dir-short' : '';
  const stale    = isSignalStale(btc.entry, btc.price, data.last_updated);
  const ageH     = Math.floor(dataAgeSeconds(data.last_updated) / 3600);

  // Atualiza níveis para price lines no chart TradingView
  _chartLevels = (stale || !btc.entry) ? {} : {
    entry: btc.entry || null,
    stop:  btc.stop  || null,
    tp1:   btc.tp1   || null,
    tp2:   btc.tp2   || null,
    tp3:   btc.tp3   || null,
  };
  const staleBanner = stale ? `
    <div style="background:rgba(220,50,50,0.15);border:1px solid rgba(220,50,50,0.4);border-radius:6px;
                padding:7px 12px;margin:0 0 10px 0;font-size:11px;color:rgba(255,100,100,0.9);display:flex;
                align-items:center;gap:6px;">
      ⚠ DADOS DESATUALIZADOS (${ageH}h) — aguardando nova análise. Entry/SL/TP ocultados.
    </div>` : '';

  return `<div class="asset-card ${dirCls}">
    <div class="card-stripe" style="background:${stripeColor}"></div>

    <div class="card-header">
      <div class="card-meta">
        <span class="card-name">BTC / USDT</span>
        <span class="card-price" id="btcPriceLive">${fmtPrice(btc.price)}</span>
        <div class="price-ticker-row">
          <span class="price-tick">
            <span class="tick-dot" id="btcTickDot"></span>
            <span class="tick-val" id="btcTickVal">—</span>
          </span>
          <span class="card-updated" style="${stale ? 'color:rgba(220,80,80,0.8)' : ''}">${timeAgo(data.last_updated)}</span>
        </div>
      </div>
      ${statusBadge(btc.direction, score)}
    </div>

    <div class="card-insights">
      <div class="insight-cell">
        <span class="insight-label">MARKET REGIME</span>
        <span class="insight-value ${regime.cls}">${regime.icon} ${regime.label}</span>
      </div>
      <div class="insight-cell">
        <span class="insight-label">VOLATILIDADE</span>
        <span class="insight-value" style="color:${vol.color}">${vol.label}</span>
      </div>
      <div class="insight-cell insight-cell-wide">
        <span class="insight-label">PROBABILIDADE</span>
        <div class="prob-bar-wrap">
          <div class="prob-seg prob-short-seg" style="width:${prob.short}%">
            <span class="prob-label">${prob.short > 20 ? 'SHORT ' + prob.short + '%' : ''}</span>
          </div>
          <div class="prob-seg prob-long-seg" style="width:${prob.long}%">
            <span class="prob-label">${prob.long > 20 ? 'LONG ' + prob.long + '%' : ''}</span>
          </div>
        </div>
        <div class="prob-tags">
          <span class="c-red">SHORT ${prob.short}%</span>
          <span class="c-green">LONG ${prob.long}%</span>
        </div>
      </div>
    </div>

    <div class="card-score">
      <div class="score-row">
        <div>
          <div class="score-number-row">
            <span class="score-number" style="color:${sc}">${score.toFixed(0)}</span>
            <span class="score-pct" style="color:${sc}">%</span>
            ${deltaHtml}
          </div>
          <span class="score-label-text" style="color:${sc}">${scoreLabel(score)}</span>
        </div>
        <div class="score-meta-right">
          <span class="score-strength" style="color:${sc}">${btc.strength || '—'}</span>
          <span class="score-confluences">${btc.confluences || 0} / 6 confluências</span>
          <span class="score-ms-detail">${msDetail}</span>
        </div>
      </div>
      <div class="score-track">
        <div class="score-fill" style="width:${score}%;background:${sc}"></div>
      </div>
    </div>

    <div class="card-confluences">
      <div class="layers-header">CONFLUÊNCIAS</div>
      <div class="confluences-row">${confluenceDots(btc)}</div>
    </div>

    <div class="card-chart">
      <div class="chart-header">
        <span class="insight-label">CHART</span>
        <div class="spark-tf-row">
          ${['1m','5m','15m','1h'].map(tf =>
            `<button class="spark-tf-btn${tf === _sparkTf ? ' active' : ''}" data-tf="${tf}" onclick="onSparkTf('${tf}')">${tf}</button>`
          ).join('')}
        </div>
      </div>
      <div id="sparklineChart" class="sparkline-container">
        <div class="spark-loading">Carregando...</div>
      </div>
    </div>

    ${staleBanner}

    <div class="card-levels">
      ${stale
        ? `<div class="level-cell" style="grid-column:1/-1;text-align:center;color:var(--text-muted);font-size:10px;padding:8px 0">
             Sinal expirado — aguardando nova análise do bot
           </div>`
        : `<div class="level-cell"><span class="level-lbl">Entry</span><span class="level-val val-entry">${fmtPrice(btc.entry)}</span></div>
           <div class="level-cell"><span class="level-lbl">Stop Loss</span><span class="level-val val-stop">${fmtPrice(btc.stop)}</span></div>
           <div class="level-cell"><span class="level-lbl">TP 1</span><span class="level-val val-tp">${fmtPrice(btc.tp1)}</span></div>`
      }
      ${!stale ? `<div class="level-cell"><span class="level-lbl">TP 2</span><span class="level-val val-tp">${fmtPrice(btc.tp2)}</span></div>
      <div class="level-cell"><span class="level-lbl">TP 3</span><span class="level-val val-tp">${fmtPrice(btc.tp3)}</span></div>` : ''}
      <div class="level-cell"><span class="level-lbl">ATR</span><span class="level-val c-muted">${(btc.atr_pct || 0).toFixed(2)}%${btc.squeeze ? ' 🔥' : ''}</span></div>
    </div>

    ${liquidityZones(btc)}

    ${renderLiqHeatmap(_prevBtcPrice || btc.price, _liqLevels)}

    ${renderSmcPanel(data.btc?.smart_money)}

    <div class="card-layers">
      <div class="layers-header">SCORE POR CAMADA (INSTITUCIONAL)</div>
      ${layerRows}
    </div>
  </div>`;
}

function renderSmcPanel(smc) {
  if (!smc || smc.smc_score == null) return '';

  const score   = smc.smc_score || 50;
  const sc      = scoreColor(score);
  const dir     = smc.direction || 'AGUARDANDO';
  const valid   = smc.valid;
  const align   = smc.alignment || '—';
  const conf    = smc.confidence || '—';
  const struct  = smc.structure || {};

  const tfOrder = ['1D', '4H', '1H', '15m'];
  const tfRows  = tfOrder.map(tf => {
    const s = struct[tf] || '?';
    const isBull = s.includes('BULL') || s.includes('CHOCH BULL');
    const isBear = s.includes('BEAR') || s.includes('CHOCH BEAR');
    const cls    = isBull ? 'c-green' : isBear ? 'c-red' : 'c-muted';
    return `<div class="smc-tf-row">
      <span class="smc-tf-label c-muted">${tf}</span>
      <span class="smc-tf-struct ${cls}">${s}</span>
    </div>`;
  }).join('');

  const dirCls = dir === 'LONG' ? 'c-green' : dir === 'SHORT' ? 'c-red' : 'c-muted';
  const reasonHtml = smc.reasoning
    ? `<div class="smc-reasoning c-muted">${smc.reasoning}</div>`
    : '';

  const smcEntry = smc.entry ? `<span class="level-val val-entry">${fmtPrice(smc.entry)}</span>` : '';
  const smcStop  = smc.stop  ? `<span class="level-val val-stop">${fmtPrice(smc.stop)}</span>`  : '';
  const t = smc.targets || {};
  const tp1Html  = t.tp1 ? `<span class="level-val val-tp">${fmtPrice(t.tp1)} <span class="c-muted" style="font-size:9px">RR ${t.rr1}</span></span>` : '';
  const tp2Html  = t.tp2 ? `<span class="level-val val-tp">${fmtPrice(t.tp2)} <span class="c-muted" style="font-size:9px">RR ${t.rr2}</span></span>` : '';
  const tp3Html  = t.tp3 ? `<span class="level-val val-tp">${fmtPrice(t.tp3)} <span class="c-muted" style="font-size:9px">RR ${t.rr3}</span></span>` : '';

  return `<div class="card-smc">
    <div class="layers-header">SMART MONEY CONCEPTS
      <span class="smc-badge ${valid ? 'smc-valid' : 'smc-wait'}">${valid ? '✅ VÁLIDO' : '⏳ AGUARDANDO'}</span>
    </div>
    <div class="smc-top-row">
      <div>
        <span class="score-number" style="color:${sc};font-size:28px">${score.toFixed(0)}</span>
        <span class="score-pct" style="color:${sc}">%</span>
        <span style="font-size:10px;color:${sc};margin-left:4px;font-weight:600">${scoreLabel(score)}</span>
      </div>
      <div style="text-align:right">
        <div class="smc-dir ${dirCls}" style="font-size:13px;font-weight:700">${dir}</div>
        <div class="c-muted" style="font-size:10px">${align} · ${conf}</div>
      </div>
    </div>
    <div class="smc-tf-grid">${tfRows}</div>
    ${(smcEntry || smcStop) ? `<div class="card-levels" style="margin-top:8px">
      ${smcEntry ? `<div class="level-cell"><span class="level-lbl">SMC Entry</span>${smcEntry}</div>` : ''}
      ${smcStop  ? `<div class="level-cell"><span class="level-lbl">SMC Stop</span>${smcStop}</div>`  : ''}
      ${tp1Html  ? `<div class="level-cell"><span class="level-lbl">TP 1</span>${tp1Html}</div>`      : ''}
      ${tp2Html  ? `<div class="level-cell"><span class="level-lbl">TP 2</span>${tp2Html}</div>`      : ''}
      ${tp3Html  ? `<div class="level-cell"><span class="level-lbl">TP 3</span>${tp3Html}</div>`      : ''}
    </div>` : ''}
    ${reasonHtml}
  </div>`;
}

// ── Liquidation Heatmap ───────────────────────────────────────────────────

/**
 * Escala de cor estilo Coinglass: dark-blue → teal → green → yellow
 * pct: 0 (frio) → 1 (quente)
 */
function heatColor(pct) {
  const stops = [
    [14,  22,  80,  0.30],   // 0%   azul escuro
    [20,  90,  140, 0.50],   // 25%  teal
    [30,  170, 110, 0.68],   // 50%  verde médio
    [130, 215,  50, 0.85],   // 75%  verde-amarelo
    [255, 232,   8, 1.00],   // 100% amarelo (zona quente)
  ];
  const n   = stops.length - 1;
  const pos = Math.min(1, Math.max(0, pct)) * n;
  const i   = Math.min(Math.floor(pos), n - 1);
  const t   = pos - i;
  const [r1,g1,b1,a1] = stops[i];
  const [r2,g2,b2,a2] = stops[i + 1];
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);
  const a = (a1 + (a2 - a1) * t).toFixed(2);
  return `rgba(${r},${g},${b},${a})`;
}

function renderLiqHeatmap(price, levels) {
  const _header = `<div class="layers-header">LIQUIDATION MAP
    <span style="font-size:9px;font-weight:400;color:var(--text-muted);margin-left:4px">±5% · 12h</span>
  </div>`;

  if (!levels?.length || !price) {
    // Se tiver screenshot do Apify, exibe a imagem
    if (_liqImgUrl) {
      return `<div class="card-liqheat">${_header}
        <img src="${_liqImgUrl}" alt="Liquidation Heatmap"
             style="width:100%;border-radius:4px;display:block;margin-top:6px;opacity:0.92"/>
      </div>`;
    }
    const msg = _liqApiError
      ? `<span style="color:rgba(220,80,80,0.7)">${_liqApiError}</span>`
      : `aguardando screenshot...`;
    return `<div class="card-liqheat">${_header}
      <div style="padding:14px 8px;text-align:center;font-size:10px;color:var(--text-muted)">${msg}</div>
    </div>`;
  }

  const BAND     = 0.05;
  const MAX_ROWS = 22;

  const nearby = levels
    .filter(l => Math.abs(l.price - price) / price <= BAND)
    .sort((a, b) => b.price - a.price)
    .slice(0, MAX_ROWS);

  if (nearby.length < 3) {
    return `<div class="card-liqheat">${_header}
      <div style="padding:14px 8px;text-align:center;font-size:10px;color:var(--text-muted)">
        sem níveis próximos ao preço atual
      </div>
    </div>`;
  }

  // Normaliza pelo percentil 95 para evitar que outliers esmaguem a escala
  const vols   = nearby.map(l => l.long_usd + l.short_usd).sort((a, b) => a - b);
  const p95    = vols[Math.floor(vols.length * 0.95)] || vols[vols.length - 1] || 0.001;
  const maxVol = Math.max(p95, 0.001);

  let priceInserted = false;
  const rows = [];

  for (const l of nearby) {
    if (!priceInserted && l.price < price) {
      priceInserted = true;
      rows.push(`<div class="liqheat-sep">
        <span class="liqheat-sep-tag">${fmtPrice(price)}</span>
      </div>`);
    }

    const isAbove  = l.price >= price;
    const longVol  = l.long_usd;
    const shortVol = l.short_usd;
    const total    = longVol + shortVol;
    const pct      = Math.min(1, total / maxVol);
    const barPct   = Math.max(0.8, pct * 100).toFixed(1);
    const col      = heatColor(pct);

    // Glow proporcional à intensidade (zonas quentes brilham)
    const glowSize = pct > 0.55 ? `${(pct * 7).toFixed(1)}px` : '0px';
    const glowStyle = pct > 0.55 ? `box-shadow:0 0 ${glowSize} ${col};` : '';

    const priceStr = '$' + Math.round(l.price).toLocaleString('en-US');
    const volStr   = total >= 1
      ? total.toFixed(1) + 'M'
      : (total * 1000).toFixed(0) + 'K';

    // Indicador L/S discreto
    const dominant = isAbove ? 'S' : 'L';
    const domCol   = isAbove ? 'rgba(80,200,120,0.65)' : 'rgba(220,80,80,0.65)';

    rows.push(`<div class="liqheat-row">
      <span class="liqheat-lbl">${priceStr}</span>
      <div class="liqheat-track">
        <div class="liqheat-bar" style="width:${barPct}%;background:${col};${glowStyle}"></div>
      </div>
      <span class="liqheat-vol">${volStr}</span>
      <span class="liqheat-side" style="color:${domCol}">${dominant}</span>
    </div>`);
  }

  if (!priceInserted) {
    rows.push(`<div class="liqheat-sep">
      <span class="liqheat-sep-tag">${fmtPrice(price)}</span>
    </div>`);
  }

  // Legenda: barra de gradiente de intensidade
  const gradStops = [0, 0.25, 0.5, 0.75, 1].map(p => heatColor(p)).join(',');

  return `<div class="card-liqheat">
    ${_header}
    <div class="liqheat-wrap">${rows.join('')}</div>
    <div class="liqheat-legend">
      <span class="c-muted" style="font-size:8px">baixo</span>
      <div style="flex:1;height:4px;border-radius:2px;background:linear-gradient(to right,${gradStops});opacity:0.75;margin:0 6px;align-self:center"></div>
      <span class="c-muted" style="font-size:8px">alto</span>
      <span style="margin-left:10px;font-size:8px;color:rgba(220,80,80,0.7)">L=long</span>
      <span style="margin-left:6px;font-size:8px;color:rgba(80,200,120,0.7)">S=short</span>
    </div>
  </div>`;
}

async function updateLiqHeatmap() {
  try {
    const res  = await fetch('/api/liquidations');
    const data = await res.json();
    if (Array.isArray(data.levels) && data.levels.length > 0) {
      _liqLevels   = data.levels;
      _liqApiError = null;
    } else {
      _liqApiError = data.error || data.api_debug || 'sem dados';
    }
  } catch(e) {
    _liqApiError = e.message;
  }
}

async function updateLiqScreenshot() {
  try {
    const res  = await fetch('/api/liq-screenshot');
    const data = await res.json();
    if (data.url) _liqImgUrl = data.url;
  } catch(_) {}
}

function renderETHCard() {
  return `<div class="coming-soon">
    <div class="coming-soon-glyph">Ξ</div>
    <div class="coming-soon-name">ETH / USDT</div>
    <div class="coming-soon-label">EM DESENVOLVIMENTO</div>
  </div>`;
}

// ── Section 3: History ────────────────────────────────────────────────────

function renderHistory(signals) {
  const tbody = document.getElementById('historyBody');
  if (!signals || signals.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="table-empty">Nenhum sinal registrado ainda</td></tr>`;
    return;
  }
  tbody.innerHTML = signals.slice(0, 5).map(sig => {
    const score = sig.inst_score || 0;
    const dir   = sig.direction || '—';
    const sc    = scoreColor(score);
    const isL   = dir === 'LONG';
    const isS   = dir === 'SHORT';
    return `<tr>
      <td class="c-muted">${fmtDateTime(sig.timestamp)}</td>
      <td style="font-weight:600">BTC</td>
      <td><span class="dir-badge ${isL ? 'dir-long' : isS ? 'dir-short' : 'dir-wait'}" style="font-size:10px;padding:3px 10px">${isL ? '▲ LONG' : isS ? '▼ SHORT' : dir}</span></td>
      <td><span class="score-pill" style="background:${sc}22;color:${sc};border:1px solid ${sc}44">${score.toFixed(0)}%</span></td>
      <td class="c-yellow">${sig.entry ? fmtPrice(sig.entry) : '—'}</td>
      <td class="c-red">${sig.stop ? fmtPrice(sig.stop) : '—'}</td>
      <td class="c-muted">${sig.confluences || 0}/6</td>
      <td><span class="status-tag">Monitorando</span></td>
    </tr>`;
  }).join('');
}

// ── Real-time Price Ticker ─────────────────────────────────────────────────

function fmtDelta(d) {
  const abs = Math.abs(d);
  return (d >= 0 ? '+' : '-') + '$' + abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

async function priceTick() {
  try {
    const res  = await fetch('/api/price');
    const data = await res.json();
    if (!data.price) return;

    const price   = data.price;
    const priceEl = document.getElementById('btcPriceLive');
    const dotEl   = document.getElementById('btcTickDot');
    const valEl   = document.getElementById('btcTickVal');
    if (!priceEl || !dotEl || !valEl) return;

    priceEl.textContent = fmtPrice(price);

    if (_prevBtcPrice !== null && price !== _prevBtcPrice) {
      const delta = price - _prevBtcPrice;
      const pct   = (delta / _prevBtcPrice) * 100;
      const up    = delta > 0;
      priceEl.classList.remove('flash-up', 'flash-down');
      void priceEl.offsetWidth;
      priceEl.classList.add(up ? 'flash-up' : 'flash-down');
      dotEl.className = 'tick-dot ' + (up ? 'up' : 'down');
      valEl.className = 'tick-val ' + (up ? 'up' : 'down');
      valEl.textContent = fmtDelta(delta) + '  ' + fmtPct(pct, 3) + '  (1s)';
      setTimeout(() => { dotEl.className = 'tick-dot'; valEl.className = 'tick-val'; }, 900);
    } else if (_prevBtcPrice === null) {
      const c24 = data.change_24h;
      if (c24 != null) {
        const up24 = c24 >= 0;
        dotEl.className = 'tick-dot ' + (up24 ? 'up' : 'down');
        valEl.className = 'tick-val ' + (up24 ? 'up' : 'down');
        valEl.textContent = fmtPct(c24) + '  (24h)';
      }
    }
    _prevBtcPrice = price;
  } catch (_) {}
}

// ── Main Loop ─────────────────────────────────────────────────────────────

async function refresh() {
  try {
    const [sRes, hRes] = await Promise.all([
      fetch('/api/status'),
      fetch('/api/signals?limit=25'),
    ]);
    const state   = await sRes.json();
    const signals = await hRes.json();

    renderRadar(state);
    document.getElementById('cardsGrid').innerHTML = renderBTCCard(state) + renderETHCard();
    renderHistory(signals);

    const now = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('updateTime').textContent = `Atualizado ${now}`;

    updateSparkline();
  } catch (e) {
    console.error('[QuantDesk] fetch error:', e);
  }
}

function clockTick() {
  const el = document.getElementById('footerClock');
  if (el) el.textContent = new Date().toLocaleTimeString('pt-BR');
}

// ── Section 4: Crash Radar ────────────────────────────────────────────────

const CRASH_REFRESH_MS = 30_000;

function crashUrgencyColor(urgency) {
  const map = { CRITICAL: 'var(--red)', DANGER: '#FF8C00', ALERT: 'var(--yellow)', WATCH: 'var(--text-muted)' };
  return map[urgency] || 'var(--text-muted)';
}

function crashUrgencyEmoji(urgency) {
  const map = { CRITICAL: '🚨', DANGER: '⚠️', ALERT: '⚡', WATCH: '👁' };
  return map[urgency] || '•';
}

function fmtCrashScore(score) {
  const col = score >= 80 ? 'var(--red)' : score >= 60 ? '#FF8C00' : score >= 40 ? 'var(--yellow)' : 'var(--text-muted)';
  return `<span style="color:${col};font-weight:700;font-size:22px">${score.toFixed(0)}</span><span style="color:${col};font-size:12px">/100</span>`;
}

function renderComponentBar(label, value) {
  const col = value >= 70 ? 'var(--red)' : value >= 50 ? '#FF8C00' : 'var(--yellow)';
  const w   = Math.max(2, Math.round((value / 100) * 60));
  return `<div class="cr-comp-row">
    <span class="cr-comp-label">${label}</span>
    <div class="cr-comp-track"><div class="cr-comp-fill" style="width:${w}px;background:${col}"></div></div>
    <span class="cr-comp-val" style="color:${col}">${value.toFixed(0)}</span>
  </div>`;
}

function renderCrashCard(c) {
  const moveCls  = c.move_classification || 'WEAK';
  const oppScore = c.opportunity_score   || 0;
  const movePct  = c.expected_move_pct   || c.estimated_drawdown || 0;

  const clsColors = {
    EXTREME:   '#ff1744',
    STRONG:    '#ff6d00',
    TRADEABLE: '#ffab00',
    WEAK:      '#78909c',
  };
  const clsColor = clsColors[moveCls] || '#78909c';

  const clsEmoji = { EXTREME: '🔴', STRONG: '🟠', TRADEABLE: '🟡', WEAK: '⚪' }[moveCls] || '⚡';

  const pctStr = c.price_change_pct >= 0
    ? `<span class="c-green">+${c.price_change_pct.toFixed(1)}%</span>`
    : `<span class="c-red">${c.price_change_pct.toFixed(1)}%</span>`;
  const priceStr = c.price < 10
    ? `$${c.price.toFixed(4)}`
    : `$${c.price.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}`;

  const comp     = c.component_scores || {};
  const oppPct   = Math.min(100, Math.max(0, oppScore));
  const oppColor = oppPct >= 85 ? '#ff1744' : oppPct >= 70 ? '#ff6d00' : '#ffab00';

  const compBars = [
    renderComponentBar('Liquidez',    comp.liquidity    || 0),
    renderComponentBar('Alavancagem', comp.leverage     || 0),
    renderComponentBar('Whale Dump',  comp.whale        || 0),
    renderComponentBar('Funding/OI',  comp.funding_oi   || 0),
  ].join('');

  const signals = (c.top_signals || []).slice(0, 3)
    .map(s => `<div class="cr-signal-row">• ${s}</div>`).join('');

  return `<div class="crash-card" style="border-left:3px solid ${clsColor}">
    <div class="cr-header">
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:10px;font-weight:700;color:${clsColor};text-transform:uppercase;letter-spacing:0.5px">${clsEmoji} ${moveCls} OPPORTUNITY</span>
        <span style="font-size:10px;color:var(--text-muted)">DOWN ↓</span>
      </div>
      <div class="cr-badge" style="background:${clsColor}22;color:${clsColor};border:1px solid ${clsColor}44">${movePct > 0 ? `-${movePct.toFixed(1)}%` : 'CRASH'}</div>
    </div>
    <div style="margin:6px 0 4px">
      <span class="cr-symbol">${c.symbol.replace('USDT','')}</span><span class="cr-usdt">USDT</span>
      <span class="cr-price">${priceStr}</span> ${pctStr}
    </div>
    <div style="margin:8px 0;padding:8px;background:${clsColor}11;border-radius:6px">
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:2px">MOVIMENTO ESPERADO (SHORT)</div>
      <div style="font-size:22px;font-weight:700;color:${clsColor}">-${movePct.toFixed(1)}%</div>
    </div>
    <div style="margin:6px 0">
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-bottom:3px">
        <span>OPPORTUNITY SCORE</span><span style="color:${oppColor};font-weight:700">${oppScore.toFixed(0)}/100</span>
      </div>
      <div style="background:var(--border);border-radius:3px;height:5px">
        <div style="width:${oppPct}%;background:${oppColor};height:5px;border-radius:3px;transition:width 0.4s"></div>
      </div>
    </div>
    <div class="cr-components">${compBars}</div>
    ${signals ? `<div class="cr-signals">${signals}</div>` : ''}
    <div class="cr-action c-muted">${c.recommended_action}</div>
  </div>`;
}

function renderCrashRadar(data) {
  const grid  = document.getElementById('crashGrid');
  const meta  = document.getElementById('crashMetaBar');
  const badge = document.getElementById('crashScanBadge');

  if (!data || !data.candidates || data.candidates.length === 0) {
    const ts0 = data?.scan_ts ? new Date(data.scan_ts).toLocaleTimeString('pt-BR') : '—';
    if (grid)  grid.innerHTML  = '<div class="cr-empty">Nenhuma oportunidade institucional detectada — aguardando movimentos ≥6%...</div>';
    if (meta)  meta.innerHTML  = `<span class="c-muted">Último scan: <b>${ts0}</b> · ${data?.coins_scanned || 0} moedas monitoradas · nenhum setup ≥6% detectado</span>`;
    if (badge) { badge.textContent = 'MONITORANDO'; badge.className = 'crash-live-badge'; }
    return;
  }

  const ts       = data.scan_ts ? new Date(data.scan_ts).toLocaleTimeString('pt-BR') : '—';
  const duration = data.scan_duration_s ? `${data.scan_duration_s.toFixed(1)}s` : '—';
  const extremes  = data.candidates.filter(c => (c.move_classification || '') === 'EXTREME').length;
  const strongs   = data.candidates.filter(c => (c.move_classification || '') === 'STRONG').length;
  const top       = data.candidates[0];

  if (meta) {
    const topStr = top
      ? `Top: <strong>${top.symbol.replace('USDT','')}</strong> opp <strong>${(top.opportunity_score||0).toFixed(0)}</strong> | move <strong>${(top.expected_move_pct||0).toFixed(1)}%</strong>`
      : '';
    meta.innerHTML = `
      <span class="c-muted">Último scan: <b>${ts}</b> em ${duration}</span>
      <span class="c-muted">${data.coins_scanned || 0} moedas · ${data.candidates.length} oportunidades</span>
      ${extremes ? `<span class="cr-count-badge cr-count-critical">🔴 ${extremes} EXTREME</span>` : ''}
      ${strongs  ? `<span class="cr-count-badge cr-count-danger">🟠 ${strongs} STRONG</span>`    : ''}
      ${topStr   ? `<span class="c-muted">${topStr}</span>` : ''}
    `;
  }

  if (badge) {
    if (extremes)     { badge.textContent = 'EXTREME'; badge.className = 'crash-live-badge badge-critical'; }
    else if (strongs) { badge.textContent = 'STRONG';  badge.className = 'crash-live-badge badge-danger';   }
    else              { badge.textContent = 'ATIVO';   badge.className = 'crash-live-badge badge-active';   }
  }

  if (grid) grid.innerHTML = data.candidates.map(renderCrashCard).join('');
}

async function updateCrashRadar() {
  try {
    const data = await fetch('/api/crash-scanner').then(r => r.json());
    renderCrashRadar(data);
  } catch (e) {
    const grid = document.getElementById('crashGrid');
    if (grid) grid.innerHTML = `<div class="cr-empty c-muted">Erro ao carregar: ${e.message}</div>`;
  }
}

// FABs — aparecem após scroll
(function() {
  const fabs = document.querySelectorAll('.crash-fab, .pump-fab');
  fabs.forEach(fab => {
    if (!fab) return;
    fab.style.cssText = 'opacity:0;pointer-events:none;transition:opacity 0.3s ease';
  });
  window.addEventListener('scroll', () => {
    const show = window.scrollY > 300;
    fabs.forEach(fab => {
      fab.style.opacity = show ? '1' : '0';
      fab.style.pointerEvents = show ? 'auto' : 'none';
    });
  }, { passive: true });
})();

// ── Section 5: Pump Radar ─────────────────────────────────────────────────────

const PUMP_REFRESH_MS = 30_000;

function pumpUrgencyColor(urgency) {
  const map = { LAUNCH: 'var(--green)', READY: '#00C853', ALERT: 'var(--yellow)', WATCH: 'var(--text-muted)' };
  return map[urgency] || 'var(--text-muted)';
}

function pumpUrgencyEmoji(urgency) {
  return { LAUNCH: '🚀', READY: '⚡', ALERT: '📡', WATCH: '👁' }[urgency] || '📡';
}

function fmtPumpScore(score) {
  if (score >= 80) return `<span style="color:var(--green);font-weight:700">${score.toFixed(0)}</span>`;
  if (score >= 60) return `<span style="color:#00C853;font-weight:700">${score.toFixed(0)}</span>`;
  if (score >= 40) return `<span style="color:var(--yellow)">${score.toFixed(0)}</span>`;
  return `<span style="color:var(--text-muted)">${score.toFixed(0)}</span>`;
}

function renderPumpComponentBar(label, value) {
  const pct  = Math.min(100, Math.max(0, value));
  const color = pct >= 70 ? 'var(--green)' : pct >= 50 ? '#00C853' : pct >= 30 ? 'var(--yellow)' : 'var(--text-muted)';
  return `<div class="pr-comp-row">
    <span class="pr-comp-label">${label}</span>
    <div class="pr-comp-track"><div class="pr-comp-fill" style="width:${pct}%;background:${color}"></div></div>
    <span class="pr-comp-val">${pct.toFixed(0)}</span>
  </div>`;
}

function renderPumpCard(c) {
  const moveCls  = c.move_classification || 'WEAK';
  const oppScore = c.opportunity_score   || 0;
  const movePct  = c.expected_move_pct   || 0;

  const clsColors = {
    EXTREME:   'var(--green)',
    STRONG:    '#00C853',
    TRADEABLE: '#ffab00',
    WEAK:      '#78909c',
  };
  const clsColor = clsColors[moveCls] || '#78909c';
  const clsEmoji = { EXTREME: '🚀', STRONG: '⚡', TRADEABLE: '📡', WEAK: '👁' }[moveCls] || '⚡';

  const pct      = c.price_change_pct >= 0 ? `+${c.price_change_pct.toFixed(1)}%` : `${c.price_change_pct.toFixed(1)}%`;
  const pctColor = c.price_change_pct >= 0 ? 'var(--green)' : 'var(--red)';
  const price    = c.price < 10 ? c.price.toFixed(4) : c.price.toFixed(2);
  const comp     = c.component_scores || {};

  const oppPct   = Math.min(100, Math.max(0, oppScore));
  const oppColor = oppPct >= 85 ? 'var(--green)' : oppPct >= 70 ? '#00C853' : '#ffab00';

  const signals  = (c.top_signals || []).slice(0, 3).map(s => `<div class="pr-signal-row">▸ ${s}</div>`).join('');

  return `<div class="pump-card" style="border-left:3px solid ${clsColor}">
    <div class="pr-header">
      <div style="display:flex;align-items:center;gap:6px">
        <span style="font-size:10px;font-weight:700;color:${clsColor};text-transform:uppercase;letter-spacing:0.5px">${clsEmoji} ${moveCls} OPPORTUNITY</span>
        <span style="font-size:10px;color:var(--text-muted)">UP ↑</span>
      </div>
      <span style="font-size:11px;color:${pctColor}">${pct}</span>
    </div>
    <div style="margin:4px 0 6px">
      <span class="pr-symbol">${c.symbol.replace('USDT','')}</span>
      <span style="font-size:12px;color:var(--text-muted);margin-left:4px">$${price}</span>
    </div>
    <div style="margin:8px 0;padding:8px;background:${clsColor}18;border-radius:6px">
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:2px">MOVIMENTO ESPERADO (LONG)</div>
      <div style="font-size:22px;font-weight:700;color:${clsColor}">+${movePct.toFixed(1)}%</div>
    </div>
    <div style="margin:6px 0">
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);margin-bottom:3px">
        <span>OPPORTUNITY SCORE</span><span style="color:${oppColor};font-weight:700">${oppScore.toFixed(0)}/100</span>
      </div>
      <div style="background:var(--border);border-radius:3px;height:5px">
        <div style="width:${oppPct}%;background:${oppColor};height:5px;border-radius:3px;transition:width 0.4s"></div>
      </div>
    </div>
    <div class="pr-comps">
      ${renderPumpComponentBar('Whale',   comp.whale   || 0)}
      ${renderPumpComponentBar('Squeeze', comp.squeeze || 0)}
      ${renderPumpComponentBar('Gravity', comp.gravity || 0)}
      ${renderPumpComponentBar('Breakout',comp.breakout|| 0)}
    </div>
    ${signals ? `<div class="pr-signals">${signals}</div>` : ''}
    <div class="pr-action">${c.recommended_action || ''}</div>
  </div>`;
}

function renderPumpRadar(data) {
  const badge   = document.getElementById('pumpScanBadge');
  const metaBar = document.getElementById('pumpMetaBar');
  const grid    = document.getElementById('pumpGrid');

  const candidates  = data.candidates || [];
  const scanTs      = data.scan_ts ? new Date(data.scan_ts).toLocaleTimeString('pt-BR') : '--:--';
  const count       = candidates.length;
  const extremeCount = candidates.filter(c => (c.move_classification || '') === 'EXTREME').length;
  const strongCount  = candidates.filter(c => (c.move_classification || '') === 'STRONG').length;

  if (badge) {
    if (extremeCount > 0) {
      badge.className = 'pump-live-badge badge-launch';
      badge.textContent = `🚀 ${extremeCount} EXTREME`;
    } else if (strongCount > 0) {
      badge.className = 'pump-live-badge badge-ready';
      badge.textContent = `⚡ ${strongCount} STRONG`;
    } else {
      badge.className = 'pump-live-badge badge-active';
      badge.textContent = count > 0 ? `LIVE · ${count}` : 'INICIANDO';
    }
  }

  if (metaBar) {
    const top = candidates[0];
    const topStr = top
      ? `<span class="pr-count-badge ${extremeCount > 0 ? 'crit' : 'norm'}">${count} oportunidades</span>
         &nbsp;Top: <strong>${top.symbol.replace('USDT','')}</strong> opp <strong>${(top.opportunity_score||0).toFixed(0)}</strong> | move <strong>+${(top.expected_move_pct||0).toFixed(1)}%</strong>
         · <span style="color:var(--text-muted)">scan ${data.scan_duration_s || 0}s · ${scanTs}</span>`
      : `<span class="c-muted" style="font-size:11px">Scanner em execução... ${scanTs}</span>`;
    metaBar.innerHTML = topStr;
  }

  if (grid) grid.innerHTML = candidates.map(renderPumpCard).join('');
}

async function updatePumpRadar() {
  try {
    const data = await fetch('/api/pump-scanner').then(r => r.json());
    renderPumpRadar(data);
  } catch (e) {
    console.warn('Pump Radar fetch error:', e);
  }
}

// ── Backtest Performance ─────────────────────────────────────────────────────

let _btChart = null;
let _btChartCont = null;

function _initBtChart(el) {
  if (_btChart) { try { _btChart.remove(); } catch (_) {} }
  _btChart = null;

  const chart = LightweightCharts.createChart(el, {
    width:  el.clientWidth || 600,
    height: 200,
    layout: {
      background: { type: 'solid', color: '#0B0F14' },
      textColor: '#8B98A5',
      fontSize: 10,
      fontFamily: "JetBrains Mono, monospace",
    },
    grid: {
      vertLines: { color: 'rgba(26,34,45,0.8)' },
      horzLines: { color: 'rgba(26,34,45,0.6)' },
    },
    crosshair: {
      mode: 1,
      vertLine: { color: 'rgba(139,152,165,0.35)', labelBackgroundColor: '#161C25' },
      horzLine: { color: 'rgba(139,152,165,0.35)', labelBackgroundColor: '#161C25' },
    },
    rightPriceScale: { borderColor: '#1A222D', textColor: '#8B98A5' },
    timeScale: { borderColor: '#1A222D', timeVisible: false, rightOffset: 4 },
    handleScroll: true,
    handleScale:  true,
  });

  const areaSeries = chart.addAreaSeries({
    lineColor:  '#00FFB2',
    topColor:   'rgba(0,255,178,0.20)',
    bottomColor:'rgba(0,255,178,0.00)',
    lineWidth: 2,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
  });

  _btChart     = chart;
  _btChartCont = el;
  return areaSeries;
}

function renderBtEquityCurve(equityCurve) {
  const el = document.getElementById('btChartWrap');
  if (!el || !equityCurve || equityCurve.length < 2) return;

  const areaSeries = (_btChartCont !== el) ? _initBtChart(el) : (() => {
    // Reusa chart existente — recria series
    if (_btChart) { try { _btChart.remove(); } catch (_) {} }
    return _initBtChart(el);
  })();

  // Converte datas para epoch segundos (YYYY-MM-DD → timestamp)
  const data = equityCurve.map(p => ({
    time:  Math.floor(new Date(p.date + 'T12:00:00Z').getTime() / 1000),
    value: p.equity,
  })).sort((a, b) => a.time - b.time);

  // Remove duplicatas de mesmo dia (mantém último)
  const deduped = [];
  const seen = new Set();
  for (const pt of data) {
    if (!seen.has(pt.time)) { seen.add(pt.time); deduped.push(pt); }
  }

  areaSeries.setData(deduped);
  if (_btChart) _btChart.timeScale().fitContent();
}

function renderBtMetrics(metrics) {
  const el = document.getElementById('btMetrics');
  if (!el || !metrics) return;

  const wr   = metrics.win_rate_pct   ?? 0;
  const sh   = metrics.sharpe_ratio   ?? 0;
  const exp  = metrics.expectancy_r   ?? 0;
  const dd   = metrics.max_drawdown_pct ?? 0;
  const pf   = metrics.profit_factor  ?? 0;
  const ret  = metrics.total_return_pct ?? 0;
  const aw   = metrics.avg_win_r      ?? 0;
  const al   = metrics.avg_loss_r     ?? 0;
  const tot  = metrics.total_trades   ?? 0;
  const wins = metrics.wins           ?? 0;
  const loss = metrics.losses         ?? 0;

  function card(label, value, sub, cls = 'bt-neutral') {
    return `<div class="bt-metric-card ${cls}">
      <div class="bt-metric-label">${label}</div>
      <div class="bt-metric-value">${value}</div>
      <div class="bt-metric-sub">${sub}</div>
    </div>`;
  }

  el.innerHTML = [
    card('Win Rate',    `${wr.toFixed(1)}%`,       `${wins}W / ${loss}L`,         wr >= 50 ? 'bt-positive' : 'bt-negative'),
    card('Sharpe',      sh.toFixed(2),              sh >= 1 ? 'Sólido' : sh >= 0.5 ? 'Razoável' : 'Fraco',  sh >= 1 ? 'bt-positive' : sh >= 0.5 ? 'bt-neutral' : 'bt-negative'),
    card('Expectância', `${exp >= 0 ? '+' : ''}${exp.toFixed(3)}R`, 'por trade',  exp > 0 ? 'bt-positive' : 'bt-negative'),
    card('Max DD',      `${dd.toFixed(1)}%`,        'drawdown máx.',               dd > -5 ? 'bt-positive' : dd > -15 ? 'bt-neutral' : 'bt-negative'),
    card('Trades',      tot,                        `${metrics.period_days ?? 180} dias`, 'bt-neutral'),
    card('Profit F.',   pf.toFixed(2),              pf >= 1.5 ? 'Excelente' : pf >= 1 ? 'Positivo' : 'Negativo', pf >= 1.5 ? 'bt-positive' : pf >= 1 ? 'bt-neutral' : 'bt-negative'),
    card('Avg Win',     `+${aw.toFixed(2)}R`,       'por trade ganho',             'bt-positive'),
    card('Retorno',     `${ret >= 0 ? '+' : ''}${ret.toFixed(1)}%`, `capital: $${(metrics.final_capital ?? 0).toLocaleString('pt-BR', {maximumFractionDigits:0})}`, ret > 0 ? 'bt-positive' : 'bt-negative'),
  ].join('');
}

function renderBtTrades(trades) {
  const tbody = document.getElementById('btBody');
  if (!tbody) return;
  if (!trades || trades.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="table-empty">Nenhum trade no período</td></tr>';
    return;
  }

  const rows = [...trades].reverse().slice(0, 100).map(t => {
    const isLong = t.direction === 'LONG';
    const dirCls = isLong ? 'c-bull' : 'c-bear';
    const resCls = t.result === 'WIN' ? 'bt-win' : t.result === 'LOSS' ? 'bt-loss' : 'bt-timeout';
    const pnlSign = t.pnl_r >= 0 ? '+' : '';
    const statusMap = { TP3: '🟢 TP3', TP2: '🟡 TP2', TP1: '🟠 TP1', STOP: '🔴 STOP', TIMEOUT: '⏱ TIMEOUT' };
    const dt = new Date(t.timestamp);
    const dateStr = `${dt.getUTCDate().toString().padStart(2,'0')}/${(dt.getUTCMonth()+1).toString().padStart(2,'0')}`;

    return `<tr>
      <td style="color:var(--text-dim);font-size:10px">${dateStr}</td>
      <td class="${dirCls}" style="font-weight:700">${t.direction}</td>
      <td style="font-size:11px">${(t.smc_score||0).toFixed(1)}</td>
      <td style="font-size:11px;font-family:'JetBrains Mono',monospace">${(t.entry||0).toLocaleString('en-US',{maximumFractionDigits:1})}</td>
      <td style="font-size:11px;font-family:'JetBrains Mono',monospace">${(t.exit_price||0).toLocaleString('en-US',{maximumFractionDigits:1})}</td>
      <td class="${resCls}" style="font-family:'JetBrains Mono',monospace">${pnlSign}${(t.pnl_r||0).toFixed(2)}R</td>
      <td class="${resCls}" style="font-family:'JetBrains Mono',monospace">${pnlSign}${(t.pnl_pct||0).toFixed(2)}%</td>
      <td class="${resCls}">${statusMap[t.exit_reason] || t.exit_reason}</td>
    </tr>`;
  });

  tbody.innerHTML = rows.join('');
}

async function updateBacktestResults() {
  try {
    const data = await fetch('/api/backtest-results').then(r => r.json());

    if (data.status === 'no_data' || !data.metrics || !data.metrics.total_trades) {
      document.getElementById('btMetrics').innerHTML =
        '<div class="bt-metric-card bt-neutral" style="grid-column:1/-1;text-align:center">' +
        '<div class="bt-metric-label">STATUS</div>' +
        '<div class="bt-metric-value" style="font-size:14px">Backtest não executado</div>' +
        '<div class="bt-metric-sub">Execute: python -m backtester.run_backtest</div>' +
        '</div>';
      return;
    }

    const m = data.metrics;
    renderBtMetrics({ ...m, period_days: data.config?.period_days });
    renderBtEquityCurve(data.equity_curve || []);
    renderBtTrades(data.trades || []);

    const subtitle = document.getElementById('btSubtitle');
    if (subtitle && data.config) {
      const gen = data.generated_at ? new Date(data.generated_at) : null;
      const genStr = gen ? gen.toLocaleDateString('pt-BR') : '';
      subtitle.textContent =
        `${data.config.period_days} dias · BTC/USDT · SMC Engine · ` +
        `${data.config.risk_per_trade_pct}% risco/trade · gerado ${genStr}`;
    }
  } catch (e) {
    console.warn('[Trinity] Backtest fetch error:', e);
  }
}

// Init
refresh();
priceTick();
updateLiqHeatmap();
updateLiqScreenshot();                          // busca URL da screenshot Apify
updateCrashRadar();                             // Crash Radar — primeiro carregamento
updatePumpRadar();                              // Pump Radar — primeiro carregamento
updateBacktestResults();                        // Backtest Performance — primeiro carregamento
setInterval(refresh,                REFRESH_MS);
setInterval(priceTick,              PRICE_MS);
setInterval(clockTick,              1000);
setInterval(updateSparkline,        30_000);
setInterval(updateLiqHeatmap,       300_000);      // 5min — respeita rate limit Coinglass
setInterval(updateLiqScreenshot,    300_000);      // 5min — sincroniza com ciclo do servidor
setInterval(updateCrashRadar,       CRASH_REFRESH_MS); // 30s — sincroniza com ciclo do scanner
setInterval(updatePumpRadar,        PUMP_REFRESH_MS);  // 30s — sincroniza com ciclo do scanner
setInterval(updateBacktestResults,  120_000);      // 2min — atualiza métricas de backtest
clockTick();
