/**
 * fetchFunding.js — Fetch funding rates via backend proxy.
 * Browser nao pode chamar fapi.binance.com / contract.mexc.com direto (CORS).
 * /api/funding/{symbol} no backend lida com Binance + fallback MEXC + cache 30s.
 */

/**
 * Busca funding rate para um símbolo
 * @param {string} symbol — ex: 'BTCUSDT'
 * @returns {Promise<{ symbol, fundingRate, nextFundingTime, source }|null>}
 */
export async function fetchFundingRate(symbol) {
  try {
    const res = await fetch(`/api/funding/${symbol}`, {
      signal: AbortSignal.timeout(6000),
    })
    if (!res.ok) return null
    const data = await res.json()
    if (data.error) return null
    return {
      symbol:          data.symbol ?? symbol,
      fundingRate:     parseFloat(data.fundingRate ?? 0),
      nextFundingTime: data.nextFundingTime ?? null,
      source:          data.source ?? 'binance',
    }
  } catch {
    return null
  }
}

/**
 * Busca funding rates para múltiplos símbolos em paralelo
 * @param {string[]} symbols
 * @returns {Promise<Object>} — map { symbol: { fundingRate, ... } }
 */
export async function fetchMultipleFunding(symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']) {
  const results = await Promise.allSettled(symbols.map(fetchFundingRate))
  const map = {}
  results.forEach((r, i) => {
    if (r.status === 'fulfilled' && r.value) {
      map[symbols[i]] = r.value
    }
  })
  return map
}

/**
 * Formata funding rate para exibição
 * @param {number} rate — ex: 0.0001
 * @returns {string} — ex: '+0.01%'
 */
export function formatFunding(rate) {
  if (rate === null || rate === undefined) return '—'
  const pct = (rate * 100).toFixed(4)
  return rate >= 0 ? `+${pct}%` : `${pct}%`
}

/**
 * Cor do funding rate
 */
export function fundingColor(rate) {
  if (!rate && rate !== 0) return 'var(--text-muted)'
  if (rate > 0.001)  return 'var(--red)'     // longs pagando muito
  if (rate > 0.0003) return 'var(--yellow)'
  if (rate < -0.001) return 'var(--green)'   // shorts pagando muito
  return 'var(--text-secondary)'
}
