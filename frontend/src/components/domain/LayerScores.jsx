/**
 * LayerScores.jsx — Grid 3x2 de 6 cards (1 por camada).
 *
 * Single-TF: rotulo "CURRENT" (multi-TF quando backend expor
 * layer_scores_mtf com 1D/4H/1H/15M vira em sessao futura).
 *
 * Cada card: border-top fina colorida (tone) + label + Num grande.
 */
import { T } from '@/styles/tokens'
import { Num, Label } from '@/components/primitives'
import { toneForScore, LAYER_LABELS, LAYER_ORDER } from '@/lib/backendMapping'

function toneColor(tone) {
  if (tone === 'long')  return T.long
  if (tone === 'short') return T.short
  return T.amber
}

function LayerCard({ label, value }) {
  const v     = Math.max(0, Math.min(100, Number(value) || 0))
  const tone  = toneForScore(v)
  const color = toneColor(tone)

  return (
    <div
      style={{
        background: T.surface2,
        border: `1px solid ${T.border}`,
        borderTop: `2px solid ${color}`,
        borderRadius: T.radius.sm,
        padding: '14px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      <Label>{label}</Label>
      <Num size={28} weight={300} color={color} style={{ lineHeight: 1 }}>
        {v.toFixed(0)}
      </Num>
    </div>
  )
}

export function LayerScores({ layers = {}, tfLabel = 'CURRENT' }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Header TF indicator */}
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
        }}
      >
        <Label style={{ color: T.textMut }}>TIMEFRAME</Label>
        <Label style={{ color: T.text }}>{tfLabel}</Label>
      </div>

      {/* Grid 3 colunas x 2 linhas = 6 layers */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(3, 1fr)',
          gridAutoRows: 'min-content',
          gap: 10,
        }}
      >
        {LAYER_ORDER.map((key) => (
          <LayerCard key={key} label={LAYER_LABELS[key]} value={layers[key]} />
        ))}
      </div>
    </div>
  )
}
