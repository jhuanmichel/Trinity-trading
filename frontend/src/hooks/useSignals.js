/**
 * useSignals.js — Hook para dados principais do Trinity via React Query
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import useSignalStore from '../store/useSignalStore'

const POLL_MS = 30_000  // 30s refresh

// ── Fetchers ──────────────────────────────────────────────────────────────────

async function fetchCurrentState() {
  const res = await fetch('/api/current-state', { signal: AbortSignal.timeout(8000) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function fetchSignalHistory() {
  const res = await fetch('/api/signal-history', { signal: AbortSignal.timeout(8000) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function fetchAltcoinScan() {
  const res = await fetch('/api/altcoin-scanner', { signal: AbortSignal.timeout(10000) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function fetchCrashScan() {
  const res = await fetch('/api/crash-scan', { signal: AbortSignal.timeout(8000) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function fetchPumpScan() {
  const res = await fetch('/api/pump-scan', { signal: AbortSignal.timeout(8000) })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

/**
 * Hook principal — sincroniza /api/current-state com Zustand
 */
export function useCurrentState() {
  const { setMarketContext, setSmcAnalysis, setCurrentSignal, setLastUpdate } = useSignalStore()

  const query = useQuery({
    queryKey:  ['currentState'],
    queryFn:   fetchCurrentState,
    refetchInterval: POLL_MS,
    staleTime: POLL_MS / 2,
    retry: 2,
  })

  useEffect(() => {
    const data = query.data
    if (!data) return

    // Extrai sub-objetos (tolerante a múltiplos formatos da API)
    const mc = data.market_context ?? data.marketContext
      ?? (data.price ? data : null)
      ?? null

    // SMC analysis pode estar em vários campos
    const smc = data.smc_analysis ?? data.smcAnalysis
      ?? data.smart_money ?? data ?? null

    const sig = data.signal ?? data.current_signal ?? data.activeSignal ?? null

    if (mc)  setMarketContext(mc)
    if (smc) setSmcAnalysis(smc)
    if (sig) setCurrentSignal(sig)
    setLastUpdate(new Date().toISOString())
  }, [query.data])

  return query
}

/**
 * Hook histórico de sinais
 */
export function useSignalHistory() {
  const { setSignals } = useSignalStore()

  const query = useQuery({
    queryKey:  ['signalHistory'],
    queryFn:   fetchSignalHistory,
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: 2,
  })

  useEffect(() => {
    const data = query.data
    if (!data) return
    const list = Array.isArray(data) ? data : data?.signals ?? data?.history ?? []
    setSignals(list)
  }, [query.data])

  return query
}

/**
 * Hook altcoin scanner
 */
export function useAltcoinScan() {
  const { setAltcoinScan } = useSignalStore()

  const query = useQuery({
    queryKey:  ['altcoinScan'],
    queryFn:   fetchAltcoinScan,
    refetchInterval: 300_000,  // 5min
    staleTime: 240_000,
    retry: 1,
  })

  useEffect(() => {
    if (query.data) setAltcoinScan(query.data)
  }, [query.data])

  return query
}

/**
 * Hook crash/pump radar
 */
export function useRadar() {
  const { setCrashCandidates, setPumpCandidates } = useSignalStore()

  const crashQ = useQuery({
    queryKey:  ['crashScan'],
    queryFn:   fetchCrashScan,
    refetchInterval: 120_000,
    staleTime: 60_000,
    retry: 1,
  })

  const pumpQ = useQuery({
    queryKey:  ['pumpScan'],
    queryFn:   fetchPumpScan,
    refetchInterval: 120_000,
    staleTime: 60_000,
    retry: 1,
  })

  useEffect(() => {
    const data = crashQ.data
    if (!data) return
    const list = data.candidates ?? data.crash_candidates ?? (Array.isArray(data) ? data : [])
    setCrashCandidates(list)
  }, [crashQ.data])

  useEffect(() => {
    const data = pumpQ.data
    if (!data) return
    const list = data.candidates ?? data.pump_candidates ?? (Array.isArray(data) ? data : [])
    setPumpCandidates(list)
  }, [pumpQ.data])

  return { crashQ, pumpQ }
}
