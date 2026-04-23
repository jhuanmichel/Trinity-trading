/**
 * Landing.jsx — Tela Landing real (S3b).
 * Hero + top movers live.
 */
import { T } from '@/styles/tokens'
import { Label, Num, Pill, Delta } from '@/components/primitives'
import { useRouterStore, ROUTES } from '@/store/routerStore'
import { useTickerData }   from '@/hooks/useTickerData'
import { useMacroStatus }  from '@/hooks/useMacroStatus'
import { useRecentSignals } from '@/hooks/useRecentSignals'

export function Landing() {
  const setRoute        = useRouterStore(s => s.setRoute)
  const { tickers }     = useTickerData()
  const { macroStatus } = useMacroStatus()
  const { data: signals = [] } = useRecentSignals(5)

  const topGainers = (tickers || [])
    .filter(t => t.change24h > 0)
    .sort((a, b) => b.change24h - a.change24h)
    .slice(0, 3)
  const topLosers = (tickers || [])
    .filter(t => t.change24h < 0)
    .sort((a, b) => a.change24h - b.change24h)
    .slice(0, 3)
  const firstRow = (tickers || []).slice(0, 3)

  return (
    <div>
      <section style={{ padding: '100px 24px 80px', maxWidth: 1400, margin: '0 auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 80, alignItems: 'center' }}>
          <div>
            <Pill tone="long" style={{ marginBottom: 24 }}>
              ● {signals.length} SINAIS RECENTES · LIVE
            </Pill>
            <h1 style={{
              fontFamily: T.font.display,
              fontWeight: 400,
              fontSize: 76,
              lineHeight: 0.98,
              letterSpacing: '-0.03em',
              margin: 0,
            }}>
              Leitura institucional<br/>
              <span style={{ color: T.textMut }}>em tempo real,</span><br/>
              sem ruido.
            </h1>
            <p style={{
              marginTop: 28,
              fontSize: 17,
              lineHeight: 1.55,
              color: T.textMut,
              maxWidth: 520,
            }}>
              Trinity agrega <span style={{ color: T.text }}>Market Structure, Liquidez, Volume, Tendencia, Correlacao e Volatilidade</span> em um unico indice de conviccao — arquitetura usada por mesas institucionais.
            </p>

            <div style={{ marginTop: 36, display: 'flex', gap: 12 }}>
              <button
                onClick={() => setRoute(ROUTES.DASHBOARD)}
                style={{
                  background: T.text, color: T.bg, border: 'none',
                  padding: '14px 26px', borderRadius: 3, cursor: 'pointer',
                  fontFamily: T.font.mono, fontSize: 12,
                  letterSpacing: '0.18em', textTransform: 'uppercase',
                }}
              >
                Abrir Dashboard →
              </button>
              <button
                onClick={() => setRoute(ROUTES.SIGNALS)}
                style={{
                  background: 'transparent', color: T.text,
                  border: `1px solid ${T.borderHi}`,
                  padding: '14px 26px', borderRadius: 3, cursor: 'pointer',
                  fontFamily: T.font.mono, fontSize: 12,
                  letterSpacing: '0.18em', textTransform: 'uppercase',
                }}
              >
                Ver Sinais
              </button>
            </div>

            <div style={{ marginTop: 60, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 24, maxWidth: 520 }}>
              {[
                { k: macroStatus || '—', l: 'REGIME MACRO' },
                { k: '6',                l: 'CAMADAS DE SINAL' },
                { k: '24/7',             l: 'MONITORAMENTO' },
              ].map((s, i) => (
                <div key={i} style={{ borderTop: `1px solid ${T.border}`, paddingTop: 14 }}>
                  <Num size={28} weight={300}>{s.k}</Num>
                  <div className="t-label" style={{ color: T.textDim, marginTop: 4 }}>{s.l}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Painel direita — preview tickers */}
          <div>
            <div className="t-card t-gridbg" style={{ padding: 24 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
                <Label>TOP MOVERS · 24h</Label>
                <Pill tone="cobalt">LIVE</Pill>
              </div>

              {topGainers.length > 0 && (
                <>
                  <Label style={{ marginBottom: 8 }}>GAINERS</Label>
                  {topGainers.map((t, i) => <MoverRow key={`g-${i}`} t={t} />)}
                </>
              )}

              {topLosers.length > 0 && (
                <>
                  <div style={{ marginTop: 16 }}>
                    <Label style={{ marginBottom: 8 }}>LOSERS</Label>
                  </div>
                  {topLosers.map((t, i) => <MoverRow key={`l-${i}`} t={t} />)}
                </>
              )}

              {/* Fallback se change24h=0 em todos (useTickerData ainda placeholder) */}
              {topGainers.length === 0 && topLosers.length === 0 && firstRow.length > 0 && (
                <>
                  <Label style={{ marginBottom: 8 }}>SNAPSHOT</Label>
                  {firstRow.map((t, i) => <MoverRow key={`s-${i}`} t={t} />)}
                </>
              )}
            </div>
          </div>
        </div>
      </section>
    </div>
  )
}

function MoverRow({ t }) {
  return (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'baseline',
      padding: '8px 0',
      borderBottom: `1px solid ${T.border}`,
      fontFamily: T.font.mono,
      fontSize: 12,
    }}>
      <span style={{ color: T.textDim }}>{t.symbol}</span>
      <span style={{ color: T.text }}>{t.price}</span>
      <Delta v={t.change24h} />
    </div>
  )
}
