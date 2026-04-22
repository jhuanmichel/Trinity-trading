/**
 * Logo.jsx — Trinity V2 brand mark.
 * Pirâmide isométrica SVG (3 triangulos + dot central) + optional wordmark.
 */
import { T } from '@/styles/tokens'

export function Logo({ size = 28, showText = true, tagline = 'INSTITUTIONAL CRYPTO SIGNALS' }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
      <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
        <path d="M4 6 L14 2 L24 6 L14 14 Z" fill="none" stroke={T.long} strokeWidth="1.5" />
        <path d="M4 6 L14 14 L4 22 Z"        fill="none" stroke={T.text} strokeWidth="1.5" opacity="0.6" />
        <path d="M24 6 L14 14 L24 22 Z"      fill="none" stroke={T.text} strokeWidth="1.5" opacity="0.6" />
        <circle cx="14" cy="14" r="1.4" fill={T.long} />
      </svg>
      {showText && (
        <div>
          <div style={{
            fontFamily: T.font.display,
            fontWeight: T.fw.semi,
            fontSize: 15,
            letterSpacing: T.ls.TITLE,
            color: T.text,
            lineHeight: 1,
          }}>
            TRINITY
          </div>
          {tagline && (
            <div className="t-label" style={{ fontSize: 8, marginTop: 3, color: T.textDim }}>
              {tagline}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
