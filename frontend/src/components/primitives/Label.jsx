/**
 * Label.jsx — Primitive V2
 * Label pequena uppercase em mono com letter-spacing alto.
 * Uso: identificar secoes, categorias, timestamps. Nao usar pra prosa.
 */
import { T } from '../../styles/tokens'

export function Label({ children, color, style, ...rest }) {
  return (
    <div
      className="t-label"
      style={{ color: color || T.textDim, ...style }}
      {...rest}
    >
      {children}
    </div>
  )
}
