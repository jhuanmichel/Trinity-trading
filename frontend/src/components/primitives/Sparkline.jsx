/**
 * Sparkline.jsx — Primitive V2
 * Sparkline SVG puro (sem deps externas). Aceita array de numeros.
 * Props: data (number[]), w (80), h (24), color (T.long), fill (true).
 */
import { T } from '../../styles/tokens'

export function Sparkline({
  data,
  w = 80,
  h = 24,
  color = T.long,
  fill = true,
}) {
  if (!Array.isArray(data) || data.length < 2) return null

  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1

  // Mapeia pontos para coordenadas SVG
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = h - ((v - min) / range) * (h - 2) - 1
    return [x, y]
  })

  const d = pts
    .map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
    .join(' ')
  const area = `${d} L ${w} ${h} L 0 ${h} Z`

  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {fill && <path d={area} fill={color} opacity="0.12" />}
      <path d={d} stroke={color} strokeWidth="1.2" fill="none" />
    </svg>
  )
}
