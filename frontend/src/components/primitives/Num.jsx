/**
 * Num.jsx — Primitive V2
 * Numero em IBM Plex Mono + tabular-nums.
 * Props: size (px), color (T.*), weight (default 500).
 */
import { T } from '../../styles/tokens'

export function Num({
  children,
  size = 20,
  color,
  weight = 500,
  style,
  ...rest
}) {
  return (
    <span
      className="t-num"
      style={{
        fontSize: size,
        color: color || T.text,
        fontWeight: weight,
        letterSpacing: '-0.01em',
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  )
}
