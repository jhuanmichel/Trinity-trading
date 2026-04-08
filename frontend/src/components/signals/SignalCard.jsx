/**
 * SignalCard.jsx — Card de sinal ativo BTC/USDT
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import ScoreGauge from '../ui/ScoreGauge'
import { extractScore } from '../../engine/scoreEngine'
import { fmtPrice, fmtPct, pctColor, directionColor, fmtTime } from '../../engine/formatters'
import './SignalCard.css'

const MetricRow = ({ label, value, color }) => (
  <div className="sc-metric">
    <span className="sc-metric-label">{label}</span>
    <span className="sc-metric-value" style={color ? { color } : {}}>
      {value ?? '—'}
    </span>
  </div>
)

const SignalCard = memo(({ smcAnalysis, currentSignal, marketContext }) => {
  const score     = extractScore(smcAnalysis)
  const direction = smcAnalysis?.direction ?? smcAnalysis?.bias ?? 'NEUTRO'
  const structure = smcAnalysis?.structure ?? smcAnalysis?.market_structure?.structure ?? '?'
  const dirColor  = directionColor(direction)

  const price   = marketContext?.price ?? marketContext?.btc_price ?? null
  const change  = marketContext?.change_24h ?? marketContext?.price_change_pct_24h ?? null

  const sig     = currentSignal
  const hasSignal = sig?.valid === true || sig?.entry != null

  const obCount  = smcAnalysis?.ob_count  ?? 0
  const fvgCount = smcAnalysis?.fvg_count ?? 0
  const bosBull  = smcAnalysis?.bos_bull  ?? false
  const bosBear  = smcAnalysis?.bos_bear  ?? false
  const choch    = smcAnalysis?.choch     ?? false

  return (
    <motion.div
      className={`sc-card panel ${direction === 'LONG' || direction === 'BULLISH' ? 'sc-bull' :
                                   direction === 'SHORT' || direction === 'BEARISH' ? 'sc-bear' : ''}`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {/* Header */}
      <div className="sc-header">
        <div className="sc-pair">
          <span className="sc-symbol">BTC</span>
          <span className="sc-quote">/USDT</span>
        </div>
        <div className="sc-price-block">
          <span className="sc-price">{fmtPrice(price)}</span>
          {change !== null && (
            <span className="sc-change" style={{ color: pctColor(change) }}>
              {fmtPct(change)}
            </span>
          )}
        </div>
        <div className={`badge ${direction === 'LONG' || direction === 'BULLISH' ? 'badge-green' :
                                  direction === 'SHORT' || direction === 'BEARISH' ? 'badge-red' :
                                  'badge-dim'}`}>
          {direction}
        </div>
      </div>

      {/* Gauge */}
      <div className="sc-gauge">
        <ScoreGauge score={score} size={200} />
      </div>

      {/* SMC Layers */}
      <div className="sc-layers">
        <div className="sc-layer-title">SMC LAYERS</div>
        <div className="sc-layers-grid">
          <div className={`sc-layer-tag ${bosBull ? 'active-bull' : bosBear ? 'active-bear' : ''}`}>
            BOS {bosBull ? '↑' : bosBear ? '↓' : '—'}
          </div>
          <div className={`sc-layer-tag ${choch ? 'active-choch' : ''}`}>
            CHOCH {choch ? '✓' : '—'}
          </div>
          <div className={`sc-layer-tag ${obCount > 0 ? 'active-bull' : ''}`}>
            OB ×{obCount}
          </div>
          <div className={`sc-layer-tag ${fvgCount > 0 ? 'active-bull' : ''}`}>
            FVG ×{fvgCount}
          </div>
        </div>
      </div>

      <div className="sc-struct">
        <span className="sc-struct-label">STRUCTURE</span>
        <span className="sc-struct-value">{structure}</span>
      </div>

      {/* Signal levels */}
      {hasSignal && (
        <motion.div
          className="sc-signal"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.2 }}
        >
          <div className="sc-signal-title">
            <span className="live-dot" />
            ACTIVE SIGNAL
          </div>
          <div className="sc-levels">
            <MetricRow label="ENTRY"  value={fmtPrice(sig?.entry)} color="var(--yellow)" />
            <MetricRow label="STOP"   value={fmtPrice(sig?.stop)}  color="var(--red)" />
            <MetricRow label="TP1"    value={fmtPrice(sig?.tp1)}   color="var(--green-dim)" />
            <MetricRow label="TP2"    value={fmtPrice(sig?.tp2)}   color="var(--green)" />
          </div>
        </motion.div>
      )}
    </motion.div>
  )
})

SignalCard.displayName = 'SignalCard'
export default SignalCard
