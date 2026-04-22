/**
 * AltcoinRadar.jsx — Grid de altcoins com search, filtros e modal de detalhe
 */
import React, { memo, useState, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { fmtPrice, fmtPct, pctColor } from '../../engine/formatters'
import ScoreGauge from '../ui/ScoreGauge'
import EmptyState from '../ui/EmptyState'
import './AltcoinRadar.css'

/* ── Modal de detalhe ─────────────────────────────────────────────────────── */
const AltModal = memo(({ coin, onClose }) => {
  if (!coin) return null

  const dir   = coin.direction ?? 'NEUTRO'
  const score = coin.smc_score ?? coin.score ?? 50
  const dirColor = dir === 'LONG' ? 'var(--green)' : dir === 'SHORT' ? 'var(--red)' : 'var(--text-muted)'

  return (
    <AnimatePresence>
      <motion.div
        className="alt-modal-overlay"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        onClick={e => e.target === e.currentTarget && onClose()}
      >
        <motion.div
          className="alt-modal-box"
          initial={{ opacity: 0, scale: 0.92, y: 20 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.92, y: 20 }}
          transition={{ type: 'spring', damping: 22, stiffness: 300 }}
        >
          {/* Header */}
          <div className="alt-modal-head">
            <div>
              <div className="alt-modal-symbol">
                {coin.symbol}<span className="alt-quote">/USDT</span>
              </div>
              <div className="alt-modal-pair-sub">{coin.pair}</div>
            </div>
            <div className="alt-modal-head-right">
              <span className={`badge ${dir === 'LONG' ? 'badge-green' : dir === 'SHORT' ? 'badge-red' : 'badge-dim'}`}>
                {dir}
              </span>
              <button className="alt-modal-close" onClick={onClose}>✕</button>
            </div>
          </div>

          {/* Gauge + preço */}
          <div className="alt-modal-gauge-row">
            <ScoreGauge score={score} size={140} />
            <div className="alt-modal-price-col">
              <div className="alt-modal-price">{fmtPrice(coin.price)}</div>
              {coin.change_24h !== undefined && (
                <div className="alt-modal-change" style={{ color: pctColor(coin.change_24h) }}>
                  {fmtPct(coin.change_24h)} 24h
                </div>
              )}
              <div className="alt-modal-score-label">
                SMC SCORE <span style={{ color: dirColor }}>{score}</span>
              </div>
              <div className="alt-modal-conviction">
                CONVICTION <span>{coin.conviction ?? Math.abs(score - 50)}</span>
              </div>
            </div>
          </div>

          {/* Estrutura de mercado */}
          <div className="alt-modal-section-title">ESTRUTURA DE MERCADO</div>
          <div className="alt-modal-grid">
            <div className="alt-modal-item">
              <span className="alt-modal-key">BIAS</span>
              <span className="alt-modal-val" style={{ color: dirColor }}>{coin.bias ?? '-'}</span>
            </div>
            <div className="alt-modal-item">
              <span className="alt-modal-key">STRUCTURE</span>
              <span className="alt-modal-val">{coin.structure ?? '-'}</span>
            </div>
            <div className="alt-modal-item">
              <span className="alt-modal-key">BOS ↑</span>
              <span className="alt-modal-val" style={{ color: coin.bos_bull ? 'var(--green)' : 'var(--text-muted)' }}>
                {coin.bos_bull ? 'SIM' : 'NÃO'}
              </span>
            </div>
            <div className="alt-modal-item">
              <span className="alt-modal-key">BOS ↓</span>
              <span className="alt-modal-val" style={{ color: coin.bos_bear ? 'var(--red)' : 'var(--text-muted)' }}>
                {coin.bos_bear ? 'SIM' : 'NÃO'}
              </span>
            </div>
            <div className="alt-modal-item">
              <span className="alt-modal-key">CHOCH</span>
              <span className="alt-modal-val" style={{ color: coin.choch ? 'var(--yellow)' : 'var(--text-muted)' }}>
                {coin.choch ? 'SIM' : 'NÃO'}
              </span>
            </div>
            <div className="alt-modal-item">
              <span className="alt-modal-key">OBs</span>
              <span className="alt-modal-val">{coin.ob_count ?? 0}</span>
            </div>
            <div className="alt-modal-item">
              <span className="alt-modal-key">FVGs</span>
              <span className="alt-modal-val">{coin.fvg_count ?? 0}</span>
            </div>
          </div>

          {/* Níveis */}
          {coin.entry && (
            <>
              <div className="alt-modal-section-title">NÍVEIS</div>
              <div className="alt-modal-levels">
                <div className="alt-modal-level">
                  <span className="alt-modal-level-key">ENTRY</span>
                  <span className="alt-modal-level-val text-yellow">{fmtPrice(coin.entry)}</span>
                </div>
                <div className="alt-modal-level">
                  <span className="alt-modal-level-key">STOP</span>
                  <span className="alt-modal-level-val text-red">{fmtPrice(coin.stop)}</span>
                </div>
                <div className="alt-modal-level">
                  <span className="alt-modal-level-key">TP1</span>
                  <span className="alt-modal-level-val text-green">{fmtPrice(coin.tp1)}</span>
                </div>
                <div className="alt-modal-level">
                  <span className="alt-modal-level-key">TP2</span>
                  <span className="alt-modal-level-val text-green">{fmtPrice(coin.tp2)}</span>
                </div>
              </div>
            </>
          )}
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
})

AltModal.displayName = 'AltModal'

/* ── Card individual ──────────────────────────────────────────────────────── */
const AltCard = memo(({ coin, index, onClick }) => {
  const dir   = coin.direction ?? 'NEUTRO'
  const score = coin.smc_score ?? coin.score ?? 50

  const cls = dir === 'LONG' ? 'alt-long' : dir === 'SHORT' ? 'alt-short' : 'alt-neutro'

  return (
    <motion.div
      className={`alt-card ${cls}`}
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: index * 0.04 }}
      onClick={() => onClick(coin)}
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

      {/* Entry */}
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

/* ── Radar principal ──────────────────────────────────────────────────────── */
const AltcoinRadar = memo(({ altcoinScan }) => {
  const [query,  setQuery]  = useState('')
  const [filter, setFilter] = useState('all')
  const [modal,  setModal]  = useState(null)

  const coins  = altcoinScan?.candidates ?? []
  const scanTs = altcoinScan?.scan_ts ?? null
  const total  = altcoinScan?.coins_scanned ?? 0

  const filtered = coins.filter(c => {
    const matchDir = filter === 'all' || c.direction === filter
    const matchQ   = !query || (c.symbol ?? '').toLowerCase().includes(query.toLowerCase())
    return matchDir && matchQ
  })

  const onCardClick = useCallback(coin => setModal(coin), [])
  const closeModal  = useCallback(() => setModal(null), [])

  return (
    <div className="alt-radar-wrap">
      {/* Cabeçalho */}
      <div className="alt-radar-header">
        <div className="alt-radar-title-row">
          <span className="live-dot" />
          <span className="alt-radar-title">ALTCOIN RADAR</span>
          {total > 0 && <span className="alt-radar-badge">{total} coins</span>}
        </div>
        {scanTs && <span className="alt-radar-ts">{new Date(scanTs).toLocaleTimeString()}</span>}
      </div>

      {/* Toolbar: search + filtros */}
      <div className="alt-toolbar">
        <div className="alt-search-wrap">
          <input
            className="alt-search"
            placeholder="Buscar coin..."
            value={query}
            onChange={e => setQuery(e.target.value)}
          />
          {query && (
            <button className="alt-search-clear" onClick={() => setQuery('')}>✕</button>
          )}
        </div>
        <div className="alt-filter-btns">
          {['all', 'LONG', 'SHORT'].map(f => (
            <button
              key={f}
              className={`alt-filter-btn ${filter === f ? 'active' : ''}`}
              onClick={() => setFilter(f)}
            >
              {f === 'all' ? 'TODOS' : f}
            </button>
          ))}
        </div>
      </div>

      {/* Grid */}
      {coins.length === 0 ? (
        <EmptyState
          icon="🛰️"
          title="Escaneando altcoins"
          description="O scanner está processando o mercado. Resultados aparecem aqui em instantes."
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon="🔍"
          title="Nenhuma coin encontrada"
          description="Tente ajustar os filtros ou aguardar o próximo scan."
        />
      ) : (
        <div className="alt-grid">
          {filtered.map((coin, i) => (
            <AltCard key={coin.symbol ?? i} coin={coin} index={i} onClick={onCardClick} />
          ))}
        </div>
      )}

      {/* Modal */}
      {modal && <AltModal coin={modal} onClose={closeModal} />}
    </div>
  )
})

AltcoinRadar.displayName = 'AltcoinRadar'
export default AltcoinRadar
