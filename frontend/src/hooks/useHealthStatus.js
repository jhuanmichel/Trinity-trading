/**
 * useHealthStatus.js — Latência e status do backend /health.
 */
import { useQuery } from '@tanstack/react-query'

async function probeHealth() {
  const start = Date.now()
  try {
    const res = await fetch('/health', {
      signal: AbortSignal.timeout(6000),
    })
    const latency = Date.now() - start
    return {
      apiLatency: latency,
      apiStatus: res.ok ? 'OK' : 'DEGRADED',
      wsStatus: 'OK', // placeholder — WebSocket nao ativo ainda
    }
  } catch {
    return {
      apiLatency: Date.now() - start,
      apiStatus: 'ERROR',
      wsStatus: 'N/A',
    }
  }
}

export function useHealthStatus() {
  const { data } = useQuery({
    queryKey: ['v2', 'health'],
    queryFn: probeHealth,
    refetchInterval: 15_000,
    staleTime: 10_000,
    retry: 0,
  })

  return data || { apiLatency: null, apiStatus: '...', wsStatus: '...' }
}
