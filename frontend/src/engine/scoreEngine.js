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
 * Calcula score a partir de componentes individuais da resposta API.
 *
 * Cada componente contribui +/-50 ponderado pelo seu peso. Normaliza
 * dividindo o delta pela soma dos pesos ativos, garantindo range -50..+50
 * (score final 0..100). Antes: `totalWeight` acumulado mas nunca aplicado,
 * tetando score em ~66. `liquidity` e `momentum` agora aplicados.
 */
function computeFromComponents(data) {
  const smc = data?.smc_analysis ?? data?.smcAnalysis ?? data ?? {}
  const ms  = smc.market_structure ?? smc.ms ?? {}

  let weightedDelta = 0
  let activeWeight  = 0

  const apply = (weight, direction) => {
    if (direction === 'bull')      { weightedDelta += 50 * weight; activeWeight += weight }
    else if (direction === 'bear') { weightedDelta -= 50 * weight; activeWeight += weight }
  }

  // market_structure
  if (ms.bias === 'BULLISH')      apply(WEIGHTS.market_structure, 'bull')
  else if (ms.bias === 'BEARISH') apply(WEIGHTS.market_structure, 'bear')

  // liquidity (agora implementado — antes ignorado)
  if (smc.liquidity_sweep === 'bullish' || smc.liquidity_sweep_bull) apply(WEIGHTS.liquidity, 'bull')
  else if (smc.liquidity_sweep === 'bearish' || smc.liquidity_sweep_bear) apply(WEIGHTS.liquidity, 'bear')

  // BOS
  if (smc.bos_bull || ms.bos_bull) apply(WEIGHTS.bos, 'bull')
  else if (smc.bos_bear || ms.bos_bear) apply(WEIGHTS.bos, 'bear')

  // CHOCH
  if (smc.choch || ms.choch) {
    const dir = (smc.choch_direction ?? ms.bias ?? 'BULLISH').toString().toUpperCase()
    apply(WEIGHTS.choch, dir === 'BEARISH' ? 'bear' : 'bull')
  }

  // order_blocks — intensidade proporcional (satura em 3+ OBs)
  const obCount = smc.ob_count ?? 0
  if (obCount > 0) {
    const intensity = Math.min(obCount / 3, 1)
    const dir = (smc.ob_direction ?? smc.bias ?? ms.bias ?? 'BULLISH').toString().toUpperCase()
    const sign = dir === 'BEARISH' ? -1 : 1
    weightedDelta += sign * 50 * WEIGHTS.order_blocks * intensity
    activeWeight  += WEIGHTS.order_blocks
  }

  // FVG
  const fvgCount = smc.fvg_count ?? 0
  if (fvgCount > 0) {
    const bullFvg = smc.bull_fvg_count ?? Math.ceil(fvgCount / 2)
    const bearFvg = smc.bear_fvg_count ?? Math.floor(fvgCount / 2)
    apply(WEIGHTS.fvg, bullFvg > bearFvg ? 'bull' : 'bear')
  }

  // volume_confirm
  const volConf = smc.volume_confirmation ?? smc.volume_confirm ?? false
  if (volConf) {
    const bias = (smc.bias ?? ms.bias ?? 'NEUTRO').toString().toUpperCase()
    if (bias === 'BULLISH')      apply(WEIGHTS.volume_confirm, 'bull')
    else if (bias === 'BEARISH') apply(WEIGHTS.volume_confirm, 'bear')
  }

  // momentum (agora implementado — antes ignorado)
  const momentum = (smc.momentum ?? smc.rsi_momentum ?? '').toString().toLowerCase()
  if (momentum === 'bullish') apply(WEIGHTS.momentum, 'bull')
  else if (momentum === 'bearish') apply(WEIGHTS.momentum, 'bear')

  if (activeWeight === 0) return 50
  const delta = weightedDelta / activeWeight
  return clamp(Math.round(50 + delta))
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
