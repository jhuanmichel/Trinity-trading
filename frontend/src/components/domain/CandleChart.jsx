/**
 * CandleChart.jsx — Candlestick SVG (visual mockup).
 *
 * Props:
 *   candles: array [{o,h,l,c}] - se ausente, gera 60 candles pseudo-random estaveis
 *   height:  px (default 280)
 *   levels?: { entry?, sl?, tp?, ... } - linhas horizontais anotadas
 *   seedPrice: preco inicial para simulacao (default 78800)
 */
import { useMemo } from 'react'
import { T } from '@/styles/tokens'

function seeded(seed) {
  return () => {
    seed = (seed * 9301 + 49297) % 233280
    return seed / 233280
  }
}

function genCandles(n = 60, startPrice = 78800, seed = 13) {
  const rng = seeded(seed)
  let p = startPrice
  return Array.from({ length: n }, () => {
    const o = p
    const c = p + (rng() - 0.48) * 120
    const h = Math.max(o, c) + rng() * 40
    const l = Math.min(o, c) - rng() * 40
    p = c
    return { o, c, h, l }
  })
}

function fmt(v) {
  if (v >= 1000) return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (v >= 1)    return v.toFixed(4)
  return v.toFixed(6)
}

export function CandleChart({
  candles,
  height = 280,
  levels = { entry: 78843.10, sl: 78447.52, tp: 79238.68 },
  seedPrice = 78800,
}) {
  const data = useMemo(
    () => (Array.isArray(candles) && candles.length ? candles : genCandles(60, seedPrice)),
    [candles, seedPrice]
  )
  const W = 640, H = height
  const min = Math.min(...data.map(c => c.l)) - 30
  const max = Math.max(...data.map(c => c.h)) + 30
  const xw = W / data.length
  const scaleY = v => H - ((v - min) / (max - min)) * (H - 20) - 10

  const levelRows = [
    levels.entry != null ? { v: levels.entry, c: T.amber, l: `ENTRY ${fmt(levels.entry)}` } : null,
    levels.sl    != null ? { v: levels.sl,    c: T.short, l: `SL    ${fmt(levels.sl)}`    } : null,
    levels.tp    != null ? { v: levels.tp,    c: T.long,  l: `TP1   ${fmt(levels.tp)}`    } : null,
  ].filter(Boolean)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H, display: 'block' }}>
      {[0.2, 0.4, 0.6, 0.8].map((t, i) => (
        <line key={i} x1="0" x2={W} y1={H * t} y2={H * t}
          stroke={T.border} strokeWidth="0.5" strokeDasharray="2 6" />
      ))}

      {levelRows.map((lvl, i) => (
        <g key={i}>
          <line x1="0" x2={W} y1={scaleY(lvl.v)} y2={scaleY(lvl.v)}
            stroke={lvl.c} strokeWidth="0.8" strokeDasharray="3 3" opacity="0.8" />
          <rect x={W - 160} y={scaleY(lvl.v) - 9} width={160} height={18} fill={T.bg} />
          <text x={W - 8} y={scaleY(lvl.v) + 4}
            fontFamily={T.font.mono} fontSize="10" fill={lvl.c}
            textAnchor="end" letterSpacing="1">{lvl.l}</text>
        </g>
      ))}

      {data.map((c, i) => {
        const up = c.c >= c.o
        const x = i * xw + xw / 2
        const color = up ? T.long : T.short
        return (
          <g key={i} opacity="0.95">
            <line x1={x} x2={x} y1={scaleY(c.h)} y2={scaleY(c.l)}
              stroke={color} strokeWidth="1" />
            <rect
              x={x - xw * 0.35}
              y={scaleY(Math.max(c.o, c.c))}
              width={xw * 0.7}
              height={Math.max(1, Math.abs(scaleY(c.o) - scaleY(c.c)))}
              fill={color}
            />
          </g>
        )
      })}

      <g>
        <circle cx={data.length * xw - xw / 2} cy={scaleY(data[data.length - 1].c)}
          r="3" fill={T.cobalt} />
        <circle cx={data.length * xw - xw / 2} cy={scaleY(data[data.length - 1].c)}
          r="7" fill="none" stroke={T.cobalt} strokeWidth="0.8" opacity="0.5" />
      </g>
    </svg>
  )
}
