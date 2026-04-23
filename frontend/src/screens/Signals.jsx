/**
 * Signals.jsx — Placeholder S3b.
 */
import { T } from '@/styles/tokens'
import { Label } from '@/components/primitives'

export function Signals() {
  return (
    <div style={{ padding: 80, textAlign: 'center' }}>
      <Label>SIGNALS</Label>
      <div style={{
        marginTop: 16,
        color: T.textMut,
        fontFamily: T.font.mono,
        fontSize: 13,
        letterSpacing: T.ls.label,
        textTransform: 'uppercase',
      }}>
        Em breve · Filtros + lista + detalhe de sinais reais
      </div>
    </div>
  )
}
