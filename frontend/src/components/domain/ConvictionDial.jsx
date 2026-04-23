/**
 * ConvictionDial.jsx — Radial gauge -220° a 40° (sweep 260°).
 * Visual fiel ao mockup Trinity Redesign.
 *
 * Props:
 *   value: 0-100 (default 52)
 *   status: texto do pill central (default "AGUARDANDO")
 *   statusTone: 'amber'|'long'|'short' (default 'amber')
 */
import { T } from '@/styles/tokens'
import { Pill } from '@/components/primitives'

export function ConvictionDial({
  value = 52,
  status = 'AGUARDANDO',
  statusTone = 'amber',
}) {
  const size = 240, cx = size / 2, cy = size / 2, r = 88
  const arcStart = -220, arcEnd = 40
  const sweep = arcEnd - arcStart
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  const valAngle = arcStart + (v / 100) * sweep

  const polar = (deg, rad) => [
    cx + rad * Math.cos((deg * Math.PI) / 180),
    cy + rad * Math.sin((deg * Math.PI) / 180),
  ]
  const arcPath = (a1, a2, rad) => {
    const [x1, y1] = polar(a1, rad)
    const [x2, y2] = polar(a2, rad)
    const large = Math.abs(a2 - a1) > 180 ? 1 : 0
    return `M ${x1} ${y1} A ${rad} ${rad} 0 ${large} 1 ${x2} ${y2}`
  }

  const ticks = []
  for (let i = 0; i <= 20; i++) {
    const a = arcStart + (i / 20) * sweep
    const big = i % 5 === 0
    const [x1, y1] = polar(a, r + 6)
    const [x2, y2] = polar(a, r + (big ? 14 : 10))
    ticks.push(
      <line
        key={i}
        x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={T.textDim}
        strokeWidth={big ? 1.2 : 0.7}
        opacity={big ? 0.9 : 0.5}
      />
    )
  }

  return (
    <div style={{ position: 'relative', width: size, height: size, margin: '0 auto' }}>
      <svg width={size} height={size}>
        <defs>
          <linearGradient id="dialgrad" x1="0" x2="1">
            <stop offset="0"   stopColor={T.short} />
            <stop offset="0.5" stopColor={T.amber} />
            <stop offset="1"   stopColor={T.long} />
          </linearGradient>
        </defs>
        <path d={arcPath(arcStart, arcEnd, r)}    stroke={T.border} strokeWidth="10" fill="none" strokeLinecap="round" />
        <path d={arcPath(arcStart, valAngle, r)}  stroke="url(#dialgrad)" strokeWidth="10" fill="none" strokeLinecap="round" />
        {ticks}
        <circle cx={cx} cy={cy} r={r - 20} fill="none" stroke={T.border} strokeWidth="0.8" strokeDasharray="2 4" opacity="0.5" />
        <line
          x1={cx} y1={cy}
          x2={polar(valAngle, r - 6)[0]} y2={polar(valAngle, r - 6)[1]}
          stroke={T.text} strokeWidth="1.5"
        />
        <circle cx={cx} cy={cy} r="4" fill={T.text} />
        <circle cx={cx} cy={cy} r="2" fill={T.bg} />
      </svg>

      <div style={{
        position: 'absolute', inset: 0,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        pointerEvents: 'none', paddingTop: 40,
      }}>
        <div className="t-label" style={{ color: T.textDim, marginBottom: 2 }}>CONVICCAO</div>
        <div className="t-num" style={{
          fontSize: 48, fontWeight: 300, letterSpacing: '-0.03em', marginTop: 0,
        }}>
          {v}<span style={{ fontSize: 18, color: T.textMut, marginLeft: 2 }}>%</span>
        </div>
        <div style={{ marginTop: 4 }}>
          <Pill tone={statusTone}>{status}</Pill>
        </div>
      </div>

      <div className="t-mono" style={{
        position: 'absolute', bottom: 8, left: 4, fontSize: 9,
        color: T.short, letterSpacing: '0.12em',
      }}>SHORT 0</div>
      <div className="t-mono" style={{
        position: 'absolute', bottom: 8, right: 4, fontSize: 9,
        color: T.long, letterSpacing: '0.12em',
      }}>LONG 100</div>
    </div>
  )
}
