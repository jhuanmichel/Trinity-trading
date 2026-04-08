/**
 * AltcoinRadar.jsx — Grid de altcoins scaneadas
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import { fmtPrice, fmtPct, pctColor } from '../../engine/formatters'
import './AltcoinRadar.css'

const AltCard = memo(({ coin, index }) => {
  const dir   = coin.direction ?? 'NEUTRO'
  const score = coin.smc_score ?? coin.score ?? 50
  const conv  = coin.conviction ?? Math.abs(score - 50)

  const cls = dir === 'LONG' ? 'alt-long' : dir === 'SHORT' ? 'alt-short' : 'alt-neutro'

  return (
    <motion.div
      className={`alt-card ${cls}`}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: index * 0.04 }}
    >
      <div className="alt-card-header">
        <div className="alt-symbol-wrap">
          <span className="alt-symbol">{coin.symbol ?? coin.pair ?? '?'}</span>
          <span className="alt-quote">/USDT</span>
        </div>
        <span className={`alt-dir-badge badge ${dir === 'LONG' ? 'badge-green' :
                                                 dir === 'SHORT' ? 'badge-red' : 'badge-dim'}`}>
          {dir}
        </span>
      </div>

      <div className="alt-price-row">
        <span className="alt-price">{fmtPrice(coin.price)}</span>
        {coin.change_24h !== undefined && (
          <span className="alt-change" style={{ color: pctColor(coin.change_24h) }}>
            {fmtPct(coin.change_24h)}
          </span>
        )}
      </div>

      {/* Score bar */}
      <div className="alt-score-row">
        <div className="score-bar-track alt-bar-track">
          <div
            className="score-bar-fill"
            style={{
              width: `${score}%`,
              background: dir === 'LONG' ? 'var(--green)' :
                          dir === 'SHORT' ? 'var(--red)' : 'var(--text-muted)',
            }}
          />
        </div>
        <span className="alt-score-val">{score.toFixed(0)}</span>
      </div>

      {/* Tags */}
      <div className="alt-tags">
        {coin.bos_bull && <span className="alt-tag alt-tag-bull">BOS↑</span>}
        {coin.bos_bear && <span className="alt-tag alt-tag-bear">BOS↓</span>}
        {coin.choch    && <span className="alt-tag alt-tag-choch">CHOCH</span>}
        {coin.ob_count > 0 && <span className="alt-tag alt-tag-dim">OB×{coin.ob_count}</span>}
        {coin.fvg_count > 0 && <span className="alt-tag alt-tag-dim">FVG×{coin.fvg_count}</span>}
      </div>

      {/* Entry if exists */}
      {coin.entry && (
        <div className="alt-entry">
          <span className="alt-entry-label">ENTRY</span>
          <span className="alt-entry-val text-yellow">{fmtPrice(coin.entry)}</span>
        </div>
      )}
    </motion.div>
  )
})

AltCard.displayName = 'AltCard'

const AltcoinRadar = memo(({ altcoinScan }) => {
  const coins  = altcoinScan?.candidates ?? []
  const scanTs = altcoinScan?.scan_ts ?? null
  const total  = altcoinScan?.coins_scanned ?? 0

  return (
    <div className="alt-radar-wrap">
      <div className="alt-radar-header">
        <div className="alt-radar-title-row">
          <span className="live-dot" />
          <span className="alt-radar-title">ALTCOIN RADAR</span>
          {total > 0 && <span className="alt-radar-badge">{total} coins</span>}
        </div>
        {scanTs && <span className="alt-radar-ts">{new Date(scanTs).toLocaleTimeString()}</span>}
      </div>

      {coins.length === 0 ? (
        <div className="alt-empty">
          <span className="spinner" />
          <span>Escaneando altcoins...</span>
        </div>
      ) : (
        <div className="alt-grid">
          {coins.map((coin, i) => (
            <AltCard key={coin.symbol ?? i} coin={coin} index={i} />
          ))}
        </div>
      )}
    </div>
  )
})

AltcoinRadar.displayName = 'AltcoinRadar'
export default AltcoinRadar
