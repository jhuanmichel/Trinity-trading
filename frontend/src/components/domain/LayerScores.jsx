/**
 * LayerScores.jsx — 4 timeframes (1D/4H/1H/15M) com top-border colorido.
 * Visual fiel ao mockup Trinity Redesign.
 *
 * Props:
 *   rows?: [{ l: '1D', t: 'BEARISH', tone: 'short' }]
 *          - se ausente usa demo hardcoded.
 */
import { T } from '@/styles/tokens'

const DEMO_ROWS = [
  { l: '1D',  t: 'BEARISH',   tone: 'short' },
  { l: '4H',  t: 'LATERAL',   tone: 'amber' },
  { l: '1H',  t: 'TRANSICAO', tone: 'amber' },
  { l: '15M', t: 'BULLISH',   tone: 'long' },
]

function toneColor(tone) {
  if (tone === 'long')  return T.long
  if (tone === 'short') return T.short
  return T.amber
}

export function LayerScores({ rows }) {
  const data = Array.isArray(rows) && rows.length ? rows : DEMO_ROWS
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${data.length}, 1fr)`, gap: 8 }}>
      {data.map((r, i) => {
        const color = toneColor(r.tone)
        return (
          <div
            key={i}
            style={{
              background: T.surface2,
              border: `1px solid ${T.border}`,
              borderTop: `2px solid ${color}`,
              padding: '12px 14px',
              borderRadius: 2,
            }}
          >
            <div className="t-mono" style={{
              fontSize: 10, color: T.textDim, letterSpacing: '0.14em',
            }}>{r.l}</div>
            <div className="t-mono" style={{
              fontSize: 13, color, marginTop: 6, letterSpacing: '0.1em',
            }}>{r.t}</div>
          </div>
        )
      })}
    </div>
  )
}
