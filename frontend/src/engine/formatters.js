/**
 * formatters.js — Utilitários de formatação de dados
 */
import { format, formatDistanceToNow, parseISO } from 'date-fns'

/**
 * Formata preço adaptativo (ex: 83420.50 → $83,420.50)
 */
export function fmtPrice(price, currency = '$') {
  if (price === null || price === undefined) return '—'
  const n = Number(price)
  if (isNaN(n)) return '—'

  // Determina decimais pelo tamanho
  let decimals = 2
  if (n < 0.01)    decimals = 6
  else if (n < 1)  decimals = 4
  else if (n < 10) decimals = 3

  return `${currency}${n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
}

/**
 * Formata variação percentual com sinal
 */
export function fmtPct(pct) {
  if (pct === null || pct === undefined) return '—'
  const n = Number(pct)
  if (isNaN(n)) return '—'
  const sign = n >= 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

/**
 * Formata número grande (volume, OI, etc.)
 */
export function fmtLarge(n) {
  if (!n && n !== 0) return '—'
  const v = Number(n)
  if (v >= 1_000_000_000) return `${(v / 1_000_000_000).toFixed(2)}B`
  if (v >= 1_000_000)     return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 1_000)         return `${(v / 1_000).toFixed(1)}K`
  return v.toFixed(2)
}

/**
 * Formata timestamp ISO para display
 */
export function fmtTime(ts) {
  if (!ts) return '—'
  try {
    const d = typeof ts === 'string' ? parseISO(ts) : new Date(ts)
    return format(d, 'HH:mm:ss')
  } catch {
    return '—'
  }
}

/**
 * Formata data completa
 */
export function fmtDate(ts) {
  if (!ts) return '—'
  try {
    const d = typeof ts === 'string' ? parseISO(ts) : new Date(ts)
    return format(d, 'dd/MM HH:mm')
  } catch {
    return '—'
  }
}

/**
 * Formata tempo relativo ("há 3 minutos")
 */
export function fmtRelative(ts) {
  if (!ts) return '—'
  try {
    const d = typeof ts === 'string' ? parseISO(ts) : new Date(ts)
    return formatDistanceToNow(d, { addSuffix: true })
  } catch {
    return '—'
  }
}

/**
 * Cor CSS para variação positiva/negativa
 */
export function pctColor(pct) {
  const n = Number(pct)
  if (isNaN(n)) return 'var(--text-muted)'
  if (n > 0)    return 'var(--green)'
  if (n < 0)    return 'var(--red)'
  return 'var(--text-secondary)'
}

/**
 * Cor CSS para direção do sinal
 */
export function directionColor(direction) {
  const d = (direction ?? '').toUpperCase()
  if (d === 'LONG'   || d === 'BULLISH') return 'var(--green)'
  if (d === 'SHORT'  || d === 'BEARISH') return 'var(--red)'
  return 'var(--text-secondary)'
}

/**
 * Classe CSS para direção
 */
export function directionClass(direction) {
  const d = (direction ?? '').toUpperCase()
  if (d === 'LONG'   || d === 'BULLISH') return 'text-green'
  if (d === 'SHORT'  || d === 'BEARISH') return 'text-red'
  return 'text-secondary'
}

/**
 * Trunca string com ellipsis
 */
export function truncate(str, max = 20) {
  if (!str) return ''
  return str.length > max ? `${str.slice(0, max)}…` : str
}
