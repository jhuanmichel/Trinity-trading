/**
 * fetchOpenInterest.js — Fetch open interest via Binance Futures
 */

const BINANCE_OI = 'https://fapi.binance.com/fapi/v1/openInterest'

/**
 * Busca open interest de um símbolo
 * @param {string} symbol — ex: 'BTCUSDT'
 * @returns {Promise<{ symbol, openInterest, value }|null>}
 */
export async function fetchOpenInterest(symbol) {
  try {
    const res = await fetch(`${BINANCE_OI}?symbol=${symbol}`, {
      signal: AbortSignal.timeout(4000),
    })
    if (!res.ok) return null
    const data = await res.json()
    return {
      symbol,
      openInterest: parseFloat(data.openInterest ?? 0),
      value:        null, // OI em USD (requer endpoint /v1/openInterestHist)
      source:       'binance',
    }
  } catch {
    return null
  }
}

/**
 * Busca OI para lista de símbolos
 */
export async function fetchMultipleOI(symbols = ['BTCUSDT']) {
  const results = await Promise.allSettled(symbols.map(fetchOpenInterest))
  const map = {}
  results.forEach((r, i) => {
    if (r.status === 'fulfilled' && r.value) {
      map[symbols[i]] = r.value
    }
  })
  return map
}

/**
 * Formata open interest para exibição
 */
export function formatOI(oi) {
  if (!oi && oi !== 0) return '—'
  if (oi >= 1_000_000) return `${(oi / 1_000_000).toFixed(2)}M`
  if (oi >= 1_000)     return `${(oi / 1_000).toFixed(2)}K`
  return oi.toFixed(2)
}
