/**
 * TopBar.jsx — Trinity V2 chrome superior (fiel ao mockup Trinity Redesign).
 *
 * Layout:
 *   [Logo + Nav]   [MACRO · LIVE · clock] [⌘K BUSCAR] [avatar]
 */
import { T } from '@/styles/tokens'
import { Logo } from '@/components/brand/Logo'
import { Dot } from '@/components/primitives'
import { useRouterStore, ROUTES } from '@/store/routerStore'
import { useClock } from '@/hooks/useClock'
import { useMacroStatus } from '@/hooks/useMacroStatus'

const NAV_ITEMS = [
  { id: ROUTES.DASHBOARD, label: 'Dashboard' },
  { id: ROUTES.SIGNALS,   label: 'Sinais'   },
  { id: ROUTES.LANDING,   label: 'Overview' },
  { id: ROUTES.SETTINGS,  label: 'Config'   },
]

function macroColor(status) {
  if (status === 'BULL') return T.long
  if (status === 'BEAR') return T.short
  return T.amber
}

export function TopBar() {
  const route    = useRouterStore((s) => s.route)
  const setRoute = useRouterStore((s) => s.setRoute)
  const time     = useClock()
  const { macroStatus } = useMacroStatus()

  return (
    <div
      style={{
        height: 56,
        borderBottom: `1px solid ${T.border}`,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 24px',
        background: T.bg,
        position: 'sticky',
        top: 0,
        zIndex: T.z.topbar,
      }}
    >
      {/* Esquerda: logo + nav */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 40 }}>
        <Logo />
        <nav style={{ display: 'flex', gap: 2 }}>
          {NAV_ITEMS.map((n) => {
            const active = n.id === route
            return (
              <button
                key={n.id}
                onClick={() => setRoute(n.id)}
                style={{
                  background: 'none',
                  border: 'none',
                  cursor: 'pointer',
                  padding: '8px 14px',
                  borderRadius: T.radius.sm,
                  fontFamily: T.font.mono,
                  fontSize: 11,
                  letterSpacing: T.ls.LABEL,
                  textTransform: 'uppercase',
                  color: active ? T.text : T.textDim,
                  position: 'relative',
                  transition: 'color 150ms',
                }}
              >
                {n.label}
                {active && (
                  <span
                    style={{
                      position: 'absolute',
                      bottom: -18,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      width: 16,
                      height: 1.5,
                      background: T.long,
                    }}
                  />
                )}
              </button>
            )
          })}
        </nav>
      </div>

      {/* Direita: status + search + avatar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 20 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            fontFamily: T.font.mono,
            fontSize: 11,
            color: T.textMut,
          }}
        >
          <span>
            <span style={{ color: T.textDim, letterSpacing: T.ls.label }}>MACRO </span>
            <span style={{ color: macroColor(macroStatus) }}>{macroStatus || 'NEUTRO'}</span>
          </span>
          <span style={{ width: 1, height: 12, background: T.border }} />
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <Dot variant="live" />
            <span style={{ letterSpacing: T.ls.label }}>LIVE</span>
          </span>
          <span style={{ width: 1, height: 12, background: T.border }} />
          <span className="t-num" style={{ color: T.textDim }}>{time}</span>
        </div>

        {/* Search button (placeholder — atalho Cmd+K pode ser wireado depois) */}
        <button
          style={{
            background: T.surface,
            border: `1px solid ${T.border}`,
            color: T.text,
            fontFamily: T.font.mono,
            fontSize: 11,
            letterSpacing: T.ls.label,
            textTransform: 'uppercase',
            padding: '8px 14px',
            borderRadius: T.radius.sm,
            cursor: 'pointer',
          }}
        >
          ⌘ K · Buscar
        </button>

        {/* Avatar */}
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: '50%',
            background: T.surface2,
            border: `1px solid ${T.borderHi}`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontFamily: T.font.mono,
            fontSize: 11,
            color: T.text,
          }}
        >
          VC
        </div>
      </div>
    </div>
  )
}
