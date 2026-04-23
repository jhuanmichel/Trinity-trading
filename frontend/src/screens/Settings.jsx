/**
 * Settings.jsx — Placeholder S3b.
 */
import { T } from '@/styles/tokens'
import { Label } from '@/components/primitives'

export function Settings() {
  return (
    <div style={{ padding: 80, textAlign: 'center' }}>
      <Label>SETTINGS</Label>
      <div style={{
        marginTop: 16,
        color: T.textMut,
        fontFamily: T.font.mono,
        fontSize: 13,
        letterSpacing: T.ls.label,
        textTransform: 'uppercase',
      }}>
        Em breve · Toggles de filtros, thresholds, notificacoes
      </div>
    </div>
  )
}
