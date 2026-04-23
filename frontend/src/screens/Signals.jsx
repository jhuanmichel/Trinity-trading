/**
 * Signals.jsx — Tela Signals real (S3b).
 * Grid 3 colunas: filtros / lista / detalhe.
 */
import { useMemo, useState } from 'react'
import { T } from '@/styles/tokens'
import { Label, Num, Pill, Card, Skel } from '@/components/primitives'
import { useRecentSignals } from '@/hooks/useRecentSignals'

const FILTERS_DIRECTION = [
  { id: 'all',   label: 'TODOS' },
  { id: 'LONG',  label: 'LONG'  },
  { id: 'SHORT', label: 'SHORT' },
]

const FILTERS_SOURCE = [
  { id: 'all',              label: 'TODOS'     },
  { id: 'pump_trader',      label: 'PUMP'      },
  { id: 'crash_trader',     label: 'CRASH'     },
  { id: 'blue_chip_trader', label: 'BLUE CHIP' },
]

export function Signals() {
  const { data: signals, isLoading } = useRecentSignals(100)
  const [dirFilter, setDirFilter]     = useState('all')
  const [srcFilter, setSrcFilter]     = useState('all')
  const [selectedId, setSelectedId]   = useState(null)

  const filtered = useMemo(() => {
    if (!signals) return []
    return signals.filter(s => {
      if (dirFilter !== 'all' && s.direction !== dirFilter) return false
      if (srcFilter !== 'all' && s.source   !== srcFilter) return false
      return true
    })
  }, [signals, dirFilter, srcFilter])

  const selected = useMemo(() => {
    if (!selectedId) return filtered[0]
    return filtered.find(s => (s.signal_id || s.id) === selectedId) || filtered[0]
  }, [filtered, selectedId])

  return (
    <div style={{ padding: '32px 24px 80px', maxWidth: 1680, margin: '0 auto' }}>
      <header style={{ marginBottom: 24 }}>
        <Label>SINAIS / HISTORICO</Label>
        <h1 style={{
          fontFamily: T.font.display,
          fontWeight: 500,
          fontSize: T.fs['3xl'],
          margin: '6px 0 0',
          letterSpacing: T.ls.snug,
        }}>
          Sinais recentes <span style={{ color: T.textMut }}>—</span>{' '}
          <span className="t-num" style={{ color: T.text }}>{filtered.length}</span>
        </h1>
      </header>

      <div style={{
        display: 'grid',
        gridTemplateColumns: '200px 1fr 360px',
        gap: 20,
      }}>
        {/* Filtros */}
        <aside>
          <Card title="DIRECAO" noPad>
            {FILTERS_DIRECTION.map(f => (
              <button
                key={f.id}
                onClick={() => setDirFilter(f.id)}
                style={filterBtnStyle(dirFilter === f.id)}
              >
                {f.label}
              </button>
            ))}
          </Card>

          <div style={{ height: 16 }} />

          <Card title="SOURCE" noPad>
            {FILTERS_SOURCE.map(f => (
              <button
                key={f.id}
                onClick={() => setSrcFilter(f.id)}
                style={filterBtnStyle(srcFilter === f.id)}
              >
                {f.label}
              </button>
            ))}
          </Card>
        </aside>

        {/* Lista */}
        <div>
          <Card noPad>
            {isLoading ? (
              <div style={{ padding: 20 }}>
                {[...Array(5)].map((_, i) => (
                  <div key={i} style={{ marginBottom: 12 }}>
                    <Skel w="100%" h={40} />
                  </div>
                ))}
              </div>
            ) : filtered.length === 0 ? (
              <div style={{
                padding: '60px 20px',
                textAlign: 'center',
                color: T.textDim,
                fontFamily: T.font.mono,
                fontSize: 11,
                letterSpacing: T.ls.label,
              }}>
                NENHUM SINAL COM OS FILTROS ATUAIS
              </div>
            ) : (
              filtered.slice(0, 50).map((s, i) => {
                const id = s.signal_id || s.id || i
                const isActive = (selected && (selected.signal_id || selected.id) === id)
                const sym = s.normalized_symbol || s.symbol || '?'
                return (
                  <div
                    key={id}
                    onClick={() => setSelectedId(id)}
                    className="t-hoverline"
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '80px 100px 70px 1fr 60px',
                      alignItems: 'center',
                      gap: 12,
                      padding: '12px 20px',
                      borderBottom: `1px solid ${T.border}`,
                      cursor: 'pointer',
                      background: isActive ? T.surface2 : 'transparent',
                    }}
                  >
                    <span className="t-mono" style={{ fontSize: 10, color: T.textDim }}>
                      {fmtTime(s.registered_at)}
                    </span>
                    <span className="t-mono" style={{ fontSize: 11, color: T.text }}>
                      {sym}
                    </span>
                    <Pill tone={s.direction === 'LONG' ? 'long' : 'short'} style={{ fontSize: 9 }}>
                      {s.direction}
                    </Pill>
                    <div style={{ display: 'flex', gap: 14, fontFamily: T.font.mono, fontSize: 10, color: T.textMut }}>
                      <span>{s.source || '—'}</span>
                      {s.conviction_tier && (
                        <span style={{ color: tierColor(s.conviction_tier) }}>
                          {s.conviction_tier}
                        </span>
                      )}
                    </div>
                    <span className="t-mono" style={{ fontSize: 11, color: T.text, textAlign: 'right' }}>
                      {typeof s.score === 'number' ? s.score.toFixed(0) : '—'}
                    </span>
                  </div>
                )
              })
            )}
          </Card>
        </div>

        {/* Detalhe */}
        <aside>
          {selected ? (
            <SignalDetail signal={selected} />
          ) : (
            <Card title="DETALHE">
              <div style={{ color: T.textDim, fontSize: 12 }}>
                Selecione um sinal na lista.
              </div>
            </Card>
          )}
        </aside>
      </div>
    </div>
  )
}

function SignalDetail({ signal }) {
  const sym     = signal.normalized_symbol || signal.symbol || '?'
  const dir     = signal.direction || '?'
  const score   = typeof signal.score    === 'number' ? signal.score.toFixed(0) : '—'
  const scoreV2 = typeof signal.score_v2 === 'number' ? signal.score_v2.toFixed(0) : null

  return (
    <Card title="DETALHE DO SINAL" aside={
      <Pill tone={dir === 'LONG' ? 'long' : 'short'}>{dir}</Pill>
    }>
      <div style={{ marginBottom: 16 }}>
        <Label>SIMBOLO</Label>
        <div style={{ fontFamily: T.font.mono, fontSize: 16, color: T.text, marginTop: 4 }}>
          {sym}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 16 }}>
        <div>
          <Label>SCORE V1</Label>
          <Num size={24}>{score}</Num>
        </div>
        {scoreV2 !== null && (
          <div>
            <Label>SCORE V2</Label>
            <Num size={24} style={{ color: T.cobalt }}>{scoreV2}</Num>
          </div>
        )}
      </div>

      {signal.conviction_tier && (
        <div style={{ marginBottom: 16 }}>
          <Label>TIER</Label>
          <div style={{
            fontFamily: T.font.mono,
            fontSize: 13,
            color: tierColor(signal.conviction_tier),
            marginTop: 4,
            letterSpacing: '0.1em',
          }}>
            {signal.conviction_tier}
          </div>
        </div>
      )}

      {(signal.entry || signal.stop_loss || signal.tp1 || signal.tp2) && (
        <div style={{ marginBottom: 16 }}>
          <Label>NIVEIS</Label>
          <div style={{ marginTop: 6, fontFamily: T.font.mono, fontSize: 11, color: T.textMut }}>
            {signal.entry != null && (
              <div>ENTRY <span style={{ color: T.text }}>{formatNum(signal.entry)}</span></div>
            )}
            {signal.stop_loss != null && (
              <div>SL <span style={{ color: T.short }}>{formatNum(signal.stop_loss)}</span></div>
            )}
            {signal.tp1 != null && (
              <div>TP1 <span style={{ color: T.long }}>{formatNum(signal.tp1)}</span></div>
            )}
            {signal.tp2 != null && (
              <div>TP2 <span style={{ color: T.long }}>{formatNum(signal.tp2)}</span></div>
            )}
          </div>
        </div>
      )}

      <div>
        <Label>STATUS</Label>
        <div style={{ fontFamily: T.font.mono, fontSize: 12, color: T.text, marginTop: 4 }}>
          {signal.status || 'OPEN'}
        </div>
      </div>
    </Card>
  )
}

function filterBtnStyle(active) {
  return {
    display: 'block',
    width: '100%',
    padding: '10px 20px',
    background: active ? T.surface2 : 'transparent',
    border: 'none',
    borderBottom: `1px solid ${T.border}`,
    fontFamily: T.font.mono,
    fontSize: 11,
    letterSpacing: T.ls.label,
    color: active ? T.text : T.textMut,
    cursor: 'pointer',
    textAlign: 'left',
  }
}

function fmtTime(iso) {
  if (!iso) return '—'
  try {
    return iso.slice(11, 19)
  } catch (_) {
    return '—'
  }
}

function formatNum(v) {
  if (typeof v !== 'number') return '—'
  if (v >= 1000) return v.toLocaleString('en-US', { maximumFractionDigits: 2 })
  if (v >= 1)    return v.toFixed(4)
  return v.toFixed(6)
}

function tierColor(tier) {
  const M = {
    GOLD:    '#F0B429',
    SILVER:  '#8B8F98',
    BRONZE:  '#C44040',
    HIGH:    '#00D68F',
    STRONG:  '#00D68F',
    EXTREME: '#00D68F',
    MICRO:   '#8B8F98',
    LOW:     '#5A5E66',
    AVOID:   '#FF5B5B',
  }
  return M[tier] || '#8B8F98'
}
