/**
 * Header.jsx — Top navigation bar
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import useSignalStore from '../../store/useSignalStore'
import { fmtPrice, fmtPct, pctColor, fmtTime } from '../../engine/formatters'
import { extractScore, convictionLabel, gaugeColor } from '../../engine/scoreEngine'
import './Header.css'

const Header = memo(() => {
  const { toggleSidebar, marketContext, smcAnalysis, lastUpdate, currentSignal } = useSignalStore()

  const btcPrice  = marketContext?.price ?? marketContext?.btc_price ?? null
  const btcChange = marketContext?.change_24h ?? marketContext?.price_change_pct_24h ?? null
  const score     = extractScore(smcAnalysis)
  const conv      = convictionLabel(score)
  const sColor    = gaugeColor(score)
  const direction = smcAnalysis?.direction ?? smcAnalysis?.bias ?? 'NEUTRO'

  return (
    <motion.header
      className="hdr"
      initial={{ y: -60, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      {/* Left: menu + brand */}
      <div className="hdr-left">
        <button className="hdr-menu-btn" onClick={toggleSidebar} title="Toggle sidebar">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor">
            <rect y="2" width="18" height="2" rx="1"/>
            <rect y="8" width="18" height="2" rx="1"/>
            <rect y="14" width="18" height="2" rx="1"/>
          </svg>
        </button>

        <div className="hdr-brand">
          <span className="hdr-brand-tri">TRINITY</span>
          <span className="hdr-brand-sep">/</span>
          <span className="hdr-brand-sub">SIGNAL ENGINE</span>
        </div>

        <div className="hdr-live">
          <span className="live-dot" />
          <span className="hdr-live-label">LIVE</span>
        </div>
      </div>

      {/* Center: key metrics */}
      <div className="hdr-center">
        {/* BTC price */}
        <div className="hdr-metric">
          <span className="hdr-metric-label">BTC</span>
          <span className="hdr-metric-value">{fmtPrice(btcPrice)}</span>
          {btcChange !== null && (
            <span className="hdr-metric-sub" style={{ color: pctColor(btcChange) }}>
              {fmtPct(btcChange)}
            </span>
          )}
        </div>

        <div className="hdr-divider" />

        {/* SMC Score */}
        <div className="hdr-metric">
          <span className="hdr-metric-label">SMC</span>
          <span className="hdr-metric-value" style={{ color: sColor }}>
            {score.toFixed(0)}
          </span>
          <span className="hdr-metric-sub" style={{ color: sColor, opacity: 0.8 }}>
            {conv.label}
          </span>
        </div>

        <div className="hdr-divider" />

        {/* Direction */}
        <div className="hdr-metric">
          <span className="hdr-metric-label">BIAS</span>
          <span
            className="hdr-metric-value"
            style={{ color: direction === 'BULLISH' || direction === 'LONG' ? 'var(--green)' :
                           direction === 'BEARISH' || direction === 'SHORT' ? 'var(--red)' :
                           'var(--text-secondary)' }}
          >
            {direction}
          </span>
        </div>

        {/* Active signal entry */}
        {currentSignal?.valid && currentSignal?.entry && (
          <>
            <div className="hdr-divider" />
            <div className="hdr-metric">
              <span className="hdr-metric-label">ENTRY</span>
              <span className="hdr-metric-value text-yellow">{fmtPrice(currentSignal.entry)}</span>
            </div>
          </>
        )}
      </div>

      {/* Right: timestamp */}
      <div className="hdr-right">
        <div className="hdr-update">
          <span className="hdr-update-label">LAST UPDATE</span>
          <span className="hdr-update-time">{fmtTime(lastUpdate) || '——:——:——'}</span>
        </div>
      </div>
    </motion.header>
  )
})

Header.displayName = 'Header'
export default Header
