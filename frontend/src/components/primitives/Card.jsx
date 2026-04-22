/**
 * Card.jsx — Primitive V2
 * Container com header opcional (title + aside) e corpo com padding.
 * Props: title, aside (ReactNode), pad (default 20), noPad (bool).
 */
import { T } from '../../styles/tokens'
import { Label } from './Label'

export function Card({
  title,
  aside,
  children,
  style,
  pad = 20,
  noPad = false,
  ...rest
}) {
  return (
    <div className="t-card" style={style} {...rest}>
      {(title || aside) && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '14px 20px',
            borderBottom: `1px solid ${T.border}`,
          }}
        >
          {title && <Label>{title}</Label>}
          {aside}
        </div>
      )}
      <div style={{ padding: noPad ? 0 : pad }}>{children}</div>
    </div>
  )
}
