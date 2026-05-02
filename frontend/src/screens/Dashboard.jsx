/**
 * Dashboard.jsx — Trinity V2 tela principal.
 *
 * WIRED em 2026-05-02: substitui valores DEMO por consumo real de:
 *   - useConviction()   → /api/current-state (poll 30s)
 *   - useRecentSignals()→ /api/signal-history
 * Componentes auxiliares (CandleChart, LiquidationMap, ConfluenceBars,
 * RadarTile, LayerScores) ainda podem ter mock interno — wiring incremental.
 */
import { useEffect, useState } from 'react'
import { T } from '@/styles/tokens'
import { Card, Label, Num, Pill, Delta } from '@/components/primitives'
import { ConvictionDial }  from '@/components/domain/ConvictionDial'
import { ConfluenceBars }  from '@/components/domain/ConfluenceBars'
import { LayerScores }     from '@/components/domain/LayerScores'
import { RadarTile }       from '@/components/domain/RadarTile'
import { CandleChart }     from '@/components/domain/CandleChart'
import { LiquidationMap }  from '@/components/domain/LiquidationMap'
import { SignalsList }     from '@/components/domain/SignalsList'
import { useConviction }   from '@/hooks/useConviction'
import { LAYER_LABELS, LAYER_ORDER } from '@/lib/backendMapping'

const TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h', '1D']
const ACTIVE_TF = '15m'

// ── Helpers ─────────────────────────────────────────────────────────────────

function fmtMoney(n, dec = 2) {
  if (!Number.isFinite(n) || n <= 0) return '—'
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}`
}

function secondsAgo(iso) {
  if (!iso) return null
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return null
  return Math.max(0, Math.floor((Date.now() - t) / 1000))
}

function fmtAgo(sec) {
  if (sec == null) return '—'
  if (sec < 60)        return `${sec}s atrás`
  if (sec < 3600)      return `${Math.floor(sec / 60)}min atrás`
  if (sec < 86400)     return `${Math.floor(sec / 3600)}h atrás`
  return `${Math.floor(sec / 86400)}d atrás`
}

function directionLabel(dir) {
  if (!dir || dir === 'NEUTRO' || dir === 'AGUARDANDO') return 'Neutro'
  if (dir === 'LONG')  return 'LONG favorável'
  if (dir === 'SHORT') return 'SHORT favorável'
  return dir
}

function directionColor(dir) {
  if (dir === 'LONG')  return T.long
  if (dir === 'SHORT') return T.short
  return T.amber
}

function directionPillTone(dir) {
  if (dir === 'LONG')  return 'long'
  if (dir === 'SHORT') return 'short'
  return 'amber'
}

// Use this hook to refresh the relative time display every second
function useTick(ms = 1000) {
  const [, setT] = useState(0)
  useEffect(() => {
    const id = setInterval(() => setT((x) => x + 1), ms)
    return () => clearInterval(id)
  }, [ms])
}

export function Dashboard() {
  const { conviction, layers, tradePlan, meta, isLoading, error } = useConviction()
  useTick(1000)  // re-render a cada 1s para atualizar "Xs atrás"

  const ago = secondsAgo(meta?.lastUpdated)
  const isStale = ago != null && ago > 90 * 60   // > 90min sem update = stale

  const dirLabel  = directionLabel(conviction.direction)
  const dirColor  = directionColor(conviction.direction)
  const dirPill   = directionPillTone(conviction.direction)

  const score = conviction.score || 0
  const price = conviction.price || 0

  // CONV chips dinâmicos: highlight camadas que passam threshold do bias
  const convChips = LAYER_ORDER.map((k) => {
    const v = layers[k] || 50
    const on = (conviction.direction === 'LONG' && v >= 60) ||
               (conviction.direction === 'SHORT' && v <= 40)
    const tone = conviction.direction === 'SHORT' ? 'short' : 'long'
    return { k: LAYER_LABELS[k], on, tone }
  })

  const levels = [
    { l: 'ENTRY',     v: fmtMoney(tradePlan.entry), c: T.amber },
    { l: 'STOP LOSS', v: fmtMoney(tradePlan.stop),  c: T.short },
    { l: 'TP 1',      v: fmtMoney(tradePlan.tp1),   c: T.long  },
    { l: 'TP 2',      v: fmtMoney(tradePlan.tp2),   c: T.long  },
    { l: 'TP 3',      v: fmtMoney(tradePlan.tp3),   c: T.long  },
    {
      l: `ATR · ${(tradePlan.atr_pct || 0).toFixed(2)}%`,
      v: 'R/R 1:1.4',
      c: T.text,
    },
  ]

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
            <span style={{ color: dirColor }}>{dirLabel}</span>
          </h1>
          <div style={{
            marginTop: 8,
            fontFamily: T.font.mono,
            fontSize: 11,
            color: isStale ? T.short : T.textMut,
            letterSpacing: '0.06em',
          }}>
            {isLoading
              ? 'Carregando…'
              : error
                ? `Erro: ${String(error.message || error).slice(0, 80)}`
                : `Atualizado ${fmtAgo(ago)} · ${conviction.confluences}/6 confluências · ${
                    isStale ? '⚠ STALE' : 'próximo tick em ' + Math.max(0, 30 - (ago % 30)) + 's'
                  }`}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <Pill tone="violet">MACRO · {conviction.direction === 'NEUTRO' ? 'NEUTRO' : conviction.direction}</Pill>
          <Pill tone="amber">SCORE · {score.toFixed(1)}</Pill>
          <Pill tone="cobalt">{conviction.marketStructure}</Pill>
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
              {conviction.confluences}/6 CONFLU.
            </span>
          </div>
          <ConvictionDial value={Math.round(score)} />
          <div style={{
            marginTop: 12,
            display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr',
            gap: 6,
          }}>
            {convChips.map((c, i) => {
              const tColor = c.tone === 'short' ? T.short : T.long
              return (
                <div key={i} style={{
                  padding: '8px 0',
                  textAlign: 'center',
                  border: `1px solid ${c.on ? tColor + '55' : T.border}`,
                  background: c.on ? tColor + '0e' : 'transparent',
                  borderRadius: 2,
                  fontFamily: T.font.mono,
                  fontSize: 10,
                  color: c.on ? tColor : T.textDim,
                  letterSpacing: '0.14em',
                }}>{c.k}</div>
              )
            })}
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
                <Pill tone={dirPill}>{dirLabel.toUpperCase()}</Pill>
                <Pill tone="neutral">PERP · MEXC</Pill>
              </div>
              <div style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'baseline',
                gap: 14,
                flexWrap: 'wrap',
              }}>
                <Num size={40} weight={300}>{fmtMoney(price)}</Num>
                <span className="t-mono" style={{ fontSize: 11, color: T.textDim }}>
                  · {fmtAgo(ago)}
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
            {levels.map((x, i) => (
              <div key={i} style={{
                padding: '14px 18px',
                borderRight: i < levels.length - 1 ? `1px solid ${T.border}` : 'none',
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
        <RadarTile label="BTC · 24H"       value="+0.81%" sub={fmtMoney(price, 0)}    tone="long"   spark={[4,3,5,4,6,5,7,8,7,9,8,10]} spread="Alta" />
        <RadarTile label="ETH · 24H"       value="+1.12%" sub="—"                     tone="long"   spark={[3,4,3,5,6,5,7,6,8,9,8,9]}  spread="Alta" />
        <RadarTile label="BTC DOMINANCE"   value="58.1%"  sub="Capital em BTC"        tone="amber"  spark={[5,5,6,5,6,6,5,6,6,5,6,6]}  spread="Estável" />
        <RadarTile label="USDT DOMINANCE"  value="6.9%"   sub="Normal"                tone="cobalt" spark={[6,6,5,6,5,6,5,5,6,5,5,6]}  spread="Estável" />
      </div>

      {/* ── Sentiment + Regime row ─────────────────────────── */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: '1.2fr 1fr',
        gap: 20,
        marginBottom: 20,
      }}>
        <Card title="SENTIMENTO · LONG/SHORT RATIO" aside={<Pill tone={dirPill}>{dirLabel.toUpperCase()}</Pill>}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
            <div style={{ flex: 1 }}>
              <div style={{ display: 'flex', height: 44, borderRadius: 3, overflow: 'hidden' }}>
                <div style={{
                  width: `${100 - score}%`,
                  background: T.short + '40',
                  border: `1px solid ${T.short}55`,
                  display: 'flex',
                  alignItems: 'center',
                  paddingLeft: 14,
                  fontFamily: T.font.mono,
                  fontSize: 12,
                  color: T.short,
                  letterSpacing: '0.1em',
                }}>SHORT {(100 - score).toFixed(0)}%</div>
                <div style={{
                  width: `${score}%`,
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
                }}>{score.toFixed(0)}% LONG</div>
              </div>
              <div style={{
                marginTop: 12,
                fontFamily: T.font.mono,
                fontSize: 10,
                color: T.textDim,
                letterSpacing: '0.1em',
              }}>
                FONTE: TRINITY INST_SCORE · 6 CAMADAS · {conviction.confluences}/6 CONFLU
              </div>
            </div>
            <div style={{ textAlign: 'right', minWidth: 120 }}>
              <Num size={44} weight={300}>
                {score.toFixed(0)}<span style={{ fontSize: 20, color: T.textMut }}>%</span>
              </Num>
              <div style={{ marginTop: 2 }}>
                <Delta v={0.0} size={12} />
              </div>
              <div className="t-label" style={{ color: T.textDim, marginTop: 4 }}>SCORE</div>
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
        <Card title="ÚLTIMOS SINAIS" aside={<Pill tone="cobalt">LIVE</Pill>} noPad>
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
