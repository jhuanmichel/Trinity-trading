/**
 * useRecentSignals.js — Sinais recentes (S3b).
 *
 * Tenta /api/signals/recent (pode estar bugado);
 * fallback em /api/outcomes/export filtrando ultimos N.
 */
import { useQuery } from '@tanstack/react-query'

export function useRecentSignals(limit = 50) {
  return useQuery({
    queryKey: ['v2', 'recent-signals', limit],
    queryFn: async () => {
      // Tenta endpoint oficial
      try {
        const r = await fetch(`/api/signals/recent?limit=${limit}`, {
          credentials: 'include',
        })
        if (r.ok) {
          const d = await r.json()
          if (d.signals && Array.isArray(d.signals) && d.signals.length > 0) {
            return d.signals
          }
        }
      } catch (_) {
        // fallthrough
      }

      // Fallback: outcomes/export
      const r2 = await fetch('/api/outcomes/export', { credentials: 'include' })
      if (!r2.ok) return []
      const text = await r2.text()
      const lines = text.split('\n').filter(Boolean)
      const outcomes = []
      for (const line of lines.slice(-limit * 3)) {
        try {
          const o = JSON.parse(line)
          outcomes.push(o)
        } catch (_) {}
      }
      outcomes.sort((a, b) => (b.registered_at || '').localeCompare(a.registered_at || ''))
      return outcomes.slice(0, limit)
    },
    refetchInterval: 30_000,
    staleTime: 10_000,
  })
}
