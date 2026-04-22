/**
 * Delta.jsx — Primitive V2
 * Variacao percentual com seta triangular + cor semantica.
 * Props: v (number), size (default 12), decimals (default 2).
 */
import { T } from '../../styles/tokens'

export function Delta({ v, size = 12, decimals = 2, style, ...rest }) {
  const pos = v >= 0
  return (
    <span
      className="t-num"
      style={{
        color: pos ? T.long : T.short,
        fontSize: size,
        fontWeight: 500,
        ...style,
      }}
      {...rest}
    >
      {pos ? '▲' : '▼'} {Math.abs(v).toFixed(decimals)}%
    </span>
  )
}
