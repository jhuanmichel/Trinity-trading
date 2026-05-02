/**
 * backendMapping.js — Extractors puros do shape /api/current-state.
 *
 * Backend retorna as 6 camadas com nomes ja alinhados ao design V2
 * (market_structure/liquidity/volume/trend/correlation/volatility),
 * entao nao ha traducao — apenas normalizacao defensiva.
 */

export function extractConvictionData(state) {
  const btc = state?.btc || {}
  return {
    score:           Number(btc.inst_score) || 0,
    direction:       btc.direction || 'NEUTRO',
    confluences:     Number(btc.confluences) || 0,
    marketStructure: btc.market_structure || 'LATERAL',
    bosBull:         !!btc.bos_bull,
    bosBear:         !!btc.bos_bear,
    choch:           !!btc.choch,
    sweepHigh:       !!btc.sweep_high,
    sweepLow:        !!btc.sweep_low,
    strength:        btc.strength || '',
    valid:           !!btc.valid,
    price:           Number(btc.price) || 0,
  }
}

export function extractLayerScores(state) {
  const ls = state?.btc?.layer_scores || {}
  return {
    market_structure: Number(ls.market_structure) || 50,
    liquidity:        Number(ls.liquidity)        || 50,
    volume:           Number(ls.volume)           || 50,
    trend:            Number(ls.trend)            || 50,
    correlation:      Number(ls.correlation)      || 50,
    volatility:       Number(ls.volatility)       || 50,
  }
}

export function extractTradePlan(state) {
  const btc = state?.btc || {}
  return {
    entry:   Number(btc.entry) || 0,
    stop:    Number(btc.stop)  || 0,
    tp1:     Number(btc.tp1)   || 0,
    tp2:     Number(btc.tp2)   || 0,
    tp3:     Number(btc.tp3)   || 0,
    atr_pct: Number(btc.atr_pct) || 0,
  }
}

/** Metadata do state (last_updated, last_error) para indicador de freshness. */
export function extractMeta(state) {
  return {
    lastUpdated: state?.last_updated || null,
    lastError:   state?.last_error || null,
  }
}

/**
 * Tone semantico pra valor 0-100.
 * >= 65 → long (verde)
 * 35-64 → amber (ambiguo)
 * < 35  → short (vermelho)
 */
export function toneForScore(score) {
  const n = Number(score) || 0
  if (n >= 65) return 'long'
  if (n >= 35) return 'amber'
  return 'short'
}

// Labels abreviadas das 6 camadas (design V2)
export const LAYER_LABELS = {
  market_structure: 'STR',
  liquidity:        'LIQ',
  volume:           'VOL',
  trend:            'TRD',
  correlation:      'COR',
  volatility:       'VTY',
}

// Ordem canonica para render
export const LAYER_ORDER = [
  'market_structure', 'liquidity', 'volume',
  'trend', 'correlation', 'volatility',
]
