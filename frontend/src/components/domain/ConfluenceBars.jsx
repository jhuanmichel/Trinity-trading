/**
 * ConfluenceBars.jsx — 6 barras bidirecionais (visual mockup).
 * Center split: valor 0.5 = neutro, <0.5 fill esquerda (short), >0.5 fill direita (long).
 *
 * Props:
 *   rows?: [{ l: 'Market Structure', v: 0.72, t: 'LONG', tone: 'long' }]
 *          - se ausente usa demo hardcoded do mockup.
 */
import { T } from '@/styles/tokens'

const DEMO_ROWS = [
  { l: 'Market Structure', v: 0.72, t: 'LONG',   tone: 'long' },
  { l: 'Liquidity',        v: 0.50, t: 'NEUTRO', tone: 'amber' },
  { l: 'Volume',           v: 0.28, t: 'SHORT',  tone: 'short' },
  { l: 'Trend',            v: 0.78, t: 'LONG',   tone: 'long' },
  { l: 'Correlation',      v: 0.55, t: 'NEUTRO', tone: 'amber' },
  { l: 'Volatility',       v: 0.66, t: 'LONG',   tone: 'long' },
]

function toneColor(tone) {
  if (tone === 'long')  return T.long
  if (tone === 'short') return T.short
  return T.amber
}

export function ConfluenceBars({ rows }) {
  const data = Array.isArray(rows) && rows.length ? rows : DEMO_ROWS
  return (
    <div>
      {data.map((r, i) => {
        const color = toneColor(r.tone)
        const v = Math.max(0, Math.min(1, Number(r.v) || 0))
        return (
          <div
            key={i}
            style={{
              display: 'grid',
              gridTemplateColumns: '140px 1fr 64px',
              alignItems: 'center',
              gap: 14,
              padding: '10px 0',
              borderBottom: i < data.length - 1 ? `1px solid ${T.border}` : 'none',
            }}
          >
            <span className="t-mono" style={{ fontSize: 11, color: T.text }}>{r.l}</span>
            <div style={{
              position: 'relative',
              height: 6,
              background: T.surface2,
              borderRadius: 1,
            }}>
              <div style={{
                position: 'absolute',
                top: 0,
                bottom: 0,
                left: v < 0.5 ? `${v * 100}%` : '50%',
                width: `${Math.abs(v - 0.5) * 100}%`,
                background: color,
                borderRadius: 1,
              }} />
              <div style={{
                position: 'absolute',
                left: '50%',
                top: -2,
                bottom: -2,
                width: 1,
                background: T.borderHi,
              }} />
            </div>
            <span className="t-mono" style={{
              fontSize: 10, color, letterSpacing: '0.14em', textAlign: 'right',
            }}>{r.t}</span>
          </div>
        )
      })}
    </div>
  )
}
