/**
 * Trinity V2 — Design Tokens
 *
 * Fonte unica da verdade para cores, tipografia, espacamentos, radius.
 * Namespace V2: coexiste com V1 (globals.css). V2 primitives importam daqui;
 * V1 permanece intocado usando suas proprias vars (--green, --bg-base, etc.).
 *
 * Violacao: hardcoded hex/px em primitive V2 = regressao do contrato.
 */

export const T = {
  // ═══ COLORS ═══════════════════════════════════════════
  // Background layers (escuros institucionais)
  bg:        '#0A0B0D',       // page
  surface:   '#0F1115',       // cards
  surface2:  '#14171D',       // elevated / hover
  surface3:  '#1A1E26',       // highlight / active

  // Borders
  border:    '#1D2026',       // default
  borderHi:  '#2A2E37',       // hover / emphasized

  // Text
  text:      '#E8E6E1',       // primary (off-white)
  textMut:   '#8B8F98',       // secondary
  textDim:   '#5A5E66',       // labels / timestamps

  // Semantic accents
  long:      '#00D68F',
  longDim:   '#00A06B',
  short:     '#FF5B5B',
  shortDim:  '#C44040',
  amber:     '#F0B429',
  amberDim:  '#A87C14',
  cobalt:    '#4E7CFF',
  cobaltDim: '#3861D6',
  violet:    '#9B5CF6',
  violetDim: '#7B3FDB',

  // ═══ TYPOGRAPHY ═══════════════════════════════════════
  font: {
    mono:    "'IBM Plex Mono', ui-monospace, 'SF Mono', Menlo, monospace",
    sans:    "'Inter', 'IBM Plex Sans', system-ui, -apple-system, sans-serif",
    display: "'Space Grotesk', 'Inter', system-ui, sans-serif",
  },

  // Font size scale (px)
  fs: {
    xs:    10,
    sm:    11,
    base:  13,
    md:    15,
    lg:    17,
    xl:    20,
    '2xl': 28,
    '3xl': 32,
    '4xl': 48,
    '5xl': 76,
  },

  // Font weights — 300, 400, 500 sao os usados; 600 so em display
  fw: {
    light:   300,
    regular: 400,
    medium:  500,
    semi:    600,
  },

  // Letter spacing
  ls: {
    tight:  '-0.03em',
    snug:   '-0.02em',
    normal: '0',
    mono:   '0.02em',
    label:  '0.14em',
    LABEL:  '0.18em',
    TITLE:  '0.24em',
  },

  // ═══ SPACING ══════════════════════════════════════════
  space: {
    0:  0,
    1:  4,
    2:  8,
    3:  12,
    4:  16,
    5:  20,
    6:  24,
    8:  32,
    10: 40,
    12: 48,
    16: 64,
    20: 80,
    24: 100,
  },

  // ═══ RADIUS ═══════════════════════════════════════════
  // Institucional = radius discreto (nada arredondado exceto dots)
  radius: {
    xs:   2,
    sm:   3,
    md:   4,
    lg:   6,
    full: 9999,
  },

  // ═══ Z-INDEX ══════════════════════════════════════════
  z: {
    ticker:   1,
    topbar:   20,
    dropdown: 30,
    modal:    100,
    toast:    200,
  },
}

/**
 * Helper: concatena 2 hex chars de opacidade ao final de uma cor.
 * Ex: alpha(T.long, '14') => '#00D68F14' (~8% opacidade).
 */
export const alpha = (color, hex2) => `${color}${hex2}`
