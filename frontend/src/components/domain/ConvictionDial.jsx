/**
 * ConvictionDial.jsx — Gauge radial SVG (componente estrela V2).
 *
 * Arc de -220deg a +40deg (260deg span). Gradient long->amber->short.
 * Needle rotacionada via transform rotate. Ticks a cada 5%.
 * Label central com status + score + confluences count.
 */
import { T } from '@/styles/tokens'
import { Num, Label } from '@/components/primitives'
import { toneForScore } from '@/lib/backendMapping'

const SIZE      = 240
const CX        = 120
const CY        = 120
const R         = 88
const ARC_START = -220
const ARC_END   = 40

function polar(cx, cy, r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180
  return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
}

function arcPath(cx, cy, r, startDeg, endDeg) {
  const [x1, y1] = polar(cx, cy, r, startDeg)
  const [x2, y2] = polar(cx, cy, r, endDeg)
  const largeArc = Math.abs(endDeg - startDeg) > 180 ? 1 : 0
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${largeArc} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
}

function centerLabel(direction) {
  const d = String(direction || '').toUpperCase()
  if (d === 'LONG' || d === 'SHORT') return 'ATIVO'
  if (d === 'NEUTRO' || d === 'NEUTRAL') return 'NEUTRO'
  return 'AGUARDANDO'
}

export function ConvictionDial({ score = 0, confluences = 0, direction = 'NEUTRO' }) {
  const s     = Math.max(0, Math.min(100, Number(score) || 0))
  const angle = ARC_START + (s / 100) * (ARC_END - ARC_START)
  const tone  = toneForScore(s)
  const needleColor = tone === 'long' ? T.long : tone === 'short' ? T.short : T.amber

  const ticks = Array.from({ length: 21 }, (_, i) => {
    const a = ARC_START + (i / 20) * (ARC_END - ARC_START)
    const [x1, y1] = polar(CX, CY, R - 6, a)
    const [x2, y2] = polar(CX, CY, R,     a)
    return { x1, y1, x2, y2, major: i % 4 === 0 }
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: 12, position: 'relative' }}>
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
        <defs>
          <linearGradient id="conviction-grad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%"   stopColor={T.short}  />
            <stop offset="50%"  stopColor={T.amber}  />
            <stop offset="100%" stopColor={T.long}   />
          </linearGradient>
        </defs>

        {/* Background arc */}
        <path
          d={arcPath(CX, CY, R, ARC_START, ARC_END)}
          stroke={T.border}
          strokeWidth="8"
          fill="none"
          strokeLinecap="round"
        />

        {/* Progress arc */}
        <path
          d={arcPath(CX, CY, R, ARC_START, angle)}
          stroke="url(#conviction-grad)"
          strokeWidth="8"
          fill="none"
          strokeLinecap="round"
        />

        {/* Ticks */}
        {ticks.map((tk, i) => (
          <line
            key={i}
            x1={tk.x1.toFixed(2)} y1={tk.y1.toFixed(2)}
            x2={tk.x2.toFixed(2)} y2={tk.y2.toFixed(2)}
            stroke={tk.major ? T.textDim : T.border}
            strokeWidth={tk.major ? 1.5 : 1}
          />
        ))}

        {/* Needle */}
        <g transform={`rotate(${angle.toFixed(2)} ${CX} ${CY})`}>
          <line
            x1={CX} y1={CY}
            x2={CX + R - 12} y2={CY}
            stroke={needleColor}
            strokeWidth="2.5"
            strokeLinecap="round"
          />
          <circle cx={CX} cy={CY} r="5" fill={needleColor} />
        </g>
      </svg>

      {/* Center labels (sobreposto no SVG) */}
      <div
        style={{
          position: 'absolute',
          top: '50%',
          left: '50%',
          transform: 'translate(-50%, -50%)',
          textAlign: 'center',
          pointerEvents: 'none',
          width: 160,
        }}
      >
        <Label>{centerLabel(direction)}</Label>
        <Num
          size={48}
          weight={300}
          style={{ display: 'block', lineHeight: 1, marginTop: 4, color: needleColor }}
        >
          {s.toFixed(0)}
        </Num>
        <Label style={{ marginTop: 6, color: T.textDim }}>
          {confluences}/6 CONFLU.
        </Label>
      </div>
    </div>
  )
}
