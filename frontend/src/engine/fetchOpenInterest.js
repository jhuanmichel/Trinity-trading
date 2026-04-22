/**
 * fetchOpenInterest.js — Fetch open interest via backend proxy.
 * Browser nao pode chamar fapi.binance.com direto (CORS).
 * /api/open-interest/{symbol} backend: Binance + fallback MEXC + cache 30s.
 */

/**
 * Busca open interest de um símbolo
 * @param {string} symbol — ex: 'BTCUSDT'
 * @returns {Promise<{ symbol, openInterest, value, source }|null>}
 */
export async function fetchOpenInterest(symbol) {
  try {
    const res = await fetch(`/api/open-interest/${symbol}`, {
      signal: AbortSignal.timeout(6000),
    })
    if (!res.ok) return null
    const data = await res.json()
    if (data.error) return null
    return {
      symbol:       data.symbol ?? symbol,
      openInterest: parseFloat(data.openInterest ?? 0),
      value:        null,
      source:       data.source ?? 'binance',
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
