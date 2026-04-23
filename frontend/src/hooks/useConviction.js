/**
 * useConviction.js — Consome /api/current-state e expoe extractors V2.
 * Poll 30s. QueryKey com prefixo ['v2', ...] isolado do cache V1.
 */
import { useQuery } from '@tanstack/react-query'
import {
  extractConvictionData,
  extractLayerScores,
  extractTradePlan,
} from '@/lib/backendMapping'

async function fetchState() {
  const res = await fetch('/api/current-state', {
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export function useConviction() {
  const { data, isLoading, error } = useQuery({
    queryKey: ['v2', 'conviction'],
    queryFn: fetchState,
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: 1,
  })

  return {
    conviction: extractConvictionData(data),
    layers:     extractLayerScores(data),
    tradePlan:  extractTradePlan(data),
    isLoading,
    error,
  }
}
