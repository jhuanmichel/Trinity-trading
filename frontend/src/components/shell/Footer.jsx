/**
 * Footer.jsx — Shell footer: versao, health status, copyright.
 */
import { T } from '@/styles/tokens'
import { useHealthStatus } from '@/hooks/useHealthStatus'

export function Footer() {
  const { apiLatency, apiStatus, wsStatus } = useHealthStatus()
  const latencyLabel = apiLatency == null ? '—' : `${apiLatency}ms`

  return (
    <footer
      style={{
        borderTop: `1px solid ${T.border}`,
        padding: '16px 24px',
        marginTop: 40,
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        fontFamily: T.font.mono,
        fontSize: 10,
        letterSpacing: T.ls.label,
        textTransform: 'uppercase',
        color: T.textDim,
      }}
    >
      <span>TRINITY · v2.0.0</span>
      <span>
        LATÊNCIA {latencyLabel} · API {apiStatus} · WS {wsStatus}
      </span>
      <span>© 2026</span>
    </footer>
  )
}
