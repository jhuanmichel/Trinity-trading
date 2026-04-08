/**
 * fetchFunding.js — Fetch funding rates via MEXC / Binance fallback
 */

const BINANCE_PREMIUM = 'https://fapi.binance.com/fapi/v1/premiumIndex'
const MEXC_FUNDING    = 'https://contract.mexc.com/api/v1/contract/funding_rate'

/**
 * Busca funding rate para um símbolo
 * @param {string} symbol — ex: 'BTCUSDT'
 * @returns {Promise<{ symbol, fundingRate, nextFundingTime }|null>}
 */
export async function fetchFundingRate(symbol) {
  // Tenta Binance primeiro (mais confiável para futuros)
  try {
    const res = await fetch(`${BINANCE_PREMIUM}?symbol=${symbol}`, {
      signal: AbortSignal.timeout(4000),
    })
    if (res.ok) {
      const data = await res.json()
      return {
        symbol,
        fundingRate:     parseFloat(data.lastFundingRate ?? 0),
        nextFundingTime: data.nextFundingTime ?? null,
        source:          'binance',
      }
    }
  } catch {/* fallthrough */}

  // Fallback MEXC
  try {
    const mexcSym = symbol.replace('USDT', '_USDT')
    const res = await fetch(`${MEXC_FUNDING}/${mexcSym}`, {
      signal: AbortSignal.timeout(4000),
    })
    if (res.ok) {
      const data = await res.json()
      const rate  = data?.data?.fundingRate ?? 0
      return {
        symbol,
        fundingRate:     parseFloat(rate),
        nextFundingTime: null,
        source:          'mexc',
      }
    }
  } catch {/* ignore */}

  return null
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
