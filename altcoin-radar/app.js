/**
 * app.js — Altcoin Radar frontend logic (vanilla JS)
 */

const API = window.location.hostname === '127.0.0.1' || window.location.hostname === 'localhost'
  ? 'http://localhost:5000'
  : '';   // mesmo host em produção

// ── Estado global ─────────────────────────────────────────────────────────────
let _allSignals   = [];   // lista completa vinda do /api/signals
let _activeSym    = null; // symbol exibido no card
let _countdown    = 0;    // segundos até próxima análise
let _cTimer       = null; // setInterval do countdown
let _pollSignals  = null; // setInterval de polling de chips
let _pollCoin     = null; // setInterval de re-fetch silencioso da coin ativa

// ── DOM refs ─────────────────────────────────────────────────────────────────
const elInput      = document.getElementById('search-input');
const elAC         = document.getElementById('autocomplete');
const elChips      = document.getElementById('chips');
const elEmpty      = document.getElementById('state-empty');
const elLoading    = document.getElementById('state-loading');
const elError      = document.getElementById('state-error');
const elResult     = document.getElementById('state-result');
const elErrorMsg   = document.getElementById('error-msg');
const elNextScan   = document.getElementById('next-scan-timer');
const elRetry      = document.getElementById('btn-retry');
const elRefresh    = document.getElementById('btn-refresh');
const elAnalyze    = document.getElementById('btn-analyze');

// ── Formatadores ─────────────────────────────────────────────────────────────
function fmtPrice(n) {
  if (n === null || n === undefined) return '—';
  if (Math.abs(n) < 0.001) return n.toFixed(6);
  if (Math.abs(n) < 1)     return n.toFixed(4);
  if (Math.abs(n) < 100)   return n.toFixed(2);
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtVolume(n) {
  if (!n) return '—';
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  return n.toFixed(0);
}

function fmtPct(n, decimals = 2) {
  if (n === null || n === undefined) return '—';
  const sign = n >= 0 ? '+' : '';
  return sign + n.toFixed(decimals) + '%';
}

function fmtCountdown(secs) {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function scoreClass(score) {
  if (score >= 65) return 'high';
  if (score >= 50) return 'mid';
  return 'low';
}

function scoreLabel(score) {
  if (score >= 75) return 'Sinal forte';
  if (score >= 65) return 'Confiança alta';
  if (score >= 55) return 'Moderado';
  if (score >= 45) return 'Fraco';
  return 'Sem convicção';
}

// ── Funding squares ───────────────────────────────────────────────────────────
function renderFundingSquares(score) {
  // 5 quadradinhos: índices 0→-2, 1→-1, 2→0, 3→+1, 4→+2
  // score range: -2 a +2
  const labels = ['–2', '–1', '0', '+1', '+2'];
  let html = '';
  for (let i = 0; i < 5; i++) {
    const val = i - 2;  // -2 … +2
    let cls = '';
    if (val === score) {
      if (score > 0)  cls = 'pos';
      else if (score < 0) cls = 'neg';
      else cls = 'zero';
    }
    html += `<div class="fsq ${cls}" title="${labels[i]}"></div>`;
  }
  return html;
}

function fundingLabelText(score) {
  const map = { 2: 'Confirma forte', 1: 'Favorável', 0: 'Neutro', '-1': 'Contra sinal', '-2': 'Bloquear' };
  return map[String(score)] ?? 'Neutro';
}

// ── History mini chart ────────────────────────────────────────────────────────
function renderHistoryBars(history, direction) {
  if (!history || history.length === 0) return '<div style="color:var(--text-dim);font-size:11px">sem histórico</div>';
  const max = Math.max(...history.map(Math.abs)) || 0.001;
  return history.map(val => {
    const pct  = Math.max(8, Math.abs(val) / max * 100);
    const col  = val > 0 ? 'var(--red)' : val < 0 ? 'var(--green)' : 'var(--text-dim)';
    const tip  = fmtPct(val, 4);
    return `<div class="h-bar" style="height:${pct}%;background:${col}" title="${tip}"></div>`;
  }).join('');
}

// ── Estados da UI ─────────────────────────────────────────────────────────────
function showState(state) {
  elEmpty.style.display   = state === 'empty'   ? 'block' : 'none';
  elLoading.style.display = state === 'loading' ? 'block' : 'none';
  elError.style.display   = state === 'error'   ? 'block' : 'none';
  elResult.style.display  = state === 'result'  ? 'block' : 'none';
}

// ── Chips (estado vazio) ──────────────────────────────────────────────────────
function renderChips(signals) {
  // top 7 por score desc
  const top = [...signals].sort((a, b) => b.score - a.score).slice(0, 7);
  elChips.innerHTML = top.map(s =>
    `<div class="chip" onclick="analyzeSymbol('${s.symbol}')">${s.symbol}</div>`
  ).join('');
}

// ── Autocomplete ──────────────────────────────────────────────────────────────
function renderAutocomplete(query) {
  if (!query || query.length < 1) {
    elAC.classList.remove('open');
    return;
  }
  const q = query.toUpperCase();
  const matches = _allSignals.filter(s =>
    s.symbol.startsWith(q) || s.symbol.includes(q) || s.pair?.includes(q)
  ).slice(0, 6);

  if (matches.length === 0) {
    elAC.classList.remove('open');
    return;
  }

  elAC.innerHTML = matches.map(s => {
    const dirCls = (s.direction || 'neutro').toLowerCase();
    return `
      <div class="ac-item" onclick="analyzeSymbol('${s.symbol}')">
        <span class="ac-sym">${s.symbol}</span>
        <span class="ac-dir ${dirCls}">${s.direction || 'NEUTRO'}</span>
        <span class="ac-score">Score ${s.score ?? '—'}</span>
      </div>`;
  }).join('');
  elAC.classList.add('open');
}

function closeAutocomplete() {
  setTimeout(() => elAC.classList.remove('open'), 150);
}

// ── Fetch /api/signals ────────────────────────────────────────────────────────
async function fetchAllSignals() {
  try {
    const r = await fetch(`${API}/api/signals`);
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    _allSignals = data.signals ?? [];
    renderChips(_allSignals);
    // Re-render autocomplete se estiver aberto
    if (elInput.value) renderAutocomplete(elInput.value);
  } catch (e) {
    // Tenta carregar do signals.json local
    try {
      const r2 = await fetch('./data/signals.json');
      const d2 = await r2.json();
      _allSignals = d2.signals ?? [];
      renderChips(_allSignals);
    } catch (_) {}
  }
}

// ── Render card de resultado ──────────────────────────────────────────────────
function renderResult(sig, funding) {
  const dir       = (sig.direction || 'NEUTRO').toLowerCase();
  const dirUp     = sig.direction || 'NEUTRO';
  const scoreVal  = sig.score ?? 50;
  const scoreCls  = scoreClass(scoreVal);
  const changeCls = (sig.change24h ?? 0) >= 0 ? 'up' : 'down';
  const fs        = sig.fundingScore ?? funding?.score ?? 0;
  const fr        = sig.funding ?? funding?.funding ?? 0;
  const history   = funding?.history ?? [];

  document.getElementById('c-name').textContent    = sig.symbol;
  document.getElementById('c-pair').textContent    = `${sig.pair ?? sig.symbol + 'USDT'} · Perpétuo`;
  document.getElementById('c-price').textContent   = fmtPrice(sig.price);
  document.getElementById('c-change').textContent  = fmtPct(sig.change24h ?? 0);
  document.getElementById('c-change').className    = `price-change ${changeCls}`;
  document.getElementById('c-vol').textContent     = fmtVolume(sig.volume);
  document.getElementById('c-fr').textContent      = fmtPct(fr, 4);
  document.getElementById('c-oi').textContent      = fmtVolume(sig.openInterest);
  document.getElementById('c-entry').textContent   = fmtPrice(sig.entry);
  document.getElementById('c-sl').textContent      = fmtPrice(sig.sl);
  document.getElementById('c-tp1').textContent     = fmtPrice(sig.tp1);
  document.getElementById('c-tp2').textContent     = fmtPrice(sig.tp2);

  // Badge de direção
  const badge = document.getElementById('c-dir-badge');
  badge.textContent = dirUp;
  badge.className   = `dir-badge ${dir}`;

  // Funding squares
  document.getElementById('c-fsq').innerHTML         = renderFundingSquares(fs);
  document.getElementById('c-funding-label').textContent = fundingLabelText(fs);
  document.getElementById('c-funding-rate').textContent  = `Funding: ${fmtPct(fr, 4)}`;

  // Score bar
  const sEl  = document.getElementById('c-score-num');
  const sBar = document.getElementById('c-score-bar');
  const sTxt = document.getElementById('c-score-txt');
  sEl.textContent  = scoreVal;
  sEl.className    = `score-num ${scoreCls}`;
  sBar.style.width = `${scoreVal}%`;
  sBar.className   = `score-bar-fill ${scoreCls}`;
  sTxt.textContent = scoreLabel(scoreVal);

  // SMC tags
  const smcEl = document.getElementById('c-smc-tags');
  let tags = '';
  if (sig.bos_bull)   tags += `<span class="smc-tag bull">BOS ↑</span>`;
  if (sig.bos_bear)   tags += `<span class="smc-tag bear">BOS ↓</span>`;
  if (sig.choch)      tags += `<span class="smc-tag choch">CHOCH</span>`;
  if ((sig.ob_count ?? 0) > 0)  tags += `<span class="smc-tag dim">OB ×${sig.ob_count}</span>`;
  if ((sig.fvg_count ?? 0) > 0) tags += `<span class="smc-tag dim">FVG ×${sig.fvg_count}</span>`;
  if (sig.bias) tags += `<span class="smc-tag ${sig.bias === 'BULLISH' ? 'bull' : sig.bias === 'BEARISH' ? 'bear' : 'dim'}">${sig.bias}</span>`;
  smcEl.innerHTML = tags || `<span class="smc-tag dim">Sem tags SMC</span>`;

  // Histórico funding
  const histEl = document.getElementById('c-history-bars');
  histEl.innerHTML = renderHistoryBars(history, dirUp);

  // Countdown
  startCountdown(sig.nextScan ?? 300);
}

// ── Análise principal ─────────────────────────────────────────────────────────
async function analyzeSymbol(symbol) {
  if (!symbol) return;
  const sym = symbol.toUpperCase().trim();
  elInput.value = sym;
  elAC.classList.remove('open');
  _activeSym = sym;

  showState('loading');

  try {
    // Busca signal e funding em paralelo
    const [sigRes, fundRes] = await Promise.allSettled([
      fetch(`${API}/api/signal/${sym}`),
      fetch(`${API}/api/funding/${sym}`),
    ]);

    let sig = null, funding = null;

    if (sigRes.status === 'fulfilled' && sigRes.value.ok) {
      sig = await sigRes.value.json();
    } else {
      // Fallback: pega do cache local
      sig = _allSignals.find(s => s.symbol === sym) ?? null;
    }

    if (!sig) {
      // Tenta fallback direto no signals.json
      const fr = await fetch('./data/signals.json');
      const fd = await fr.json();
      sig = (fd.signals ?? []).find(s => s.symbol === sym) ?? null;
    }

    if (!sig) throw new Error(`Coin ${sym} não encontrada nos sinais ativos`);

    if (fundRes.status === 'fulfilled' && fundRes.value.ok) {
      funding = await fundRes.value.json();
    }

    renderResult(sig, funding);
    showState('result');

    // Polling silencioso a cada 60s
    clearInterval(_pollCoin);
    _pollCoin = setInterval(() => refreshActiveCoin(), 60_000);

  } catch (err) {
    elErrorMsg.textContent = err.message ?? 'Erro ao buscar dados. Verifique o backend.';
    showState('error');
  }
}

async function refreshActiveCoin() {
  if (!_activeSym) return;
  try {
    const [sigRes, fundRes] = await Promise.allSettled([
      fetch(`${API}/api/signal/${_activeSym}`),
      fetch(`${API}/api/funding/${_activeSym}`),
    ]);
    let sig = null, funding = null;
    if (sigRes.status === 'fulfilled' && sigRes.value.ok) sig = await sigRes.value.json();
    if (fundRes.status === 'fulfilled' && fundRes.value.ok) funding = await fundRes.value.json();
    if (sig) renderResult(sig, funding);
  } catch (_) {}
}

// ── Countdown ─────────────────────────────────────────────────────────────────
function startCountdown(secs) {
  clearInterval(_cTimer);
  _countdown = secs;
  _updateCountdown();
  _cTimer = setInterval(() => {
    _countdown = Math.max(0, _countdown - 1);
    _updateCountdown();
    if (_countdown === 0) refreshActiveCoin();
  }, 1000);
}

function _updateCountdown() {
  if (elNextScan) elNextScan.textContent = fmtCountdown(_countdown);
}

// ── Event listeners ───────────────────────────────────────────────────────────
elInput.addEventListener('input', () => renderAutocomplete(elInput.value));
elInput.addEventListener('blur',  closeAutocomplete);
elInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const first = elAC.querySelector('.ac-item');
    if (first) first.click();
    else analyzeSymbol(elInput.value);
  }
  if (e.key === 'Escape') elAC.classList.remove('open');
});

elAnalyze?.addEventListener('click', () => analyzeSymbol(elInput.value));
elRetry?.addEventListener('click',   () => _activeSym && analyzeSymbol(_activeSym));
elRefresh?.addEventListener('click', () => _activeSym && analyzeSymbol(_activeSym));

// Fecha autocomplete ao clicar fora
document.addEventListener('click', e => {
  if (!e.target.closest('.search-wrap')) elAC.classList.remove('open');
});

// ── Init ──────────────────────────────────────────────────────────────────────
(async function init() {
  showState('empty');
  await fetchAllSignals();
  // Polling a cada 30s para atualizar chips
  _pollSignals = setInterval(fetchAllSignals, 30_000);
})();

// ── Expõe para o HTML ─────────────────────────────────────────────────────────
window.analyzeSymbol = analyzeSymbol;
