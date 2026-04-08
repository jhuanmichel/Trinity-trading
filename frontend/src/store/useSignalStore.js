/**
 * useSignalStore.js — Zustand global store
 * Estado centralizado para todos os dados do Trinity dashboard
 */
import { create } from 'zustand'

const useSignalStore = create((set, get) => ({
  // ── Estado principal ──────────────────────────────────────────
  signals:        [],       // histórico de sinais recentes
  currentSignal:  null,     // sinal ativo atual
  marketContext:  null,     // contexto de mercado (BTC price, trend, etc.)
  smcAnalysis:    null,     // análise SMC completa
  altcoinScan:    null,     // resultado do altcoin scanner
  crashCandidates: [],      // candidatos crash radar
  pumpCandidates:  [],      // candidatos pump radar
  fundingRates:   {},       // funding rates por symbol
  openInterest:   {},       // open interest por symbol

  // ── UI state ──────────────────────────────────────────────────
  sidebarOpen:    true,
  activeTab:      'signals',   // 'signals' | 'altcoins' | 'radar' | 'market'
  lastUpdate:     null,
  isLoading:      false,
  error:          null,

  // ── Computed ──────────────────────────────────────────────────
  getScore: () => {
    const s = get().smcAnalysis
    return s?.composite_score ?? s?.smc_score ?? 50
  },

  getDirection: () => {
    const s = get().smcAnalysis
    return s?.direction ?? s?.bias ?? 'NEUTRO'
  },

  getBtcPrice: () => {
    const mc = get().marketContext
    if (!mc) return null
    return mc.price ?? mc.btc_price ?? mc.current_price ?? null
  },

  // ── Actions ───────────────────────────────────────────────────
  setSignals:        (v) => set({ signals: v }),
  setCurrentSignal:  (v) => set({ currentSignal: v }),
  setMarketContext:  (v) => set({ marketContext: v }),
  setSmcAnalysis:    (v) => set({ smcAnalysis: v }),
  setAltcoinScan:    (v) => set({ altcoinScan: v }),
  setCrashCandidates:(v) => set({ crashCandidates: v }),
  setPumpCandidates: (v) => set({ pumpCandidates: v }),
  setFundingRates:   (v) => set({ fundingRates: v }),
  setOpenInterest:   (v) => set({ openInterest: v }),

  setSidebarOpen: (v) => set({ sidebarOpen: v }),
  toggleSidebar:  ()  => set((s) => ({ sidebarOpen: !s.sidebarOpen })),
  setActiveTab:   (v) => set({ activeTab: v }),
  setLastUpdate:  (v) => set({ lastUpdate: v }),
  setLoading:     (v) => set({ isLoading: v }),
  setError:       (v) => set({ error: v }),

  // Merge parcial de state externo (para updates batch)
  mergeState: (partial) => set(partial),
}))

export default useSignalStore
