/**
 * MarketContext.jsx — Painel de contexto de mercado
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import { fmtPrice, fmtPct, pctColor } from '../../engine/formatters'
import { formatFunding, fundingColor } from '../../engine/fetchFunding'
import { formatOI } from '../../engine/fetchOpenInterest'
import './MarketContext.css'

const MetricCard = ({ label, value, sub, color, icon }) => (
  <div className="mc-card panel panel-sm">
    <div className="mc-card-label">
      {icon && <span>{icon}</span>}
      {label}
    </div>
    <div className="mc-card-value" style={color ? { color } : {}}>
      {value ?? '—'}
    </div>
    {sub && <div className="mc-card-sub">{sub}</div>}
  </div>
)

const MarketContext = memo(({ marketContext, fundingRates, openInterest }) => {
  const btcPrice  = marketContext?.price ?? marketContext?.btc_price ?? null
  const change24h = marketContext?.change_24h ?? null
  const volume    = marketContext?.volume_24h ?? null
  const trend     = marketContext?.trend ?? marketContext?.direction ?? '—'
  const dominance = marketContext?.btc_dominance ?? null

  const btcFunding = fundingRates?.BTCUSDT?.fundingRate ?? null
  const ethFunding = fundingRates?.ETHUSDT?.fundingRate ?? null
  const btcOI      = openInterest?.BTCUSDT?.openInterest ?? null
  const ethOI      = openInterest?.ETHUSDT?.openInterest ?? null

  return (
    <motion.div
      className="mc-wrap"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
    >
      <div className="mc-section-title">MARKET CONTEXT</div>

      <div className="mc-grid">
        <MetricCard
          label="BTC PRICE"
          value={fmtPrice(btcPrice)}
          sub={change24h !== null ? fmtPct(change24h) : null}
          color={pctColor(change24h)}
        />
        <MetricCard
          label="24H TREND"
          value={trend}
          color={trend === 'BULLISH' || trend === 'LONG' ? 'var(--green)' :
                 trend === 'BEARISH' || trend === 'SHORT' ? 'var(--red)' :
                 'var(--text-secondary)'}
        />
        {volume !== null && (
          <MetricCard
            label="VOLUME 24H"
            value={formatOI(volume)}
          />
        )}
        {dominance !== null && (
          <MetricCard
            label="BTC DOMINANCE"
            value={`${Number(dominance).toFixed(1)}%`}
          />
        )}
      </div>

      {/* Funding rates */}
      {(btcFunding !== null || ethFunding !== null) && (
        <>
          <div className="mc-sub-title">FUNDING RATES</div>
          <div className="mc-grid">
            {btcFunding !== null && (
              <MetricCard
                label="BTC FUNDING"
                value={formatFunding(btcFunding)}
                color={fundingColor(btcFunding)}
                sub={btcFunding > 0 ? 'Longs pagando' : 'Shorts pagando'}
              />
            )}
            {ethFunding !== null && (
              <MetricCard
                label="ETH FUNDING"
                value={formatFunding(ethFunding)}
                color={fundingColor(ethFunding)}
              />
            )}
            {btcOI !== null && (
              <MetricCard
                label="BTC OI"
                value={formatOI(btcOI)}
              />
            )}
          </div>
        </>
      )}

      {/* Market narrative */}
      {marketContext?.narrative && (
        <div className="mc-narrative">
          <div className="mc-narrative-label">NARRATIVE</div>
          <div className="mc-narrative-text">{marketContext.narrative}</div>
        </div>
      )}
    </motion.div>
  )
})

MarketContext.displayName = 'MarketContext'
export default MarketContext
