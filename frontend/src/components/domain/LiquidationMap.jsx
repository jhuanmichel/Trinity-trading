/**
 * LiquidationMap.jsx — Heatmap de liquidacoes (visual mockup).
 *
 * Longs na metade superior (verde), shorts na inferior (vermelho).
 * Props:
 *   data?: matrix[rows][cols] com valores 0-1. Se ausente, gera demo estavel.
 *   rows, cols: dimensoes (default 8 × 24)
 *   priceRange: { lower, spot, upper } - labels eixo X
 */
import { useMemo } from 'react'
import { T } from '@/styles/tokens'

function seeded(seed) {
  return () => {
    seed = (seed * 9301 + 49297) % 233280
    return seed / 233280
  }
}

function genHeatmap(rows, cols, seed = 99) {
  const rng = seeded(seed)
  return Array.from({ length: rows }, () =>
    Array.from({ length: cols }, () => rng())
  )
}

function formatPrice(v) {
  if (!v) return '—'
  if (v >= 1000) return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (v >= 1)    return `$${v.toFixed(2)}`
  return `$${v.toFixed(4)}`
}

export function LiquidationMap({
  data,
  rows = 8,
  cols = 24,
  priceRange = { lower: 74973, spot: 78919, upper: 82865 },
}) {
  const matrix = useMemo(
    () => (Array.isArray(data) && data.length ? data : genHeatmap(rows, cols)),
    [data, rows, cols]
  )

  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: 2 }}>
        {matrix.flat().map((v, i) => {
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
