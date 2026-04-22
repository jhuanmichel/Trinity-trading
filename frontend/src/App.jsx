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
