/**
 * useTickerData.js — Preços de 10 majors via /api/funding-batch.
 *
 * Limitação atual: funding-batch não retorna change24h. Essa sessão
 * entrega delta=0.00% nos items. Sessão 3 pode expor endpoint dedicado
 * com percent change (/api/ticker-batch ou estender /api/price).
 */
import { useQuery } from '@tanstack/react-query'

const SYMBOLS = [
  'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT',
  'DOGEUSDT', 'AVAXUSDT', 'LINKUSDT', 'TONUSDT', 'ARBUSDT',
]

async function fetchBatch() {
  const q = SYMBOLS.join(',')
  const res = await fetch(`/api/funding-batch?symbols=${q}`, {
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function formatPrice(n) {
  const num = Number(n)
  if (!Number.isFinite(num) || num <= 0) return '—'
  if (num >= 1000)   return `$${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (num >= 1)      return `$${num.toFixed(4)}`
  if (num >= 0.01)   return `$${num.toFixed(4)}`
  return `$${num.toFixed(6)}`
}

export function useTickerData() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['v2', 'ticker-data'],
    queryFn: fetchBatch,
    refetchInterval: 10_000,
    staleTime: 7_000,
    retry: 1,
  })

  const results = data?.results || {}
  const tickers = SYMBOLS.map((sym) => {
    const row = results[sym] || {}
    return {
      symbol: sym.replace('USDT', ''),
      price: formatPrice(row.markPrice),
      change24h: 0, // funding-batch nao retorna change — placeholder temporario
    }
  })

  return { tickers, isLoading, error }
}
