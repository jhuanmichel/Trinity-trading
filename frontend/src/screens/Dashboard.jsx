/**
 * Dashboard.jsx — Trinity V2 tela principal.
 *
 * Consome useConviction (score, direction, layers, trade plan) e
 * useTickerData (4 majors secundarios). Compoe ConvictionDial,
 * ConfluenceBars, LayerScores, RadarTile.
 */
import { T } from '@/styles/tokens'
import { Card, Label, Num, Pill } from '@/components/primitives'
import { ConvictionDial } from '@/components/domain/ConvictionDial'
import { ConfluenceBars } from '@/components/domain/ConfluenceBars'
import { LayerScores } from '@/components/domain/LayerScores'
import { RadarTile } from '@/components/domain/RadarTile'
import { useConviction } from '@/hooks/useConviction'
import { useTickerData } from '@/hooks/useTickerData'

function sentimentColor(direction) {
  const d = String(direction || '').toUpperCase()
  if (d === 'LONG')    return T.long
  if (d === 'SHORT')   return T.short
  if (d === 'BULLISH') return T.long
  if (d === 'BEARISH') return T.short
  return T.amber
}

function formatPrice(n) {
  const num = Number(n) || 0
  if (num >= 1000) return `$${num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  if (num >= 1)    return `$${num.toFixed(4)}`
  return `$${num.toFixed(6)}`
}

function TradePlanLine({ label, value, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', padding: '8px 0', borderBottom: `1px solid ${T.border}` }}>
      <Label style={{ color: T.textDim }}>{label}</Label>
      <Num size={15} weight={400} color={color}>{formatPrice(value)}</Num>
    </div>
  )
}

export function Dashboard() {
  const { conviction, layers, tradePlan, isLoading } = useConviction()
  const { tickers } = useTickerData()

  // Filtra ETH/SOL/BNB/XRP para Radar Tiles secundarios
  const secondarySymbols = ['ETH', 'SOL', 'BNB', 'XRP']
  const secondaryTickers = tickers.filter((t) => secondarySymbols.includes(t.symbol))

  const sColor = sentimentColor(conviction.direction)

  return (
    <div style={{ padding: '32px 24px 80px', maxWidth: 1680, margin: '0 auto' }}>
      {/* Section header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'flex-end',
          justifyContent: 'space-between',
          marginBottom: 28,
          gap: 24,
        }}
      >
        <div>
          <Label>DASHBOARD / RADAR GLOBAL</Label>
          <h1
            style={{
              fontFamily: T.font.display,
              fontWeight: T.fw.medium,
              fontSize: T.fs['3xl'],
              letterSpacing: T.ls.snug,
              lineHeight: 1.1,
              margin: '8px 0 0',
              color: T.text,
            }}
          >
            Sentimento institucional{' '}
            <span style={{ color: T.textMut }}>—</span>{' '}
            <span style={{ color: sColor }}>{conviction.direction.toUpperCase()}</span>
          </h1>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Pill tone="violet">MS · {conviction.marketStructure}</Pill>
          {conviction.bosBull && <Pill tone="long">BOS ↑</Pill>}
          {conviction.bosBear && <Pill tone="short">BOS ↓</Pill>}
          {conviction.choch && <Pill tone="amber">CHOCH</Pill>}
          {conviction.sweepHigh && <Pill tone="cobalt">SWEEP HIGH</Pill>}
          {conviction.sweepLow && <Pill tone="cobalt">SWEEP LOW</Pill>}
        </div>
      </div>

      {/* Hero: ConvictionDial + Trade Plan */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '340px 1fr',
          gap: 20,
          marginBottom: 20,
        }}
      >
        <Card title="CONVICTION · BTC/USDT" aside={
          <span className="t-num" style={{ fontSize: 10, color: T.textDim, letterSpacing: T.ls.label }}>
            {conviction.confluences}/6 CONFLU.
          </span>
        }>
          <ConvictionDial
            score={conviction.score}
            confluences={conviction.confluences}
            direction={conviction.direction}
          />
        </Card>

        <Card title="TRADE PLAN" aside={<Pill tone={conviction.valid ? 'long' : 'neutral'}>{conviction.valid ? 'VÁLIDO' : 'INSUFICIENTE'}</Pill>}>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <TradePlanLine label="ENTRY"  value={tradePlan.entry} color={T.text} />
            <TradePlanLine label="STOP"   value={tradePlan.stop}  color={T.short} />
            <TradePlanLine label="TP1"    value={tradePlan.tp1}   color={T.long} />
            <TradePlanLine label="TP2"    value={tradePlan.tp2}   color={T.long} />
            <TradePlanLine label="TP3"    value={tradePlan.tp3}   color={T.long} />
            <div style={{ marginTop: 16, color: T.textDim, fontSize: 11, letterSpacing: T.ls.label, fontFamily: T.font.mono, textTransform: 'uppercase' }}>
              PREÇO ATUAL: <span style={{ color: T.text }}>{formatPrice(conviction.price)}</span>
            </div>
          </div>
        </Card>
      </div>

      {/* Confluences + LayerScores */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 20,
          marginBottom: 20,
        }}
      >
        <Card title="CONFLUÊNCIAS · 6 CAMADAS">
          <ConfluenceBars layers={layers} />
        </Card>
        <Card title="TENDÊNCIA · MULTI-TIMEFRAME">
          <LayerScores layers={layers} />
        </Card>
      </div>

      {/* Secondary majors */}
      <Card title="MAJORS · RADAR SECUNDÁRIO" noPad>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: 1,
            background: T.border,
            padding: 1,
          }}
        >
          {secondaryTickers.length === 0 ? (
            <div style={{ gridColumn: '1 / -1', padding: 32, textAlign: 'center', color: T.textDim, fontFamily: T.font.mono, fontSize: 11, letterSpacing: T.ls.label }}>
              {isLoading ? 'CARREGANDO...' : 'AGUARDANDO DADOS'}
            </div>
          ) : (
            secondaryTickers.map((t) => (
              <RadarTile
                key={t.symbol}
                symbol={t.symbol}
                price={t.price}
                change24h={t.change24h}
              />
            ))
          )}
        </div>
      </Card>
    </div>
  )
}
