/**
 * SignalHistory.jsx — Tabela de histórico de sinais recentes
 */
import React, { memo } from 'react'
import { motion } from 'framer-motion'
import { fmtPrice, fmtDate, directionColor } from '../../engine/formatters'
import EmptyState from '../ui/EmptyState'
import './SignalHistory.css'

const SignalHistory = memo(({ signals = [] }) => {
  if (!signals.length) {
    return (
      <EmptyState
        icon="📡"
        title="Aguardando sinais"
        description="O histórico aparecerá aqui conforme novos sinais forem registrados."
      />
    )
  }

  return (
    <div className="sh-wrap">
      <div className="sh-title">
        <span>HISTÓRICO DE SINAIS</span>
        <span className="sh-count">{signals.length} sinais</span>
      </div>
      <div className="sh-table-wrap">
        <table className="data-table sh-table">
          <thead>
            <tr>
              <th>DATA</th>
              <th>DIR</th>
              <th>SCORE</th>
              <th>ENTRY</th>
              <th>TP1</th>
              <th>STOP</th>
            </tr>
          </thead>
          <tbody>
            {signals.slice(0, 5).map((sig, i) => {
              const dir   = sig.direction ?? sig.signal?.direction ?? sig.bias ?? '?'
              const score = sig.smc_score ?? sig.composite_score ?? sig.score ?? '—'
              const entry = sig.signal?.entry ?? sig.entry ?? null
              const tp1   = sig.signal?.tp1   ?? sig.tp1   ?? null
              const stop  = sig.signal?.stop  ?? sig.stop  ?? null
              const ts    = sig.timestamp ?? sig.ts ?? null

              return (
                <motion.tr
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.05 }}
                >
                  <td className="sh-ts">{fmtDate(ts)}</td>
                  <td>
                    <span
                      className="sh-dir-badge"
                      style={{ color: directionColor(dir),
                               borderColor: directionColor(dir) + '40',
                               background: directionColor(dir) + '10' }}
                    >
                      {dir}
                    </span>
                  </td>
                  <td className="sh-score" style={{ color: directionColor(dir) }}>
                    {typeof score === 'number' ? score.toFixed(1) : score}
                  </td>
                  <td className="sh-price">{fmtPrice(entry)}</td>
                  <td className="sh-price text-green">{fmtPrice(tp1)}</td>
                  <td className="sh-price text-red">{fmtPrice(stop)}</td>
                </motion.tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
})

SignalHistory.displayName = 'SignalHistory'
export default SignalHistory
