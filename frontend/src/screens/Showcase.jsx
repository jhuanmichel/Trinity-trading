/**
 * Showcase.jsx — Trinity V2 Design System
 *
 * Pagina isolada que renderiza todos os primitives V2 em todas as
 * variantes. Acessivel via `/app/?showcase=1`. Serve para validar
 * visualmente tokens e componentes antes de migrar telas reais em
 * Sessoes 2+.
 */
import { T } from '@/styles/tokens'
import {
  Label,
  Num,
  Pill,
  Card,
  Skel,
  Delta,
  Dot,
  Sparkline,
} from '@/components/primitives'

const SPARK_UP   = [45, 47, 46, 49, 52, 51, 54, 53, 56]
const SPARK_DOWN = [56, 53, 54, 51, 52, 49, 46, 47, 45]

export function Showcase() {
  return (
    <div
      style={{
        padding: 40,
        maxWidth: 1200,
        margin: '0 auto',
        background: T.bg,
        minHeight: '100vh',
        color: T.text,
        fontFamily: T.font.sans,
      }}
    >
      <h1
        style={{
          fontFamily: T.font.display,
          fontWeight: T.fw.medium,
          fontSize: T.fs['3xl'],
          letterSpacing: T.ls.snug,
          margin: 0,
          color: T.text,
        }}
      >
        Trinity V2 <span style={{ color: T.textMut }}>·</span>{' '}
        <span style={{ color: T.long }}>Design System</span>
      </h1>
      <p style={{ color: T.textMut, marginTop: 8, fontSize: T.fs.base }}>
        Showcase de tokens e primitives. Use para validar o visual antes de portar telas.
      </p>

      {/* ─── COLORS ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>COLORS</Label>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(6, 1fr)',
            gap: 12,
            marginTop: 14,
          }}
        >
          {['long', 'short', 'amber', 'cobalt', 'violet', 'text'].map(k => (
            <div key={k}>
              <div
                style={{
                  height: 64,
                  background: T[k],
                  borderRadius: T.radius.md,
                  marginBottom: 8,
                  border: `1px solid ${T.border}`,
                }}
              />
              <Label>{k.toUpperCase()}</Label>
              <Num size={10} color={T.textDim} style={{ marginTop: 2, display: 'block' }}>
                {T[k]}
              </Num>
            </div>
          ))}
        </div>
      </section>

      {/* ─── SURFACES ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>SURFACES (layered dark)</Label>
        <div style={{ display: 'flex', gap: 12, marginTop: 14 }}>
          {['bg', 'surface', 'surface2', 'surface3'].map(k => (
            <div
              key={k}
              style={{
                flex: 1,
                height: 80,
                background: T[k],
                border: `1px solid ${T.border}`,
                borderRadius: T.radius.md,
                padding: 12,
              }}
            >
              <Label>{k}</Label>
              <Num size={10} color={T.textDim}>{T[k]}</Num>
            </div>
          ))}
        </div>
      </section>

      {/* ─── TYPOGRAPHY ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>TYPOGRAPHY</Label>
        <div
          style={{
            marginTop: 14,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <div style={{ fontFamily: T.font.display, fontSize: T.fs['5xl'], fontWeight: 500, letterSpacing: T.ls.tight, lineHeight: 1.05 }}>
            Space Grotesk <span style={{ color: T.long }}>76px</span>
          </div>
          <div style={{ fontFamily: T.font.sans, fontSize: T.fs.lg, fontWeight: 400 }}>
            Inter — paragraph 17px. Prosa institucional, legivel.
          </div>
          <Num size={48} weight={300}>78,843.10</Num>
          <div>
            <Num size={28} weight={400} color={T.long}>+2.34%</Num>{' '}
            <Num size={14} color={T.textMut}>LAST 24H</Num>
          </div>
        </div>
      </section>

      {/* ─── PILLS ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>PILLS</Label>
        <div style={{ display: 'flex', gap: 8, marginTop: 14, flexWrap: 'wrap' }}>
          <Pill tone="long">LONG</Pill>
          <Pill tone="short">SHORT</Pill>
          <Pill tone="amber">AGUARDANDO</Pill>
          <Pill tone="cobalt">LIVE</Pill>
          <Pill tone="violet">MACRO</Pill>
          <Pill tone="neutral">NEUTRAL</Pill>
        </div>
      </section>

      {/* ─── DELTAS & DOTS ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>DELTAS & DOTS</Label>
        <div style={{ display: 'flex', gap: 32, alignItems: 'center', marginTop: 14 }}>
          <Delta v={2.34} />
          <Delta v={-1.07} />
          <Delta v={12.456} size={16} />
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Dot variant="live" />
            <span style={{ color: T.textMut, fontFamily: T.font.mono, fontSize: 11, letterSpacing: T.ls.label, textTransform: 'uppercase' }}>
              LIVE
            </span>
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
            <Dot />
            <span style={{ color: T.textDim, fontFamily: T.font.mono, fontSize: 11 }}>idle</span>
          </span>
        </div>
      </section>

      {/* ─── SPARKLINES ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>SPARKLINES</Label>
        <div style={{ display: 'flex', gap: 32, alignItems: 'center', marginTop: 14 }}>
          <div>
            <Label>UP TREND</Label>
            <Sparkline data={SPARK_UP} color={T.long} />
          </div>
          <div>
            <Label>DOWN TREND</Label>
            <Sparkline data={SPARK_DOWN} color={T.short} />
          </div>
          <div>
            <Label>FILL OFF</Label>
            <Sparkline data={SPARK_UP} color={T.cobalt} fill={false} />
          </div>
        </div>
      </section>

      {/* ─── CARDS ─── */}
      <section style={{ marginTop: 40 }}>
        <Label>CARDS</Label>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 20,
            marginTop: 14,
          }}
        >
          <Card title="CARD COM HEADER" aside={<Pill tone="long">ATIVO</Pill>}>
            <Num size={32} weight={300}>52%</Num>
            <div style={{ marginTop: 8, color: T.textMut, fontSize: 12 }}>
              Card padrao com titulo + pill aside. Padding default 20px.
            </div>
          </Card>
          <Card>
            <div style={{ color: T.textMut, fontSize: 12 }}>
              Card sem header, so com body. Util para conteudo inline/secundario.
            </div>
          </Card>
        </div>
      </section>

      {/* ─── SKELETONS ─── */}
      <section style={{ marginTop: 40, marginBottom: 60 }}>
        <Label>SKELETONS (shimmer)</Label>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            marginTop: 14,
            maxWidth: 400,
          }}
        >
          <Skel w="60%" h={14} />
          <Skel w="90%" h={10} />
          <Skel w="40%" h={10} />
          <Skel w="100%" h={80} style={{ marginTop: 12 }} />
        </div>
      </section>
    </div>
  )
}
