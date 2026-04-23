/**
 * LiquidationMap.jsx — Heatmap de liquidacoes (S3b).
 *
 * Props:
 *   data: matrix[rows][cols] com valores 0-1 (densidade normalizada)
 *   rows: 8 (longs superiores, shorts inferiores)
 *   cols: 24
 *   priceRange: { lower, spot, upper } - labels eixo X
 */
import { T } from '@/styles/tokens'

export function LiquidationMap({
  data,
  rows = 8,
  cols = 24,
  priceRange = { lower: 0, spot: 0, upper: 0 },
}) {
  if (!data || data.length === 0) {
    return (
      <div style={{
        padding: '40px 20px',
        textAlign: 'center',
        color: T.textDim,
        fontFamily: T.font.mono,
        fontSize: 11,
        letterSpacing: T.ls.label,
      }}>
        SEM DADOS DE LIQUIDACAO
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 2 }}>
        {data.flat().map((v, i) => {
          const row = Math.floor(i / cols)
          const isLong = row < rows / 2
          const color = isLong ? T.long : T.short
          const opacity = 0.08 + Math.min(Math.max(v, 0), 1) * 0.85
          return (
            <div
              key={i}
              style={{
                aspectRatio: '1',
                background: color,
                opacity,
                borderRadius: 1,
              }}
            />
          )
        })}
      </div>
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          marginTop: 10,
          fontFamily: T.font.mono,
          fontSize: 9,
          color: T.textDim,
          letterSpacing: '0.12em',
        }}
      >
        <span>{`-5%  ${formatPrice(priceRange.lower)}`}</span>
        <span>{`SPOT ${formatPrice(priceRange.spot)}`}</span>
        <span>{`+5%  ${formatPrice(priceRange.upper)}`}</span>
      </div>
    </div>
  )
}

function formatPrice(v) {
  if (!v) return '—'
  if (v >= 1000) return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (v >= 1)    return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}
