/**
 * ScoreGauge.jsx — Semicircular SVG confidence gauge
 */
import React, { memo, useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import { gaugeColor, convictionLabel } from '../../engine/scoreEngine'
import './ScoreGauge.css'

// Converte grau → coordenadas no arco semicircular
// cx=110, cy=120, r=88
const CX = 110, CY = 120, R = 88

function polarToCartesian(deg) {
  const rad = (deg * Math.PI) / 180
  return {
    x: +(CX + R * Math.cos(rad)).toFixed(2),
    y: +(CY - R * Math.sin(rad)).toFixed(2),
  }
}

function arcPath(startDeg, endDeg) {
  const s = polarToCartesian(startDeg)
  const e = polarToCartesian(endDeg)
  const largeArc = Math.abs(endDeg - startDeg) > 180 ? 1 : 0
  return `M ${s.x} ${s.y} A ${R} ${R} 0 ${largeArc} 0 ${e.x} ${e.y}`
}

// 5 segmentos do gauge: 180° → 0° (esquerda para direita)
const SEGMENTS = [
  { start: 180, end: 144, color: '#FF2A4A' },  // 0-20: STRONG SHORT
  { start: 144, end: 108, color: '#F97316' },  // 20-40: SHORT
  { start: 108, end: 72,  color: '#FFD700' },  // 40-60: NEUTRAL
  { start: 72,  end: 36,  color: '#6dde9f' },  // 60-80: LONG
  { start: 36,  end: 0,   color: '#00FF88' },  // 80-100: STRONG LONG
]

const ScoreGauge = memo(({ score = 50, size = 220 }) => {
  const clampedScore = Math.min(100, Math.max(0, Number(score)))
  // Score 0 = 180°, Score 100 = 0°
  const needleDeg = 180 - clampedScore * 1.8
  const rad = (needleDeg * Math.PI) / 180
  const needleLen = 70
  const nx = +(CX + needleLen * Math.cos(rad)).toFixed(2)
  const ny = +(CY - needleLen * Math.sin(rad)).toFixed(2)

  const color  = gaugeColor(clampedScore)
  const conv   = convictionLabel(clampedScore)

  return (
    <div className="gauge-wrap" style={{ width: size, height: size * 0.65 }}>
      <svg viewBox="0 0 220 130" style={{ width: '100%', height: '100%' }}>
        {/* Track bg */}
        <path
          d={arcPath(180, 0)}
          fill="none"
          stroke="var(--bg-border)"
          strokeWidth="10"
          strokeLinecap="round"
        />

        {/* Colored segments */}
        {SEGMENTS.map((seg, i) => (
          <path
            key={i}
            d={arcPath(seg.start, seg.end)}
            fill="none"
            stroke={seg.color}
            strokeWidth="10"
            strokeLinecap="butt"
            opacity="0.7"
          />
        ))}

        {/* Needle */}
        <motion.line
          x1={CX}
          y1={CY}
          x2={nx}
          y2={ny}
          stroke={color}
          strokeWidth="2.5"
          strokeLinecap="round"
          initial={{ rotate: 0 }}
          animate={{ x2: nx, y2: ny }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
          style={{ filter: `drop-shadow(0 0 4px ${color})` }}
        />

        {/* Needle pivot */}
        <circle cx={CX} cy={CY} r="5" fill={color} style={{ filter: `drop-shadow(0 0 6px ${color})` }} />

        {/* Score text */}
        <text
          x={CX}
          y={CY - 22}
          textAnchor="middle"
          fill={color}
          fontSize="26"
          fontFamily="'IBM Plex Mono', monospace"
          fontWeight="700"
          style={{ filter: `drop-shadow(0 0 8px ${color})` }}
        >
          {clampedScore.toFixed(0)}
        </text>

        {/* Label */}
        <text
          x={CX}
          y={CY - 6}
          textAnchor="middle"
          fill={color}
          fontSize="8"
          fontFamily="'IBM Plex Mono', monospace"
          fontWeight="600"
          opacity="0.8"
          letterSpacing="1.5"
        >
          {conv.label}
        </text>

        {/* Min/Max labels */}
        <text x="10" y={CY + 18} fill="var(--text-muted)" fontSize="8" fontFamily="monospace">0</text>
        <text x={CX * 2 - 18} y={CY + 18} fill="var(--text-muted)" fontSize="8" fontFamily="monospace">100</text>
        <text x={CX} y={CY + 18} textAnchor="middle" fill="var(--text-muted)" fontSize="7" fontFamily="monospace">50</text>
      </svg>
    </div>
  )
})

ScoreGauge.displayName = 'ScoreGauge'
export default ScoreGauge
