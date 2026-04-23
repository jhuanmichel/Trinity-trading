/**
 * App.jsx — Trinity Trading Dashboard v3.0
 * Root component: layout + routing por tabs
 */
import React, { memo } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Toaster } from 'react-hot-toast'

// Layout
import Header from './components/layout/Header'
import Sidebar from './components/layout/Sidebar'

// Signals tab
import SignalCard from './components/signals/SignalCard'
import SignalHistory from './components/signals/SignalHistory'

// Radar tabs
import CrashRadar from './components/radar/CrashRadar'
import PumpRadar from './components/radar/PumpRadar'
import AltcoinRadar from './components/radar/AltcoinRadar'

// Market context
import MarketContext from './components/charts/MarketContext'

// UI primitives
import ErrorBoundary from './components/ui/ErrorBoundary'

// V2 design system showcase (rota ?showcase=1 — nao afeta dashboard normal)
import { Showcase } from './screens/Showcase'

// V2 Shell + router (rota ?v2=1 — isolado, nao afeta dashboard normal)
import { Shell } from './components/shell/Shell'
import { useRouterStore, ROUTES } from './store/routerStore'

// V2 Screens (S3a: Dashboard real + 3 placeholders)
import { Dashboard as V2Dashboard } from './screens/Dashboard'
import { Signals  as V2Signals }   from './screens/Signals'
import { Landing  as V2Landing }   from './screens/Landing'
import { Settings as V2Settings }  from './screens/Settings'

// Store + hooks
import useSignalStore from './store/useSignalStore'
import useMarketContext from './hooks/useMarketContext'
import { useKeyboard } from './hooks/useKeyboard'

import './App.css'

// ── Tab views ──────────────────────────────────────────────────────────────────

const SignalsView = memo(() => {
  const { smcAnalysis, currentSignal, marketContext, signals } = useSignalStore()
  return (
    <div className="app-signals-layout">
      <div className="app-col-main">
        <SignalCard
          smcAnalysis={smcAnalysis}
          currentSignal={currentSignal}
          marketContext={marketContext}
        />
      </div>
      <div className="app-col-side">
        <SignalHistory signals={signals} />
      </div>
    </div>
  )
})

const AltcoinsView = memo(() => {
  const { altcoinScan } = useSignalStore()
  return (
    <div className="app-view">
      <AltcoinRadar altcoinScan={altcoinScan} />
    </div>
  )
})

const RadarView = memo(() => {
  const { crashCandidates, pumpCandidates } = useSignalStore()
  return (
    <div className="app-radar-layout">
      <div className="app-col">
        <CrashRadar candidates={crashCandidates} />
      </div>
      <div className="app-col">
        <PumpRadar candidates={pumpCandidates} />
      </div>
    </div>
  )
})

const MarketView = memo(() => {
  const { marketContext, fundingRates, openInterest } = useSignalStore()
  return (
    <div className="app-view">
      <MarketContext
        marketContext={marketContext}
        fundingRates={fundingRates}
        openInterest={openInterest}
      />
    </div>
  )
})

const TAB_VIEWS = {
  signals:  <ErrorBoundary label="Signals — erro ao carregar"><SignalsView /></ErrorBoundary>,
  altcoins: <ErrorBoundary label="Altcoins — erro ao carregar"><AltcoinsView /></ErrorBoundary>,
  radar:    <ErrorBoundary label="Radar — erro ao carregar"><RadarView /></ErrorBoundary>,
  market:   <ErrorBoundary label="Market — erro ao carregar"><MarketView /></ErrorBoundary>,
}

// ── Root ───────────────────────────────────────────────────────────────────────

export default function App() {
  // V2 Design System Showcase — gate via query string (?showcase=1)
  // Bypassa dashboard normal, nao consome hooks/polls. Valida primitives isolados.
  if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('showcase') === '1') {
    return <Showcase />
  }

  // V2 Shell — gate via query string (?v2=1)
  // Shell institucional (TopBar + Ticker + Footer) envolvendo placeholder.
  // Sessao 3 substituira o placeholder por screens reais.
  if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('v2') === '1') {
    return <ShellV2Wrapper />
  }

  const { activeTab, sidebarOpen } = useSignalStore()

  // Inicializa todos os polls de dados
  const { isLoading } = useMarketContext()

  // Keyboard shortcuts
  useKeyboard()

  return (
    <div className={`app-root ${sidebarOpen ? 'sidebar-open' : 'sidebar-closed'}`}>
      <Header />
      <Sidebar />

      <main className="app-main">
        {isLoading && (
          <div className="app-loading">
            <span className="spinner" />
            <span>Conectando ao Trinity Engine...</span>
          </div>
        )}

        <AnimatePresence mode="wait">
          <motion.div
            key={activeTab}
            className="app-tab-content"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.18 }}
          >
            {TAB_VIEWS[activeTab] ?? TAB_VIEWS.signals}
          </motion.div>
        </AnimatePresence>
      </main>

      {/* Toast notifications */}
      <Toaster
        position="bottom-right"
        toastOptions={{
          duration: 3000,
          className: 'trinity-toast',
          style: {
            background: 'var(--bg-elevated)',
            border: '1px solid var(--bg-border)',
            color: 'var(--text-primary)',
            fontFamily: 'var(--font-mono)',
            fontSize: '12px',
            borderRadius: '6px',
          },
        }}
      />
    </div>
  )
}

// ── V2 Shell wrapper (?v2=1 gate) ────────────────────────────────────────────
// Isolado do dashboard legacy. Nao chama useSignalStore/useMarketContext/
// useKeyboard — so os hooks V2 (dentro da Shell). Placeholder no body ate
// Sessao 3 prover as screens reais.

function ShellV2Wrapper() {
  const route = useRouterStore((s) => s.route)
  let content
  switch (route) {
    case ROUTES.DASHBOARD: content = <V2Dashboard />; break
    case ROUTES.SIGNALS:   content = <V2Signals   />; break
    case ROUTES.LANDING:   content = <V2Landing   />; break
    case ROUTES.SETTINGS:  content = <V2Settings  />; break
    default:               content = <V2Dashboard />; break
  }
  return <Shell>{content}</Shell>
}
