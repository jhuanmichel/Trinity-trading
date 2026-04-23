/**
 * SignalsList.jsx — Timeline compacta de sinais (visual mockup).
 *
 * Props:
 *   items?: [{ t: '15:02:03', pair: 'BTC/USDT', side: 'LONG',
 *              entry, sl, tp, conv: 72, live: true }]
 *           - se ausente, demo hardcoded.
 */
import { T } from '@/styles/tokens'
import { Pill } from '@/components/primitives'

const DEMO_ITEMS = [
  { t: '15:02:03', pair: 'BTC/USDT', side: 'LONG',  entry: '78,843', sl: '78,447', tp: '79,238', conv: 72, live: true },
  { t: '14:48:19', pair: 'SOL/USDT', side: 'SHORT', entry: '172.88', sl: '175.02', tp: '168.41', conv: 68 },
  { t: '14:12:44', pair: 'ETH/USDT', side: 'LONG',  entry: '3,124',  sl: '3,102',  tp: '3,168',  conv: 61 },
  { t: '13:55:01', pair: 'BNB/USDT', side: 'LONG',  entry: '614.2',  sl: '608.0',  tp: '622.8',  conv: 58 },
]

export function SignalsList({ items }) {
  const data = Array.isArray(items) && items.length ? items : DEMO_ITEMS
  return (
    <div>
      {data.map((s, i) => (
        <div
          key={i}
          className="t-hoverline"
          style={{
            display: 'grid',
            gridTemplateColumns: '70px 80px 60px 1fr 60px',
            alignItems: 'center',
            gap: 12,
            padding: '12px 20px',
            borderBottom: i < data.length - 1 ? `1px solid ${T.border}` : 'none',
            cursor: 'pointer',
          }}
        >
          <span className="t-mono" style={{ fontSize: 10, color: T.textDim }}>{s.t}</span>
          <span className="t-mono" style={{ fontSize: 11, color: T.text }}>{s.pair}</span>
          <Pill tone={s.side === 'LONG' ? 'long' : 'short'} style={{ fontSize: 9 }}>{s.side}</Pill>
          <div style={{ display: 'flex', gap: 14, fontFamily: T.font.mono, fontSize: 10, color: T.textMut }}>
            <span>E <span style={{ color: T.text }}>{s.entry}</span></span>
            <span>SL <span style={{ color: T.short }}>{s.sl}</span></span>
            <span>TP <span style={{ color: T.long }}>{s.tp}</span></span>
          </div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'flex-end',
          }}>
            {s.live && <span className="t-dot-live" />}
            <span className="t-mono" style={{ fontSize: 11, color: T.text }}>{s.conv}%</span>
          </div>
        </div>
      ))}
    </div>
  )
}
