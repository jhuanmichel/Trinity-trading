/**
 * RadarTile.jsx — Card compacto para simbolo secundario (ETH/SOL/etc).
 *
 * Layout: header (symbol + delta) + price grande + footer (sparkline
 * opcional). Sparkline nao disponivel no backend atual — mostra
 * placeholder ate endpoint de time series expor.
 */
import { T } from '@/styles/tokens'
import { Num, Delta, Sparkline } from '@/components/primitives'

export function RadarTile({
  symbol,
  price = '—',
  change24h = 0,
  sparkData = null,
  sparkColor,
}) {
  const hasSpark = Array.isArray(sparkData) && sparkData.length >= 2
  const lineColor = sparkColor || (change24h >= 0 ? T.long : T.short)

  return (
    <div
      className="t-card"
      style={{
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {/* Header: symbol + delta */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <span
          style={{
            fontFamily: T.font.mono,
            fontSize: 12,
            fontWeight: T.fw.medium,
            letterSpacing: T.ls.label,
            color: T.text,
          }}
        >
          {symbol}
          <span style={{ color: T.textDim, marginLeft: 4 }}>/USDT</span>
        </span>
        <Delta v={change24h} size={11} />
      </div>

      {/* Price */}
      <Num size={20} weight={400} style={{ lineHeight: 1 }}>
        {price}
      </Num>

      {/* Sparkline footer (ou placeholder) */}
      <div style={{ height: 24, display: 'flex', alignItems: 'center' }}>
        {hasSpark ? (
          <Sparkline data={sparkData} w={140} h={24} color={lineColor} />
        ) : (
          <div
            style={{
              flex: 1,
              height: 1,
              background: T.border,
              opacity: 0.6,
            }}
          />
        )}
      </div>
    </div>
  )
}
