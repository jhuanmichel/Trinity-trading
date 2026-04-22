/**
 * useMacroStatus.js — Mapeia /api/btc-regime/detailed em BULL|BEAR|NEUTRO.
 */
import { useQuery } from '@tanstack/react-query'

async function fetchRegime() {
  const res = await fetch('/api/btc-regime/detailed', {
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function mapRegime(raw) {
  if (!raw) return 'NEUTRO'
  const r = String(raw).toUpperCase()
  if (r.includes('BULL')) return 'BULL'
  if (r.includes('BEAR')) return 'BEAR'
  return 'NEUTRO'
}

export function useMacroStatus() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['v2', 'macro-status'],
    queryFn: fetchRegime,
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: 1,
  })

  return {
    macroStatus: mapRegime(data?.regime),
    isLoading,
    error,
  }
}
