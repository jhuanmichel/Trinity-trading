/**
 * RadarTile.jsx — Tile generico para Radar Global (BTC dom, USDT dom, etc.).
 * Visual fiel ao mockup Trinity Redesign.
 *
 * Props:
 *   label:  texto topo-esquerda (ex "BTC · 24H")
 *   value:  valor grande colorido
 *   sub:    subtexto monoespacado abaixo do value
 *   tone:   'long' | 'short' | 'amber' | 'cobalt' | 'neutral'
 *   spark:  array de numeros para sparkline (opcional)
 *   spread: label direita topo (ex "Alta", "Estavel")
 */
import { T } from '@/styles/tokens'
import { Label, Num, Sparkline } from '@/components/primitives'

function toneToColor(tone) {
  if (tone === 'long')   return T.long
  if (tone === 'short')  return T.short
  if (tone === 'amber')  return T.amber
  if (tone === 'cobalt') return T.cobalt
  return T.textMut
}

export function RadarTile({ label, value, sub, tone, spark, spread }) {
  const color = toneToColor(tone)
  return (
    <div className="t-card" style={{ padding: 18, position: 'relative' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <Label>{label}</Label>
        {spread && (
          <span className="t-mono" style={{
            fontSize: 10, color: T.textDim, letterSpacing: '0.1em',
          }}>
            {spread}
          </span>
        )}
      </div>
      <div style={{
        display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between',
        marginTop: 14,
      }}>
        <div>
          <Num size={30} weight={400} style={{ color }}>{value}</Num>
          <div className="t-mono" style={{
            fontSize: 10, color: T.textDim, letterSpacing: '0.12em', marginTop: 4,
          }}>{sub}</div>
        </div>
        {Array.isArray(spark) && spark.length >= 2 && (
          <Sparkline data={spark} w={70} h={28} color={color} />
        )}
      </div>
    </div>
  )
}
