/**
 * CandleChart.jsx — Candlestick SVG puro (S3b).
 *
 * Props:
 *   candles: [{o, h, l, c, t?}]  - array OHLCV
 *   height:  px (default 280)
 *   levels?: { entry?, sl?, tp1?, tp2?, tp3? }  - linhas horizontais
 *   lastPriceMark?: boolean  - destaca ultimo preco
 */
import { T } from '@/styles/tokens'

export function CandleChart({ candles = [], height = 280, levels = {}, lastPriceMark = true }) {
  if (!candles || candles.length === 0) {
    return (
      <div style={{
        padding: '60px 20px',
        textAlign: 'center',
        color: T.textDim,
        fontFamily: T.font.mono,
        fontSize: 11,
        letterSpacing: T.ls.label,
      }}>
        SEM DADOS DE CANDLE
      </div>
    )
  }

  const W = 640
  const H = height
  const padY = 10

  const allLows  = candles.map(c => c.l)
  const allHighs = candles.map(c => c.h)
  const includeLevels = Object.values(levels).filter(v => typeof v === 'number')
  const minData = Math.min(...allLows, ...includeLevels) * 0.998
  const maxData = Math.max(...allHighs, ...includeLevels) * 1.002

  const xWidth = W / candles.length
  const scaleY = v => H - ((v - minData) / (maxData - minData || 1)) * (H - padY * 2) - padY

  const levelRows = [
    levels.entry != null ? { v: levels.entry, c: T.amber, label: `ENTRY ${fmtPrice(levels.entry)}` } : null,
    levels.sl    != null ? { v: levels.sl,    c: T.short, label: `SL ${fmtPrice(levels.sl)}`       } : null,
    levels.tp1   != null ? { v: levels.tp1,   c: T.long,  label: `TP1 ${fmtPrice(levels.tp1)}`     } : null,
    levels.tp2   != null ? { v: levels.tp2,   c: T.long,  label: `TP2 ${fmtPrice(levels.tp2)}`     } : null,
    levels.tp3   != null ? { v: levels.tp3,   c: T.long,  label: `TP3 ${fmtPrice(levels.tp3)}`     } : null,
  ].filter(Boolean)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H, display: 'block' }}>
      {/* Grid */}
      {[0.2, 0.4, 0.6, 0.8].map((t, i) => (
        <line
          key={i}
          x1="0" x2={W}
          y1={H * t} y2={H * t}
          stroke={T.border}
          strokeWidth="0.5"
          strokeDasharray="2 6"
        />
      ))}

      {/* Level lines */}
      {levelRows.map((lvl, i) => (
        <g key={`lvl-${i}`}>
          <line
            x1="0" x2={W}
            y1={scaleY(lvl.v)} y2={scaleY(lvl.v)}
            stroke={lvl.c}
            strokeWidth="0.8"
            strokeDasharray="3 3"
            opacity="0.8"
          />
          <rect x={W - 160} y={scaleY(lvl.v) - 9} width={160} height={18} fill={T.bg} />
          <text
            x={W - 8} y={scaleY(lvl.v) + 4}
            fontFamily={T.font.mono}
            fontSize="10"
            fill={lvl.c}
            textAnchor="end"
            letterSpacing="1"
          >
            {lvl.label}
          </text>
        </g>
      ))}

      {/* Candles */}
      {candles.map((c, i) => {
        const up = c.c >= c.o
        const x = i * xWidth + xWidth / 2
        const color = up ? T.long : T.short
        return (
          <g key={i} opacity="0.95">
            <line
              x1={x} x2={x}
              y1={scaleY(c.h)} y2={scaleY(c.l)}
              stroke={color}
              strokeWidth="1"
            />
            <rect
              x={x - xWidth * 0.35}
              y={scaleY(Math.max(c.o, c.c))}
              width={xWidth * 0.7}
              height={Math.max(1, Math.abs(scaleY(c.o) - scaleY(c.c)))}
              fill={color}
            />
          </g>
        )
      })}

      {/* Last price marker */}
      {lastPriceMark && (
        <g>
          <circle
            cx={candles.length * xWidth - xWidth / 2}
            cy={scaleY(candles[candles.length - 1].c)}
            r="3"
            fill={T.cobalt}
          />
          <circle
            cx={candles.length * xWidth - xWidth / 2}
            cy={scaleY(candles[candles.length - 1].c)}
            r="7"
            fill="none"
            stroke={T.cobalt}
            strokeWidth="0.8"
            opacity="0.5"
          />
        </g>
      )}
    </svg>
  )
}

function fmtPrice(v) {
  if (v >= 100) return v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (v >= 1)   return v.toFixed(4)
  return v.toFixed(6)
}
