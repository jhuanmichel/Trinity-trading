/**
 * ConfluenceBars.jsx — 6 barras bidirecionais (center-out).
 *
 * Cada barra: label esquerda + track central + valor direita.
 * Valor 50 = neutro (sem fill). >50 fill pra direita (tone long/amber).
 * <50 fill pra esquerda (tone short/amber).
 */
import { T } from '@/styles/tokens'
import { Num } from '@/components/primitives'
import { toneForScore, LAYER_LABELS, LAYER_ORDER } from '@/lib/backendMapping'

function toneColor(tone) {
  if (tone === 'long')  return T.long
  if (tone === 'short') return T.short
  return T.amber
}

function Bar({ label, value }) {
  const v = Math.max(0, Math.min(100, Number(value) || 0))
  const delta = v - 50            // -50..+50
  const magnitude = Math.abs(delta) // 0..50
  const widthPct = (magnitude / 50) * 50 // 0..50% (half of track)
  const pos = delta >= 0 ? 'right' : 'left'
  const tone = toneForScore(v)
  const color = toneColor(tone)

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      {/* Label esquerda */}
      <div
        style={{
          fontFamily: T.font.mono,
          fontSize: 10,
          letterSpacing: T.ls.LABEL,
          textTransform: 'uppercase',
          color: T.textDim,
          width: 36,
          textAlign: 'right',
        }}
      >
        {label}
      </div>

      {/* Track central */}
      <div
        style={{
          position: 'relative',
          flex: 1,
          height: 18,
          background: T.surface2,
          borderRadius: T.radius.sm,
          overflow: 'hidden',
        }}
      >
        {/* Linha central */}
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: 0,
            bottom: 0,
            width: 1,
            background: T.border,
          }}
        />
        {/* Fill — metade esquerda ou direita */}
        <div
          style={{
            position: 'absolute',
            top: 0,
            bottom: 0,
            [pos]: '50%',
            width: `${widthPct}%`,
            background: color,
            opacity: 0.7,
            transition: 'width 300ms ease-out',
          }}
        />
      </div>

      {/* Valor direita */}
      <Num size={12} color={color} style={{ width: 36, textAlign: 'left' }}>
        {v.toFixed(0)}
      </Num>
    </div>
  )
}

export function ConfluenceBars({ layers = {} }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      {LAYER_ORDER.map((key) => (
        <Bar key={key} label={LAYER_LABELS[key]} value={layers[key]} />
      ))}
    </div>
  )
}
