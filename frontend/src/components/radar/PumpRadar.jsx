/**
 * PumpRadar.jsx — Candidatos de pump (bull signals)
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import { fmtPrice, fmtPct } from '../../engine/formatters'
import EmptyState from '../ui/EmptyState'
import './RadarShared.css'

const PumpRadar = memo(({ candidates = [], scanTs }) => {
  return (
    <div className="radar-wrap">
      <div className="radar-header">
        <div className="radar-title-row">
          <span className="live-dot" />
          <span className="radar-title">PUMP RADAR</span>
        </div>
        {scanTs && <span className="radar-ts">{new Date(scanTs).toLocaleTimeString()}</span>}
      </div>

      {candidates.length === 0 ? (
        <EmptyState
          icon="🔺"
          title="Nenhum pump detectado"
          description="Radar ativo — sinais aparecem quando score bull atinge threshold."
          compact
        />
      ) : (
        <div className="radar-list">
          {candidates.map((c, i) => (
            <motion.div
              key={c.symbol ?? i}
              className="radar-item radar-item-bull"
              initial={{ opacity: 0, x: 8 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ delay: i * 0.06 }}
            >
              <div className="radar-item-left">
                <span className="radar-symbol">{c.symbol ?? '?'}</span>
                <span className="radar-pair">/USDT</span>
              </div>

              <div className="radar-item-center">
                <div className="score-bar-track" style={{ width: 80 }}>
                  <div
                    className="score-bar-fill"
                    style={{
                      width: `${c.score ?? c.smc_score ?? 50}%`,
                      background: 'var(--green)',
                    }}
                  />
                </div>
                <span className="radar-score text-green">
                  {(c.score ?? c.smc_score ?? 50).toFixed(0)}
                </span>
              </div>

              <div className="radar-item-right">
                <span className="radar-price">{fmtPrice(c.price)}</span>
                {c.change_24h !== undefined && (
                  <span className="radar-change text-green">{fmtPct(c.change_24h)}</span>
                )}
              </div>

              <div className="radar-item-tags">
                {c.bos_bull && <span className="radar-tag radar-tag-bull">BOS↑</span>}
                {c.choch    && <span className="radar-tag radar-tag-choch">CHOCH</span>}
                {c.ob_count > 0 && <span className="radar-tag radar-tag-dim">OB×{c.ob_count}</span>}
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  )
})

PumpRadar.displayName = 'PumpRadar'
export default PumpRadar
