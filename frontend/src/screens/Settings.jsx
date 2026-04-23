/**
 * Settings.jsx — Tela Settings real (S3b).
 * Toggles de notificacoes/filtros + persistencia em localStorage.
 */
import { useState } from 'react'
import { T } from '@/styles/tokens'
import { Label, Card } from '@/components/primitives'

const STORAGE_KEY = 'trinity:settings'

function loadInitial() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY))
    if (stored && typeof stored === 'object') return stored
  } catch (_) {}
  return {
    push:       false,
    telegram:   true,
    sound:      false,
    minConv:    70,
    showShadow: true,
  }
}

function persist(next) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch (_) {}
}

function Toggle({ on, onChange }) {
  return (
    <button
      onClick={onChange}
      style={{
        width: 40,
        height: 22,
        borderRadius: 999,
        border: `1px solid ${on ? T.long : T.border}`,
        background: on ? T.long + '22' : T.surface2,
        position: 'relative',
        cursor: 'pointer',
        padding: 0,
        transition: 'all 150ms',
      }}
    >
      <div style={{
        position: 'absolute',
        top: 2,
        left: on ? 20 : 2,
        width: 16,
        height: 16,
        borderRadius: '50%',
        background: on ? T.long : T.textDim,
        transition: 'all 150ms',
      }} />
    </button>
  )
}

function Row({ label, hint, right }) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      padding: '14px 20px',
      borderBottom: `1px solid ${T.border}`,
    }}>
      <div>
        <div style={{ fontSize: 13, color: T.text }}>{label}</div>
        {hint && (
          <div className="t-mono" style={{ fontSize: 10, color: T.textDim, marginTop: 3, letterSpacing: '0.08em' }}>
            {hint}
          </div>
        )}
      </div>
      {right}
    </div>
  )
}

export function Settings() {
  const [state, setState] = useState(loadInitial)

  const toggle = k => setState(s => {
    const next = { ...s, [k]: !s[k] }
    persist(next)
    return next
  })

  const setMinConv = v => setState(s => {
    const next = { ...s, minConv: v }
    persist(next)
    return next
  })

  return (
    <div style={{ padding: '32px 24px 80px', maxWidth: 1100, margin: '0 auto' }}>
      <header style={{ marginBottom: 32 }}>
        <Label>CONFIG / PREFERENCIAS</Label>
        <h1 style={{
          fontFamily: T.font.display,
          fontWeight: 500,
          fontSize: T.fs['3xl'],
          margin: '6px 0 0',
          letterSpacing: T.ls.snug,
        }}>
          Configuracoes
        </h1>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
        <Card title="NOTIFICACOES" noPad>
          <Row
            label="Push no navegador"
            hint="CHROME · SAFARI · FIREFOX"
            right={<Toggle on={state.push} onChange={() => toggle('push')} />}
          />
          <Row
            label="Telegram"
            hint="@TRINITY_BOT"
            right={<Toggle on={state.telegram} onChange={() => toggle('telegram')} />}
          />
          <Row
            label="Som ao receber sinal"
            right={<Toggle on={state.sound} onChange={() => toggle('sound')} />}
          />
        </Card>

        <Card title="FILTROS DE VISUALIZACAO" noPad>
          <Row
            label="Mostrar sinais shadow"
            hint="SINAIS GERADOS MAS NAO ENVIADOS"
            right={<Toggle on={state.showShadow} onChange={() => toggle('showShadow')} />}
          />
          <Row
            label="Score minimo visivel"
            hint={`ATUAL: ${state.minConv}`}
            right={
              <input
                type="range"
                min="0"
                max="100"
                step="5"
                value={state.minConv}
                onChange={e => setMinConv(Number(e.target.value))}
                style={{ width: 140 }}
              />
            }
          />
        </Card>
      </div>

      <div style={{ marginTop: 20 }}>
        <Card title="SOBRE" noPad>
          <Row label="Versao"    hint="Trinity v2.0.0" right={null} />
          <Row label="Dashboard" hint="trinity-trading.onrender.com" right={null} />
          <Row label="Suporte"   hint="@jhuanmichel" right={null} />
        </Card>
      </div>
    </div>
  )
}
