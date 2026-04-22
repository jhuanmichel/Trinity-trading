/**
 * RadarBase.jsx — Radar parametrizado por direcao (bull|bear).
 *
 * Dedupe de CrashRadar/PumpRadar (eram 95% identicos, 73 linhas cada).
 * VARIANTS map encapsula todas diferencas: cor, titulo, classe, slide,
 * bar width, tag BOS, empty state icon+titulo.
 *
 * Nao substitui AltcoinRadar (estrutura distinta, 292 linhas).
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import { fmtPrice, fmtPct } from '../../engine/formatters'
import EmptyState from '../ui/EmptyState'
import './RadarShared.css'

const VARIANTS = {
  bull: {
    title:        'PUMP RADAR',
    dotClass:     'live-dot',
    itemClass:    'radar-item radar-item-bull',
    scoreColor:   'var(--green)',
    textClass:    'text-green',
    initialX:     8,
    barWidth:     (s) => `${s}%`,
    bosKey:       'bos_bull',
    bosTag:       'BOS↑',
    bosTagClass:  'radar-tag radar-tag-bull',
    emptyIcon:    '🔺',
    emptyTitle:   'Nenhum pump detectado',
    emptyDesc:    'Radar ativo — sinais aparecem quando score bull atinge threshold.',
  },
  bear: {
    title:        'CRASH RADAR',
    dotClass:     'live-dot red',
    itemClass:    'radar-item radar-item-bear',
    scoreColor:   'var(--red)',
    textClass:    'text-red',
    initialX:     -8,
    barWidth:     (s) => `${Math.max(0, 100 - s)}%`,
    bosKey:       'bos_bear',
    bosTag:       'BOS↓',
    bosTagClass:  'radar-tag radar-tag-bear',
    emptyIcon:    '🔻',
    emptyTitle:   'Nenhum crash detectado',
    emptyDesc:    'Radar ativo — sinais aparecem quando score bear atinge threshold.',
  },
}

const RadarBase = memo(({ direction, candidates = [], scanTs }) => {
  const v = VARIANTS[direction]
  if (!v) return null

  return (
    <div className="radar-wrap">
      <div className="radar-header">
        <div className="radar-title-row">
          <span className={v.dotClass} />
          <span className="radar-title">{v.title}</span>
        </div>
        {scanTs && <span className="radar-ts">{new Date(scanTs).toLocaleTimeString()}</span>}
      </div>

      {candidates.length === 0 ? (
        <EmptyState icon={v.emptyIcon} title={v.emptyTitle} description={v.emptyDesc} compact />
      ) : (
        <div className="radar-list">
          {candidates.map((c, i) => {
            const score = c.score ?? c.smc_score ?? 50
            return (
              <motion.div
                key={c.symbol ?? i}
                className={v.itemClass}
                initial={{ opacity: 0, x: v.initialX }}
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
                      style={{ width: v.barWidth(score), background: v.scoreColor }}
                    />
                  </div>
                  <span className={`radar-score ${v.textClass}`}>{score.toFixed(0)}</span>
                </div>

                <div className="radar-item-right">
                  <span className="radar-price">{fmtPrice(c.price)}</span>
                  {c.change_24h !== undefined && (
                    <span className={`radar-change ${v.textClass}`}>{fmtPct(c.change_24h)}</span>
                  )}
                </div>

                <div className="radar-item-tags">
                  {c[v.bosKey] && <span className={v.bosTagClass}>{v.bosTag}</span>}
                  {c.choch    && <span className="radar-tag radar-tag-choch">CHOCH</span>}
                  {c.ob_count > 0 && <span className="radar-tag radar-tag-dim">OB×{c.ob_count}</span>}
                </div>
              </motion.div>
            )
          })}
        </div>
      )}
    </div>
  )
})

RadarBase.displayName = 'RadarBase'
export default RadarBase
