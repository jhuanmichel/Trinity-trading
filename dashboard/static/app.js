/* ── QuantDesk — Dashboard Application ───────────────────────────────────── */

const REFRESH_MS = 30_000;
const PRICE_MS   = 1_000;

let _prevBtcPrice = null;
let _scoreHistory = [];   // [{score, ts}]
let _sparkTf      = '1m';
let _liqLevels    = [];   // liquidation heatmap levels
let _liqApiError  = null; // última mensagem de erro da API de liquidações
let _liqImgUrl    = null; // URL da screenshot Apify (fallback quando sem dados estruturados)

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

// ── Sparkline ─────────────────────────────────────────────────────────────

function renderSparklineSVG(candles) {
  if (!candles || candles.length < 2) {
    return '<div class="spark-loading">Carregando gráfico...</div>';
  }
  const w = 400, h = 76, px = 4, py = 6;
  const closes = candles.map(c => c.c);
  const highs  = candles.map(c => c.h);
  const lows   = candles.map(c => c.l);
  const minP   = Math.min(...lows);
  const maxP   = Math.max(...highs);
  const range  = maxP - minP || 1;
  const xS = i => px + (i / (candles.length - 1)) * (w - px * 2);
  const yS = p => py + (h - py * 2) - ((p - minP) / range) * (h - py * 2);

  const pathD = closes.map((c, i) => `${i === 0 ? 'M' : 'L'}${xS(i).toFixed(1)},${yS(c).toFixed(1)}`).join(' ');
  const fillD = `${pathD} L${xS(candles.length-1).toFixed(1)},${h} L${px},${h} Z`;

  const isUp = closes[closes.length-1] >= closes[0];
  const col  = isUp ? 'var(--green)' : 'var(--red)';
  const lastX = xS(candles.length-1).toFixed(1);
  const lastY = yS(closes[closes.length-1]).toFixed(1);

  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${h}px;display:block">
    <defs>
      <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${col}" stop-opacity="0.18"/>
        <stop offset="100%" stop-color="${col}" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <path d="${fillD}" fill="url(#sg)"/>
    <path d="${pathD}" fill="none" stroke="${col}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${lastX}" cy="${lastY}" r="2.5" fill="${col}"/>
  </svg>
  <div class="spark-labels">
    <span class="spark-low">$${Math.round(minP).toLocaleString('en-US')}</span>
    <span class="spark-high">$${Math.round(maxP).toLocaleString('en-US')}</span>
  </div>`;
}

async function updateSparkline() {
  const el = document.getElementById('sparklineChart');
  if (!el) return;
  try {
    const candles = await fetch(`/api/candles?interval=${_sparkTf}&limit=60`).then(r => r.json());
    if (!Array.isArray(candles)) return;
    el.innerHTML = renderSparklineSVG(candles);
  } catch(_) {}
}

function onSparkTf(tf) {
  _sparkTf = tf;
  document.querySelectorAll('.spark-tf-btn').forEach(b => b.classList.toggle('active', b.dataset.tf === tf));
  updateSparkline();
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
  tbody.innerHTML = signals.map(sig => {
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
  const urgColor = crashUrgencyColor(c.urgency);
  const emoji    = crashUrgencyEmoji(c.urgency);
  const pctStr   = c.price_change_pct >= 0
    ? `<span class="c-green">+${c.price_change_pct.toFixed(1)}%</span>`
    : `<span class="c-red">${c.price_change_pct.toFixed(1)}%</span>`;
  const priceStr = c.price < 10
    ? `$${c.price.toFixed(4)}`
    : `$${c.price.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})}`;
  const comp   = c.component_scores || {};
  const compBars = [
    renderComponentBar('Liquidez',    comp.liquidity    || 0),
    renderComponentBar('Alavancagem', comp.leverage     || 0),
    renderComponentBar('Whale Dump',  comp.whale        || 0),
    renderComponentBar('Compressão',  comp.compression  || 0),
    renderComponentBar('Funding/OI',  comp.funding_oi   || 0),
  ].join('');
  const signals = (c.top_signals || []).slice(0, 3)
    .map(s => `<div class="cr-signal-row">• ${s}</div>`).join('');
  const ddStr = c.estimated_drawdown
    ? `<span class="cr-dd">Cascata est.: -${c.estimated_drawdown.toFixed(1)}%</span>` : '';

  return `<div class="crash-card" style="border-left:3px solid ${urgColor}">
    <div class="cr-header">
      <div>
        <span class="cr-symbol">${c.symbol.replace('USDT','')}</span><span class="cr-usdt">USDT</span>
        <span class="cr-price">${priceStr}</span> ${pctStr}
      </div>
      <div class="cr-badge" style="background:${urgColor}22;color:${urgColor};border:1px solid ${urgColor}44">${emoji} ${c.urgency}</div>
    </div>
    <div class="cr-score-row">
      <div>${fmtCrashScore(c.crash_score)}</div>
      <div class="cr-meta"><div class="cr-prob" style="color:${urgColor}">${c.crash_probability}</div>${ddStr}</div>
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
    if (grid)  grid.innerHTML  = '<div class="cr-empty">Scanner em execução — aguardando primeiro ciclo (±30s)...</div>';
    if (badge) { badge.textContent = 'INICIANDO'; badge.className = 'crash-live-badge'; }
    return;
  }

  const ts       = data.scan_ts ? new Date(data.scan_ts).toLocaleTimeString('pt-BR') : '—';
  const duration = data.scan_duration_s ? `${data.scan_duration_s.toFixed(1)}s` : '—';
  const critical = data.candidates.filter(c => c.crash_score >= 80).length;
  const danger   = data.candidates.filter(c => c.crash_score >= 60 && c.crash_score < 80).length;

  if (meta) {
    meta.innerHTML = `
      <span class="c-muted">Último scan: <b>${ts}</b> em ${duration}</span>
      <span class="c-muted">${data.coins_scanned || 0} moedas</span>
      ${critical ? `<span class="cr-count-badge cr-count-critical">🚨 ${critical} CRÍTICO</span>` : ''}
      ${danger   ? `<span class="cr-count-badge cr-count-danger">⚠️ ${danger} DANGER</span>` : ''}
    `;
  }

  if (badge) {
    if (critical)     { badge.textContent = 'CRÍTICO'; badge.className = 'crash-live-badge badge-critical'; }
    else if (danger)  { badge.textContent = 'DANGER';  badge.className = 'crash-live-badge badge-danger';   }
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
  const urgColor = pumpUrgencyColor(c.urgency);
  const pct      = c.price_change_pct >= 0 ? `+${c.price_change_pct.toFixed(1)}%` : `${c.price_change_pct.toFixed(1)}%`;
  const pctColor = c.price_change_pct >= 0 ? 'var(--green)' : 'var(--red)';
  const price    = c.price < 10 ? c.price.toFixed(4) : c.price.toFixed(2);
  const comp     = c.component_scores || {};
  const signals  = (c.top_signals || []).slice(0, 3).map(s => `<div class="pr-signal-row">▸ ${s}</div>`).join('');
  const targetStr = c.pump_target && c.pump_target > c.price
    ? `<div class="pr-target">🎯 Alvo: $${c.pump_target < 10 ? c.pump_target.toFixed(4) : c.pump_target.toFixed(2)}</div>`
    : '';

  return `<div class="pump-card" style="border-left:3px solid ${urgColor}">
    <div class="pr-header">
      <span class="pr-symbol">${c.symbol.replace('USDT','')}</span>
      <span class="pr-badge" style="background:${urgColor}20;color:${urgColor};border:1px solid ${urgColor}40">
        ${pumpUrgencyEmoji(c.urgency)} ${c.urgency}
      </span>
    </div>
    <div class="pr-score-row">
      <span style="color:var(--text-muted);font-size:11px">PUMP SCORE</span>
      <span style="font-size:22px;font-weight:700;color:${urgColor}">${fmtPumpScore(c.pump_score)}<span style="font-size:12px;color:var(--text-muted)">/100</span></span>
      <span style="color:${pctColor};font-size:13px">${pct}</span>
      <span style="color:var(--text-muted);font-size:12px">$${price}</span>
    </div>
    <div class="pr-comps">
      ${renderPumpComponentBar('Whale',  comp.whale || 0)}
      ${renderPumpComponentBar('Squeeze',comp.squeeze || 0)}
      ${renderPumpComponentBar('Gravity',comp.gravity || 0)}
      ${renderPumpComponentBar('Breakout',comp.breakout || 0)}
      ${renderPumpComponentBar('SmartMoney',comp.smart_money || 0)}
    </div>
    ${signals ? `<div class="pr-signals">${signals}</div>` : ''}
    ${targetStr}
    <div class="pr-action">${c.recommended_action || ''}</div>
  </div>`;
}

function renderPumpRadar(data) {
  const badge   = document.getElementById('pumpScanBadge');
  const metaBar = document.getElementById('pumpMetaBar');
  const grid    = document.getElementById('pumpGrid');

  const candidates = data.candidates || [];
  const scanTs     = data.scan_ts ? new Date(data.scan_ts).toLocaleTimeString('pt-BR') : '--:--';
  const count      = candidates.length;
  const launchCount = candidates.filter(c => c.urgency === 'LAUNCH').length;
  const readyCount  = candidates.filter(c => c.urgency === 'READY').length;

  if (badge) {
    if (launchCount > 0) {
      badge.className = 'pump-live-badge badge-launch';
      badge.textContent = `🚀 ${launchCount} LAUNCH`;
    } else if (readyCount > 0) {
      badge.className = 'pump-live-badge badge-ready';
      badge.textContent = `⚡ ${readyCount} READY`;
    } else {
      badge.className = 'pump-live-badge badge-active';
      badge.textContent = count > 0 ? `LIVE · ${count}` : 'INICIANDO';
    }
  }

  if (metaBar) {
    const top = candidates[0];
    const topStr = top
      ? `<span class="pr-count-badge ${launchCount > 0 ? 'crit' : 'norm'}">${count} candidatos</span>
         &nbsp;Top: <strong>${top.symbol.replace('USDT','')}</strong> pump_score <strong>${top.pump_score.toFixed(0)}</strong>
         · <span style="color:var(--text-muted)">scan ${data.scan_duration_s || 0}s</span>
         · <span style="color:var(--text-muted)">atualizado ${scanTs}</span>`
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

// Init
refresh();
priceTick();
updateLiqHeatmap();
updateLiqScreenshot();                          // busca URL da screenshot Apify
updateCrashRadar();                             // Crash Radar — primeiro carregamento
updatePumpRadar();                              // Pump Radar — primeiro carregamento
setInterval(refresh,             REFRESH_MS);
setInterval(priceTick,           PRICE_MS);
setInterval(clockTick,           1000);
setInterval(updateSparkline,     30_000);
setInterval(updateLiqHeatmap,    300_000);      // 5min — respeita rate limit Coinglass
setInterval(updateLiqScreenshot, 300_000);      // 5min — sincroniza com ciclo do servidor
setInterval(updateCrashRadar,    CRASH_REFRESH_MS); // 30s — sincroniza com ciclo do scanner
setInterval(updatePumpRadar,     PUMP_REFRESH_MS);  // 30s — sincroniza com ciclo do scanner
clockTick();
