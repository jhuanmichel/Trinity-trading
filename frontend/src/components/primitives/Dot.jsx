/**
 * Dot.jsx — Primitive V2
 * Bolinha indicador. variant='live' pulsa em verde.
 */
export function Dot({ variant = 'default', style, ...rest }) {
  const className = variant === 'live' ? 't-dot t-dot-live' : 't-dot'
  return <span className={className} style={style} {...rest} />
}
