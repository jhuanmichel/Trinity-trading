/**
 * Pill.jsx — Primitive V2
 * Chip pequeno uppercase mono com tone semantico.
 * Tones: long | short | amber | cobalt | violet | neutral.
 */
import { T, alpha } from '../../styles/tokens'

const TONES = {
  long:    { bg: alpha(T.long,   '14'), bd: alpha(T.long,   '55'), fg: T.long    },
  short:   { bg: alpha(T.short,  '14'), bd: alpha(T.short,  '55'), fg: T.short   },
  amber:   { bg: alpha(T.amber,  '14'), bd: alpha(T.amber,  '55'), fg: T.amber   },
  cobalt:  { bg: alpha(T.cobalt, '14'), bd: alpha(T.cobalt, '55'), fg: T.cobalt  },
  violet:  { bg: alpha(T.violet, '14'), bd: alpha(T.violet, '55'), fg: T.violet  },
  neutral: { bg: T.surface2,            bd: T.border,              fg: T.textMut },
}

export function Pill({ tone = 'neutral', children, style, ...rest }) {
  const c = TONES[tone] || TONES.neutral
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontFamily: T.font.mono,
        fontSize: 10,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
        padding: '4px 8px',
        borderRadius: T.radius.sm,
        background: c.bg,
        border: `1px solid ${c.bd}`,
        color: c.fg,
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  )
}
