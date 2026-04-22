/**
 * Ticker.jsx — Scrolling price ticker (10 majors, 60s loop).
 *
 * Layout: sticky left (RADAR GLOBAL) + scrolling content (symbols).
 * Animation: transform translateX(-50%) em 60s linear infinite.
 * Duas copias do mesmo row lado-a-lado pra loop sem gap visivel.
 */
import { T } from '@/styles/tokens'
import { Dot, Delta, Label } from '@/components/primitives'
import { useTickerData } from '@/hooks/useTickerData'

function Row({ tickers }) {
  return (
    <div style={{ display: 'flex', gap: 36, padding: '0 18px' }}>
      {tickers.map((it, i) => (
        <div
          key={i}
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 8,
            fontFamily: T.font.mono,
            fontSize: 11,
          }}
        >
          <span style={{ color: T.textDim, letterSpacing: T.ls.label }}>{it.symbol}</span>
          <span style={{ color: T.text }}>{it.price}</span>
          <Delta v={it.change24h} size={10} />
        </div>
      ))}
    </div>
  )
}

export function Ticker() {
  const { tickers } = useTickerData()
  if (!tickers || tickers.length === 0) return null

  return (
    <div
      style={{
        height: 34,
        borderBottom: `1px solid ${T.border}`,
        background: T.surface,
        display: 'flex',
        alignItems: 'center',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* Sticky left — RADAR GLOBAL tag */}
      <div
        style={{
          position: 'sticky',
          left: 0,
          zIndex: 2,
          background: T.surface,
          padding: '0 14px',
          borderRight: `1px solid ${T.border}`,
          height: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}
      >
        <Dot variant="live" />
        <Label>RADAR GLOBAL</Label>
      </div>

      {/* Scrolling content: 2 copies pra loop perfeito */}
      <div
        style={{
          display: 'flex',
          animation: 't-ticker 60s linear infinite',
          whiteSpace: 'nowrap',
        }}
      >
        <Row tickers={tickers} />
        <Row tickers={tickers} />
      </div>
    </div>
  )
}
