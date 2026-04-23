/**
 * Dashboard.jsx — Trinity V2 tela principal (visual fiel ao mockup Trinity Redesign).
 *
 * Seções (top→bottom):
 *   1. Header: label + H1 display + subtext + 3 macro pills
 *   2. Hero 340px | 1fr: ConvictionDial card + BTC primary card (chart+levels)
 *   3. Radar row 4: BTC/ETH/BTC DOM/USDT DOM
 *   4. Sentiment + Regime 1.2fr | 1fr
 *   5. Confluences + Liquidation Map + SignalsList 1/1/1.2
 *   6. Sticky action bar: PUMP ALERT / CRASH ALERT
 *
 * Hoje renderiza com dados DEMO estáveis; wiring real vem em sessão futura.
 */
import { T } from '@/styles/tokens'
import { Card, Label, Num, Pill, Delta } from '@/components/primitives'
import { ConvictionDial }  from '@/components/domain/ConvictionDial'
import { ConfluenceBars }  from '@/components/domain/ConfluenceBars'
import { LayerScores }     from '@/components/domain/LayerScores'
import { RadarTile }       from '@/components/domain/RadarTile'
import { CandleChart }     from '@/components/domain/CandleChart'
import { LiquidationMap }  from '@/components/domain/LiquidationMap'
import { SignalsList }     from '@/components/domain/SignalsList'

const CONV_CHIPS = [
  { k: 'STR', on: true,  tone: 'long'  },
  { k: 'LIQ', on: false, tone: null    },
  { k: 'VOL', on: false, tone: null    },
  { k: 'TRD', on: true,  tone: 'long'  },
  { k: 'COR', on: false, tone: null    },
  { k: 'VTY', on: true,  tone: 'long'  },
]

const LEVELS = [
  { l: 'ENTRY',       v: '$78,843.10', c: T.amber },
  { l: 'STOP LOSS',   v: '$78,447.52', c: T.short },
  { l: 'TP 1',        v: '$79,238.68', c: T.long  },
  { l: 'TP 2',        v: '$79,502.40', c: T.long  },
  { l: 'TP 3',        v: '$79,897.98', c: T.long  },
  { l: 'ATR · 0.33%', v: 'R/R 1:1.4',  c: T.text  },
]

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1D']
const ACTIVE_TF = '15m'

export function Dashboard() {
  return (
    <div style={{ padding: '32px 24px 80px', maxWidth: 1680, margin: '0 auto' }}>
      {/* ── Section header ─────────────────────────────────── */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'baseline',
        marginBottom: 24,
        gap: 24,
        flexWrap: 'wrap',
      }}>
        <div>
          <Label>DASHBOARD / RADAR GLOBAL</Label>
          <h1 style={{
            fontFamily: T.font.display,
            fontWeight: 500,
            fontSize: 32,
            margin: '6px 0 0',
            letterSpacing: '-0.02em',
          }}>
            Sentimento institucional{' '}
            <span style={{ color: T.textMut }}>—</span>{' '}
            <span style={{ color: T.amber }}>Neutro</span>
          </h1>
          <div style={{
            marginTop: 8,
            fontFamily: T.font.mono,
            fontSize: 11,
            color: T.textMut,
            letterSpacing: '0.06em',
          }}>
            Atualizado há 8s · 6 confluências monitoradas · Próximo tick em 52s
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Pill tone="violet">MACRO · NEUTRO</Pill>
          <Pill tone="amber">FUNDING · 0.012%</Pill>
          <Pill tone="cobalt">DXY · 104.3</Pill>
        </div>
      </div>

      {/* ── HERO: Conviction Dial + BTC Primary ──────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '340px 1fr',
        gap: 20,
        marginBottom: 20,
      }}>
        {/* Conviction Dial card */}
        <div className="t-card" style={{
          padding: '24px 20px',
          display: 'flex',
          flexDirection: 'column',
        }}>
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 12,
          }}>
            <Label>CONVICTION · BTC/USDT</Label>
            <span className="t-mono" style={{ fontSize: 10, color: T.textDim }}>
              3/6 CONFLU.
            </span>
          </div>
          <ConvictionDial value={52} />
          <div style={{
            marginTop: 12,
            display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr',
            gap: 6,
          }}>
            {CONV_CHIPS.map((c, i) => (
              <div key={i} style={{
                padding: '8px 0',
                textAlign: 'center',
                border: `1px solid ${c.on ? T.long + '55' : T.border}`,
                background: c.on ? T.long + '0e' : 'transparent',
                borderRadius: 2,
                fontFamily: T.font.mono,
                fontSize: 10,
                color: c.on ? T.long : T.textDim,
                letterSpacing: '0.14em',
              }}>{c.k}</div>
            ))}
          </div>
        </div>

        {/* BTC primary card */}
        <div className="t-card" style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{
            padding: 20,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-start',
            borderBottom: `1px solid ${T.border}`,
            gap: 16,
            flexWrap: 'wrap',
          }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
                <span className="t-mono" style={{
                  fontSize: 13, color: T.textMut, letterSpacing: '0.1em',
                }}>BTC / USDT</span>
                <Pill tone="long">LONG FAVORÁVEL</Pill>
                <Pill tone="neutral">PERP · BINANCE</Pill>
              </div>
              <div style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'baseline',
                gap: 14,
                flexWrap: 'wrap',
              }}>
                <Num size={40} weight={300}>$78,919.37</Num>
                <Delta v={0.812} size={14} />
                <span className="t-mono" style={{ fontSize: 11, color: T.textDim }}>
                  · +$59.58 · 10,284s atrás
                </span>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {TIMEFRAMES.map((t, i) => {
                const active = t === ACTIVE_TF
                return (
                  <button
                    key={i}
                    style={{
                      background: active ? T.surface2 : 'transparent',
                      border: `1px solid ${active ? T.borderHi : T.border}`,
                      color: active ? T.text : T.textDim,
                      fontFamily: T.font.mono,
                      fontSize: 10,
                      letterSpacing: '0.1em',
                      padding: '4px 10px',
                      borderRadius: 2,
                      cursor: 'pointer',
                    }}
                  >{t}</button>
                )
              })}
            </div>
          </div>

          <div style={{ padding: 12 }}>
            <CandleChart height={280} />
          </div>

          <div style={{
            borderTop: `1px solid ${T.border}`,
            display: 'grid',
            gridTemplateColumns: 'repeat(6, 1fr)',
          }}>
            {LEVELS.map((x, i) => (
              <div key={i} style={{
                padding: '14px 18px',
                borderRight: i < LEVELS.length - 1 ? `1px solid ${T.border}` : 'none',
              }}>
                <div className="t-label" style={{ color: T.textDim }}>{x.l}</div>
                <div className="t-num" style={{
                  fontSize: 16, color: x.c, marginTop: 4,
                }}>{x.v}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* ── RADAR row ──────────────────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 20,
        marginBottom: 20,
      }}>
        <RadarTile label="BTC · 24H"       value="+0.81%" sub="$78,919"          tone="long"   spark={[4,3,5,4,6,5,7,8,7,9,8,10]} spread="Alta" />
        <RadarTile label="ETH · 24H"       value="+1.12%" sub="$3,127"           tone="long"   spark={[3,4,3,5,6,5,7,6,8,9,8,9]}  spread="Alta" />
        <RadarTile label="BTC DOMINANCE"   value="58.1%"  sub="Capital em BTC"   tone="amber"  spark={[5,5,6,5,6,6,5,6,6,5,6,6]}  spread="Estável" />
        <RadarTile label="USDT DOMINANCE"  value="6.9%"   sub="Normal"           tone="cobalt" spark={[6,6,5,6,5,6,5,5,6,5,5,6]}  spread="Estável" />
      </div>

      {/* ── Sentiment + Regime row ─────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1.2fr 1fr',
        gap: 20,
        marginBottom: 20,
      }}>
        <Card title="SENTIMENTO · LONG/SHORT RATIO" aside={<Pill tone="amber">NEUTRO</Pill>}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', height: 44, borderRadius: 3, overflow: 'hidden' }}>
                <div style={{
                  width: '48%',
                  background: T.short + '40',
                  border: `1px solid ${T.short}55`,
                  display: 'flex',
                  alignItems: 'center',
                  paddingLeft: 14,
                  fontFamily: T.font.mono,
                  fontSize: 12,
                  color: T.short,
                  letterSpacing: '0.1em',
                }}>SHORT 48%</div>
                <div style={{
                  width: '52%',
                  background: T.long + '40',
                  border: `1px solid ${T.long}55`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'flex-end',
                  paddingRight: 14,
                  fontFamily: T.font.mono,
                  fontSize: 12,
                  color: T.long,
                  letterSpacing: '0.1em',
                }}>52% LONG</div>
              </div>
              <div style={{
                marginTop: 12,
                fontFamily: T.font.mono,
                fontSize: 10,
                color: T.textDim,
                letterSpacing: '0.1em',
              }}>
                FONTE: BINANCE · BYBIT · OKX · CME — AGREGADO OI 24H
              </div>
            </div>
            <div style={{ textAlign: 'right', minWidth: 120 }}>
              <Num size={44} weight={300}>
                52<span style={{ fontSize: 20, color: T.textMut }}>%</span>
              </Num>
              <div style={{ marginTop: 2 }}>
                <Delta v={0.0} size={12} />
              </div>
              <div className="t-label" style={{ color: T.textDim, marginTop: 4 }}>5 MIN</div>
            </div>
          </div>
        </Card>

        <Card title="BTC · REGIME POR CAMADA">
          <LayerScores />
        </Card>
      </div>

      {/* ── Confluences + Liquidation + Signals row ─────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr 1.2fr',
        gap: 20,
        marginBottom: 20,
      }}>
        <Card title="CONFLUÊNCIAS · SCORE POR CAMADA">
          <ConfluenceBars />
        </Card>
        <Card title="LIQUIDATION MAP · ±5% · 12H" aside={<Pill tone="neutral">12H</Pill>}>
          <LiquidationMap />
        </Card>
        <Card title="ÚLTIMOS SINAIS" aside={<Pill tone="cobalt">4 HOJE</Pill>} noPad>
          <SignalsList />
        </Card>
      </div>

      {/* ── Sticky action bar ──────────────────────────────── */}
      <div style={{
        position: 'sticky',
        bottom: 20,
        zIndex: 10,
        display: 'flex',
        justifyContent: 'center',
        gap: 12,
      }}>
        <button style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '14px 24px',
          borderRadius: 999,
          background: T.surface,
          border: `1px solid ${T.long}66`,
          color: T.long,
          fontFamily: T.font.mono,
          fontSize: 12,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          cursor: 'pointer',
          boxShadow: '0 10px 30px rgba(0,0,0,0.4)',
        }}>
          <span style={{ fontSize: 14 }}>↑</span> PUMP ALERT
        </button>
        <button style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '14px 24px',
          borderRadius: 999,
          background: T.surface,
          border: `1px solid ${T.short}66`,
          color: T.short,
          fontFamily: T.font.mono,
          fontSize: 12,
          letterSpacing: '0.18em',
          textTransform: 'uppercase',
          cursor: 'pointer',
          boxShadow: '0 10px 30px rgba(0,0,0,0.4)',
        }}>
          <span style={{ fontSize: 14 }}>↓</span> CRASH ALERT
        </button>
      </div>
    </div>
  )
}
