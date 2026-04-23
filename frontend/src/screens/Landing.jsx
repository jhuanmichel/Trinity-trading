/**
 * Landing.jsx — Placeholder S3b.
 */
import { T } from '@/styles/tokens'
import { Label } from '@/components/primitives'

export function Landing() {
  return (
    <div style={{ padding: 80, textAlign: 'center' }}>
      <Label>OVERVIEW</Label>
      <div style={{
        marginTop: 16,
        color: T.textMut,
        fontFamily: T.font.mono,
        fontSize: 13,
        letterSpacing: T.ls.label,
        textTransform: 'uppercase',
      }}>
        Em breve · Hero institucional + visao geral do sistema
      </div>
    </div>
  )
}
