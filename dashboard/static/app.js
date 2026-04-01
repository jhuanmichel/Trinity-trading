/* ── QuantDesk — Dashboard Application ───────────────────────────────────── */

const REFRESH_MS  = 30_000;
const PRICE_MS    = 1_000;

let _prevBtcPrice = null;

// ── Helpers ───────────────────────────────────────────────────────────────

function scoreColor(s) {
  if (s >= 75) return 'var(--green)';
  if (s >= 60) return 'var(--yellow)';
  if (s >= 50) return 'var(--text-muted)';
  return 'var(--red)';
}

function fmtPrice(p) {
  if (p == null) return '—';
  return '$' + Number(p).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPct(v, decimals = 1) {
  if (v == null) return '—';
  return (v > 0 ? '+' : '') + Number(v).toFixed(decimals) + '%';
}

function fmtTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
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

function dirBadge(dir) {
  if (!dir || dir === 'AGUARDANDO') return `<span class="dir-badge dir-wait">— AGUARDANDO</span>`;
  if (dir.includes('LONG'))         return `<span class="dir-badge dir-long">▲ LONG</span>`;
  if (dir.includes('SHORT'))        return `<span class="dir-badge dir-short">▼ SHORT</span>`;
  return `<span class="dir-badge dir-wait">— NEUTRO</span>`;
}

function dirClass(dir) {
  if (!dir) return 'c-muted';
  if (dir.includes('LONG') || dir === 'BULLISH' || dir.includes('LONG FAVORÁVEL')) return 'c-green';
  if (dir.includes('SHORT') || dir === 'BEARISH' || dir.includes('SHORT FAVORÁVEL')) return 'c-red';
  return 'c-muted';
}

function biasClass(bias) {
  if (!bias) return 'c-muted';
  if (bias === 'BULLISH' || bias.includes('LONG')) return 'c-green';
  if (bias === 'BEARISH' || bias.includes('SHORT')) return 'c-red';
  return 'c-muted';
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
    {
      label: 'BTC',
      value: fmtPct(btc.btc_change),
      desc:  btc.btc_change >= 0 ? 'Alta 24h' : 'Queda 24h',
      bull:  btc.btc_change >= 0,
    },
    {
      label: 'ETH',
      value: fmtPct(btc.eth_change),
      desc:  btc.eth_change >= 0 ? 'Alta 24h' : 'Queda 24h',
      bull:  btc.eth_change >= 0,
    },
    {
      label: 'BTC DOMINANCE',
      value: (btc.btc_dominance || 0).toFixed(1) + '%',
      desc:  btc.btc_dominance > 55 ? 'Capital em BTC' : 'Distribuído',
      bull:  btc.btc_dominance > 55,
    },
    {
      label: 'USDT DOMINANCE',
      value: (btc.usdt_dominance || 0).toFixed(1) + '%',
      desc:  btc.usdt_dominance > 8 ? 'Fuga detectada' : 'Normal',
      bull:  btc.usdt_dominance <= 8,
    },
  ];

  grid.innerHTML = items.map(i => `
    <div class="radar-item">
      <span class="radar-label">${i.label}</span>
      <span class="radar-value ${i.bull ? 'c-green' : 'c-red'}">${i.value}</span>
      <span class="radar-desc ${i.bull ? 'c-green' : 'c-red'}">${i.desc}</span>
    </div>
  `).join('');

  const sc  = scoreColor(score);
  const dir = btc.direction || 'AGUARDANDO';

  status.innerHTML = `
    <div class="radar-bias-left">
      <span class="radar-bias-tag" style="color:${sc}">${dir}</span>
      <span class="radar-bias-detail">${btc.correlation_bias || ''}</span>
    </div>
    <div class="radar-score-right">
      <span class="radar-score-num" style="color:${sc}">${score.toFixed(0)}</span>
      <span class="radar-score-denom">/100</span>
    </div>
  `;
}

// ── Section 2: Cards ──────────────────────────────────────────────────────

function renderBTCCard(data) {
  if (!data || data.status === 'no_data') {
    return `
      <div class="asset-card">
        <div class="card-stripe" style="background:var(--surface2)"></div>
        <div class="card-header">
          <div class="card-meta">
            <span class="card-name">BTC / USDT</span>
            <span class="card-price" id="btcPriceLive">—</span>
            <div class="price-ticker-row">
              <span class="price-tick" id="btcPriceTick">
                <span class="tick-dot" id="btcTickDot"></span>
                <span class="tick-val" id="btcTickVal">—</span>
              </span>
            </div>
          </div>
        </div>
        <div style="padding:48px;text-align:center;color:var(--text-muted);font-size:12px;">
          Aguardando análise institucional...
        </div>
      </div>`;
  }

  const btc       = data.btc;
  const score     = btc.inst_score || 50;
  const sc        = scoreColor(score);
  const breakdown = btc.breakdown || {};
  const layers    = btc.layer_scores || {};

  const stripeColor = score >= 60 ? 'var(--green)' : score <= 40 ? 'var(--red)' : 'var(--yellow)';

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
    return `
      <div class="layer-row">
        <span class="layer-name">${l.label}</span>
        <div class="layer-track">
          <div class="layer-fill" style="width:${s}%;background:${scoreColor(s)}"></div>
        </div>
        <span class="layer-bias ${biasClass(bias)}">${bias}</span>
      </div>`;
  }).join('');

  // Market structure detail line
  const msDetail = [
    btc.bos_bull  ? '🟢 BOS Bull' : '',
    btc.bos_bear  ? '🔴 BOS Bear' : '',
    btc.choch     ? '⚡ CHOCH'    : '',
    btc.sweep_low ? '↓ Sweep'    : '',
    btc.sweep_high? '↑ Sweep'    : '',
  ].filter(Boolean).join(' · ') || '—';

  const dirClass = btc.direction?.includes('LONG') ? 'dir-long' : btc.direction?.includes('SHORT') ? 'dir-short' : '';

  return `
    <div class="asset-card ${dirClass}">
      <div class="card-stripe" style="background:${stripeColor}"></div>

      <div class="card-header">
        <div class="card-meta">
          <span class="card-name">BTC / USDT</span>
          <span class="card-price" id="btcPriceLive">${fmtPrice(btc.price)}</span>
          <div class="price-ticker-row">
            <span class="price-tick" id="btcPriceTick">
              <span class="tick-dot" id="btcTickDot"></span>
              <span class="tick-val" id="btcTickVal">—</span>
            </span>
            <span class="card-updated">${timeAgo(data.last_updated)}</span>
          </div>
        </div>
        ${dirBadge(btc.direction)}
      </div>

      <div class="card-score">
        <div class="score-row">
          <div>
            <span class="score-number" style="color:${sc}">${score.toFixed(0)}</span><span class="score-denom">/100</span>
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

      <div class="card-levels">
        <div class="level-cell">
          <span class="level-lbl">Entry</span>
          <span class="level-val val-entry">${fmtPrice(btc.entry)}</span>
        </div>
        <div class="level-cell">
          <span class="level-lbl">Stop Loss</span>
          <span class="level-val val-stop">${fmtPrice(btc.stop)}</span>
        </div>
        <div class="level-cell">
          <span class="level-lbl">TP 1</span>
          <span class="level-val val-tp">${fmtPrice(btc.tp1)}</span>
        </div>
        <div class="level-cell">
          <span class="level-lbl">TP 2</span>
          <span class="level-val val-tp">${fmtPrice(btc.tp2)}</span>
        </div>
        <div class="level-cell">
          <span class="level-lbl">TP 3</span>
          <span class="level-val val-tp">${fmtPrice(btc.tp3)}</span>
        </div>
        <div class="level-cell">
          <span class="level-lbl">ATR</span>
          <span class="level-val c-muted">${(btc.atr_pct || 0).toFixed(2)}%${btc.squeeze ? ' 🔥' : ''}</span>
        </div>
      </div>

      <div class="card-layers">
        <div class="layers-header">Score por Camada</div>
        ${layerRows}
      </div>
    </div>`;
}

function renderETHCard() {
  return `
    <div class="coming-soon">
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

    return `
      <tr>
        <td class="c-muted">${fmtDateTime(sig.timestamp)}</td>
        <td style="font-weight:600">BTC</td>
        <td>
          <span class="dir-badge ${isL ? 'dir-long' : isS ? 'dir-short' : 'dir-wait'}" style="font-size:10px;padding:3px 10px">
            ${isL ? '▲ LONG' : isS ? '▼ SHORT' : dir}
          </span>
        </td>
        <td>
          <span class="score-pill" style="background:${sc}22;color:${sc};border:1px solid ${sc}44">
            ${score.toFixed(0)}/100
          </span>
        </td>
        <td class="c-yellow">${sig.entry ? fmtPrice(sig.entry) : '—'}</td>
        <td class="c-red">${sig.stop ? fmtPrice(sig.stop) : '—'}</td>
        <td class="c-muted">${sig.confluences || 0}/6</td>
        <td><span class="status-tag status-monitoring">Monitorando</span></td>
      </tr>`;
  }).join('');
}

// ── Real-time Price Ticker ─────────────────────────────────────────────────

function fmtDelta(d) {
  const abs  = Math.abs(d);
  const sign = d >= 0 ? '+' : '-';
  return sign + '$' + abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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

    // Update price text
    priceEl.textContent = fmtPrice(price);

    if (_prevBtcPrice !== null && price !== _prevBtcPrice) {
      const delta = price - _prevBtcPrice;
      const pct   = (delta / _prevBtcPrice) * 100;
      const up    = delta > 0;

      // Flash the price number
      priceEl.classList.remove('flash-up', 'flash-down');
      void priceEl.offsetWidth;                    // force reflow to restart animation
      priceEl.classList.add(up ? 'flash-up' : 'flash-down');

      // Tick badge
      dotEl.className = 'tick-dot ' + (up ? 'up' : 'down');
      valEl.className = 'tick-val ' + (up ? 'up' : 'down');
      valEl.textContent = fmtDelta(delta) + '  ' + fmtPct(pct, 3) + '  (1s)';

      // Reset dot after 900ms
      setTimeout(() => {
        dotEl.className = 'tick-dot';
        valEl.className = 'tick-val';
      }, 900);

    } else if (_prevBtcPrice === null) {
      // First tick — show 24h change
      const c24 = data.change_24h;
      if (c24 != null) {
        const up24 = c24 >= 0;
        dotEl.className = 'tick-dot ' + (up24 ? 'up' : 'down');
        valEl.className = 'tick-val ' + (up24 ? 'up' : 'down');
        valEl.textContent = fmtPct(c24) + '  (24h)';
      }
    }

    _prevBtcPrice = price;
  } catch (_) {
    // silent — keeps working if API is slow
  }
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

    document.getElementById('cardsGrid').innerHTML =
      renderBTCCard(state) + renderETHCard();

    renderHistory(signals);

    const now = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    document.getElementById('updateTime').textContent = `Atualizado ${now}`;

  } catch (e) {
    console.error('[QuantDesk] fetch error:', e);
  }
}

function clockTick() {
  const el = document.getElementById('footerClock');
  if (el) el.textContent = new Date().toLocaleTimeString('pt-BR');
}

// Init
refresh();
priceTick();
setInterval(refresh,    REFRESH_MS);
setInterval(priceTick,  PRICE_MS);
setInterval(clockTick,  1000);
clockTick();
