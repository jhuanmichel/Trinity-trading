/**
 * scoreEngine.js — Trinity Score Engine v3.0
 * Calcula score composto de convicção (0-100) a partir dos dados da API
 */

// ── Pesos por camada ─────────────────────────────────────────────────────────
const WEIGHTS = {
  market_structure: 0.20,  // BOS, CHOCH, HH/HL/LL/LH
  liquidity:        0.15,  // sweep de levels, EQH/EQL
  bos:              0.15,  // Break of Structure
  choch:            0.15,  // Change of Character
  order_blocks:     0.10,  // OBs válidos
  fvg:              0.10,  // Fair Value Gaps
  volume_confirm:   0.10,  // Volume acima da média
  momentum:         0.05,  // RSI / momentum confirmado
}

/**
 * Normaliza valor para range [0, 100]
 */
function clamp(v, min = 0, max = 100) {
  return Math.min(max, Math.max(min, v))
}

/**
 * Extrai score SMC de uma análise de mercado
 * @param {Object} analysis — objeto retornado por /api/current-state
 * @returns {number} score 0-100
 */
export function extractScore(analysis) {
  if (!analysis) return 50

  // Tenta extrair score direto
  const raw = analysis?.composite_score
    ?? analysis?.smc_score
    ?? analysis?.score
    ?? analysis?.smc_analysis?.composite_score
    ?? analysis?.smc_analysis?.score
    ?? null

  if (raw !== null && !isNaN(raw)) return clamp(Number(raw))

  // Fallback: calcula a partir dos componentes
  return computeFromComponents(analysis)
}

/**
 * Calcula score a partir de componentes individuais da resposta API
 */
function computeFromComponents(data) {
  let score = 50
  let totalWeight = 0

  const smc = data?.smc_analysis ?? data?.smcAnalysis ?? data ?? {}

  // Market structure
  const ms = smc.market_structure ?? smc.ms ?? {}
  if (ms.bias === 'BULLISH') {
    score += WEIGHTS.market_structure * 25
    totalWeight += WEIGHTS.market_structure
  } else if (ms.bias === 'BEARISH') {
    score -= WEIGHTS.market_structure * 25
    totalWeight += WEIGHTS.market_structure
  }

  // BOS
  if (smc.bos_bull || ms.bos_bull) {
    score += WEIGHTS.bos * 20
    totalWeight += WEIGHTS.bos
  } else if (smc.bos_bear || ms.bos_bear) {
    score -= WEIGHTS.bos * 20
    totalWeight += WEIGHTS.bos
  }

  // CHOCH
  if (smc.choch || ms.choch) {
    const chochDir = smc.choch_direction ?? ms.bias ?? 'BULLISH'
    if (chochDir === 'BULLISH') score += WEIGHTS.choch * 15
    else score -= WEIGHTS.choch * 15
    totalWeight += WEIGHTS.choch
  }

  // Order blocks
  const obCount = smc.ob_count ?? 0
  if (obCount > 0) {
    score += WEIGHTS.order_blocks * Math.min(obCount * 5, 20)
    totalWeight += WEIGHTS.order_blocks
  }

  // FVG
  const fvgCount = smc.fvg_count ?? 0
  if (fvgCount > 0) {
    const bullFvg = smc.bull_fvg_count ?? Math.ceil(fvgCount / 2)
    const bearFvg = smc.bear_fvg_count ?? Math.floor(fvgCount / 2)
    if (bullFvg > bearFvg) score += WEIGHTS.fvg * 15
    else score -= WEIGHTS.fvg * 15
    totalWeight += WEIGHTS.fvg
  }

  // Volume confirmation
  const volConf = smc.volume_confirmation ?? smc.volume_confirm ?? false
  if (volConf) {
    const bias = smc.bias ?? ms.bias ?? 'NEUTRO'
    if (bias === 'BULLISH') score += WEIGHTS.volume_confirm * 20
    else if (bias === 'BEARISH') score -= WEIGHTS.volume_confirm * 20
    totalWeight += WEIGHTS.volume_confirm
  }

  return clamp(Math.round(score))
}

/**
 * Retorna label de convicção baseado no score
 */
export function convictionLabel(score) {
  const s = Number(score)
  if (s >= 80) return { label: 'STRONG LONG', color: 'var(--green)' }
  if (s >= 65) return { label: 'LONG BIAS',   color: 'var(--green-dim)' }
  if (s >= 55) return { label: 'SLIGHT LONG', color: '#6dde9f' }
  if (s <= 20) return { label: 'STRONG SHORT', color: 'var(--red)' }
  if (s <= 35) return { label: 'SHORT BIAS',   color: 'var(--red-dim)' }
  if (s <= 45) return { label: 'SLIGHT SHORT', color: '#e07070' }
  return { label: 'NEUTRAL', color: 'var(--text-secondary)' }
}

/**
 * Cores do gauge semicircular por range de score
 */
export function gaugeColor(score) {
  const s = Number(score)
  if (s >= 70) return 'var(--green)'
  if (s >= 60) return '#6dde9f'
  if (s >= 45) return 'var(--yellow)'
  if (s >= 30) return '#e07070'
  return 'var(--red)'
}

/**
 * Formata score como string com 1 decimal
 */
export function fmtScore(score) {
  return Number(score).toFixed(1)
}
