"""
funding_extreme_engine.py — Análise Multi-Dimensional de Funding Rate Extremo

Não é um indicador — é um MOTOR DE ANÁLISE que avalia funding em 6 dimensões:
  1. NÍVEL:    quão extremo é o funding atual
  2. OI FUEL:  quanto combustível (posições abertas) existe pra liquidar
  3. PRICE CONTEXT: preço confirmando ou divergindo do funding
  4. VOLUME:   atividade institucional durante funding extremo
  5. ACELERAÇÃO (via oi_change): OI crescendo = mais traders na armadilha
  6. COMPOSITE: score final que combina todas as dimensões

Composite ponderado: 40% nível + 25% combustível + 25% contexto + 10% volume

Uso:
    from trinity.traders.funding_extreme_engine import analyze_funding_extreme
    result = analyze_funding_extreme(coin_data, direction="PUMP")
    # result["composite_mult"] → multiplicador para opportunity_score (1.0 – 2.50)
    # result["tier"]           → CRITICAL / HIGH / ELEVATED / NORMAL
    # result["signals"]        → lista de sinais descritivos com contexto

Direção:
    PUMP  → funding NEGATIVO é o sinal (shorts pagando longs)
    CRASH → funding POSITIVO é o sinal (longs pagando shorts)

Fail-open: qualquer erro retorna composite_mult=1.0 (sem efeito no score).
"""
import logging
from typing import List

log = logging.getLogger(__name__)


def analyze_funding_extreme(coin_data: dict, direction: str = "PUMP") -> dict:
    """
    Análise profunda de funding rate extremo em 4 dimensões.

    Args:
        coin_data: dict completo do coin (do scanner)
        direction: "PUMP" (funding negativo = squeeze) ou "CRASH" (funding positivo = liquidação)

    Returns:
        {
            "tier":                       str,   # CRITICAL / HIGH / ELEVATED / NORMAL
            "composite_mult":             float, # 1.0 a 2.50
            "composite_score":            float, # 0-100
            "funding_rate":               float, # rate por 8h
            "funding_ann":                float, # anualizado %
            "level_score":                float, # 0-100
            "fuel_score":                 float, # 0-100
            "context_score":              float, # 0-100
            "volume_score":               float, # 0-100
            "signals":                    list,  # sinais descritivos
            "daily_cost_pct":             float, # custo % para o lado perdedor
            "estimated_hours_to_squeeze": float, # estimativa de tempo
            "level_label":                str,   # MASSACRE / EXTREMO / ALTO / ELEVADO / NORMAL
        }
    """
    try:
        funding_rate = float(coin_data.get("funding_rate", 0))

        # ── Sanity check: exchange pode enviar em % em vez de decimal ─────
        # Funding de 0.01%/8h vem como 0.0001 (decimal). Se veio > 0.5,
        # provavelmente está em % (ex: 0.05 virou 5.0) — dividir por 100.
        if abs(funding_rate) > 0.5:
            funding_rate = funding_rate / 100.0
            log.debug(f"[FundingEngine] funding_rate convertido de % para decimal: {funding_rate}")
        # Funding > 10%/8h é fisicamente impossível nos mercados reais — dado corrompido
        if abs(funding_rate) > 0.10:
            log.warning(
                f"[FundingEngine] funding_rate anômalo ({funding_rate:.6f}) "
                f"para {coin_data.get('symbol', '?')} — ignorando"
            )
            return _normal_result(funding_rate, 0.0)

        pct_change   = float(coin_data.get("price_change_pct", 0))
        oi_usd       = float(coin_data.get("open_interest", 0))
        volume_24h   = float(coin_data.get("volume_24h", 0))
        oi_change    = float(coin_data.get("oi_change_pct", 0))

        funding_ann = funding_rate * 3 * 365 * 100   # % ao ano
        daily_cost  = abs(funding_rate) * 3 * 100    # custo % por dia

        # Verificar se o funding é relevante para a direção pedida
        if direction == "PUMP":
            relevant = -funding_rate   # positivo quando funding < 0 (shorts pagando)
        else:
            relevant = funding_rate    # positivo quando funding > 0 (longs pagando)

        if relevant <= 0:
            return _normal_result(funding_rate, funding_ann)

        signals: List[str] = []

        # ═══════════════════════════════════════════════════════════════════
        # DIMENSÃO 1: NÍVEL DO FUNDING (0-100) — quão extremo é o rate
        # ═══════════════════════════════════════════════════════════════════
        abs_rate = abs(funding_rate)

        if abs_rate >= 0.02:
            level_score = 100
            level_label = "MASSACRE"
            signals.append(
                f"🔥🔥🔥 Funding {funding_rate*100:+.3f}%/8h ({funding_ann:+,.0f}%/yr) — "
                f"lado perdedor paga {daily_cost:.1f}% POR DIA"
            )
        elif abs_rate >= 0.01:
            level_score = 85
            level_label = "EXTREMO"
            signals.append(
                f"🔥🔥 Funding {funding_rate*100:+.3f}%/8h ({funding_ann:+,.0f}%/yr) — "
                f"custo {daily_cost:.1f}%/dia — insustentável"
            )
        elif abs_rate >= 0.005:
            level_score = 65
            level_label = "ALTO"
            signals.append(
                f"🔥 Funding {funding_rate*100:+.3f}%/8h ({funding_ann:+,.0f}%/yr) — "
                f"pressão de {daily_cost:.2f}%/dia"
            )
        elif abs_rate >= 0.003:
            level_score = 45
            level_label = "ELEVADO"
            signals.append(
                f"⚡ Funding {funding_rate*100:+.3f}%/8h ({funding_ann:+,.0f}%/yr)"
            )
        elif abs_rate >= 0.001:
            level_score = 25
            level_label = "MODERADO"
        else:
            level_score = 0
            level_label = "NORMAL"

        # ═══════════════════════════════════════════════════════════════════
        # DIMENSÃO 2: COMBUSTÍVEL — OI (0-100)
        # Funding extremo SEM OI = ruído. Funding extremo COM OI = bomba.
        # ═══════════════════════════════════════════════════════════════════
        if oi_usd >= 50_000_000:
            fuel_score = 100
            signals.append(f"💰 OI massivo ${oi_usd/1e6:.0f}M — liquidações serão violentas")
        elif oi_usd >= 20_000_000:
            fuel_score = 80
            signals.append(f"💰 OI alto ${oi_usd/1e6:.0f}M — combustível abundante")
        elif oi_usd >= 5_000_000:
            fuel_score = 55
            signals.append(f"💰 OI ${oi_usd/1e6:.1f}M — combustível moderado")
        elif oi_usd >= 1_000_000:
            fuel_score = 30
        elif oi_usd >= 300_000:
            fuel_score = 15
        else:
            fuel_score = 5  # OI muito baixo = sinal pode ser ruído

        # Bônus: OI crescendo durante funding extremo = mais traders entrando na armadilha
        if oi_change > 5.0 and level_score >= 45:
            fuel_score = min(100, fuel_score + 20)
            signals.append(f"📈 OI crescendo +{oi_change:.1f}% — mais traders na armadilha")

        # ═══════════════════════════════════════════════════════════════════
        # DIMENSÃO 3: CONTEXTO DE PREÇO (0-100)
        # Regra central: se o preço cai MAIS RÁPIDO que o custo do funding,
        # o lado que está pagando está GANHANDO — não há squeeze iminente.
        # flat + funding extremo = BOMBA ARMADA (melhor cenário)
        # ═══════════════════════════════════════════════════════════════════
        context = ""  # rastreado para safety cap

        if direction == "PUMP":
            # Se a queda supera 1.5× o custo diário → shorts estão ganhando → sem squeeze
            drop_exceeds_cost = (pct_change < 0 and abs(pct_change) > daily_cost * 1.5)

            if drop_exceeds_cost:
                context_score = 0
                context       = "FALLING"
                signals.append(
                    f"⛔ Queda {pct_change:.1f}% excede custo funding {daily_cost:.1f}%/dia "
                    f"— shorts ganhando, sem squeeze"
                )
            elif abs(pct_change) < 3.0:
                context_score = 100
                context       = "BOMBA_ARMADA"
                signals.append(f"💣 Preço FLAT ({pct_change:+.1f}%) + funding extremo = BOMBA ARMADA")
            elif pct_change > 3.0:
                context_score = 80
                context       = "SQUEEZE_ATIVO"
                signals.append(f"🚀 Squeeze ativo — preço subindo {pct_change:+.1f}%")
            elif pct_change > 0:
                context_score = 60
                context       = "ACUMULANDO"
            elif abs(pct_change) <= daily_cost:
                # Queda menor que custo funding → funding pressiona mais → squeeze provável
                context_score = 50
                context       = "ACUMULANDO"
                signals.append(
                    f"📊 Queda leve {pct_change:.1f}% < custo {daily_cost:.1f}%/dia — funding pressiona"
                )
            else:
                # Zona cinza: queda entre daily_cost e 1.5× daily_cost
                context_score = 20
                context       = "LATE"
                signals.append(
                    f"⏳ Queda {pct_change:.1f}% vs custo {daily_cost:.1f}%/dia — monitorar"
                )

        else:  # CRASH
            # Se a alta supera 1.5× o custo diário → longs estão ganhando → sem liquidação
            rise_exceeds_cost = (pct_change > 0 and pct_change > daily_cost * 1.5)

            if rise_exceeds_cost:
                context_score = 0
                context       = "RISING"
                signals.append(
                    f"⛔ Alta {pct_change:+.1f}% excede custo funding {daily_cost:.1f}%/dia "
                    f"— longs ganhando"
                )
            elif abs(pct_change) < 3.0:
                context_score = 100
                context       = "BOMBA_ARMADA"
                signals.append(f"💣 Preço FLAT ({pct_change:+.1f}%) com longs pagando — ARMADILHA")
            elif pct_change < -3.0:
                context_score = 80
                context       = "LIQUIDACAO_ATIVA"
                signals.append(f"📉 Liquidação ativa — preço caindo {pct_change:.1f}%")
            elif pct_change < 0:
                context_score = 60
                context       = "ACUMULANDO"
            elif pct_change <= daily_cost:
                context_score = 50
                context       = "ACUMULANDO"
            else:
                context_score = 20
                context       = "LATE"

        # ═══════════════════════════════════════════════════════════════════
        # DIMENSÃO 4: VOLUME (0-100)
        # Volume alto durante funding extremo = institucionais se posicionando
        # ═══════════════════════════════════════════════════════════════════
        if volume_24h >= 50_000_000:
            volume_score = 100
            signals.append(f"📊 Volume ${volume_24h/1e6:.0f}M — atividade institucional intensa")
        elif volume_24h >= 20_000_000:
            volume_score = 75
        elif volume_24h >= 5_000_000:
            volume_score = 50
        elif volume_24h >= 1_000_000:
            volume_score = 30
        else:
            volume_score = 10

        # ═══════════════════════════════════════════════════════════════════
        # COMPOSITE SCORE PONDERADO: 40% nível + 25% fuel + 25% ctx + 10% vol
        # ═══════════════════════════════════════════════════════════════════
        composite = (
            level_score   * 0.40 +
            fuel_score    * 0.25 +
            context_score * 0.25 +
            volume_score  * 0.10
        )

        # ── Multiplicador baseado no composite ─────────────────────────────
        if composite >= 85:
            tier           = "CRITICAL"
            composite_mult = 2.50
        elif composite >= 70:
            tier           = "CRITICAL"
            composite_mult = 2.00
        elif composite >= 55:
            tier           = "HIGH"
            composite_mult = 1.60
        elif composite >= 40:
            tier           = "ELEVATED"
            composite_mult = 1.35
        elif composite >= 25:
            tier           = "ELEVATED"
            composite_mult = 1.15
        else:
            tier           = "NORMAL"
            composite_mult = 1.0

        # Safety cap: queda/alta excedeu custo de funding → lado oposto está ganhando
        # Não há squeeze/liquidação iminente — não amplificar o score
        if context in ("FALLING", "RISING"):
            composite_mult = 1.0
            tier           = "NORMAL"

        # ── Estimativa de tempo até squeeze/liquidação ─────────────────────
        if daily_cost >= 5.0:
            est_hours = 12
            signals.append(
                f"⏰ Estimativa: squeeze em ~12h (custo {daily_cost:.1f}%/dia insuportável)"
            )
        elif daily_cost >= 3.0:
            est_hours = 24
            signals.append(
                f"⏰ Estimativa: squeeze em ~24h (custo {daily_cost:.1f}%/dia)"
            )
        elif daily_cost >= 1.0:
            est_hours = 72
        elif daily_cost >= 0.3:
            est_hours = 168  # ~1 semana
        else:
            est_hours = 999

        if tier != "NORMAL":
            log.info(
                f"[FundingEngine] {tier} — {coin_data.get('symbol', '?')} "
                f"fr={funding_rate*100:+.4f}%/8h ({funding_ann:+,.0f}%/yr) "
                f"composite={composite:.0f} mult={composite_mult:.2f} "
                f"[level={level_score:.0f} fuel={fuel_score:.0f} "
                f"ctx={context_score:.0f} vol={volume_score:.0f}] "
                f"→ {direction}"
            )

        return {
            "tier":                       tier,
            "composite_mult":             composite_mult,
            "composite_score":            round(composite, 1),
            "funding_rate":               funding_rate,
            "funding_ann":                round(funding_ann, 1),
            "level_score":                round(level_score, 1),
            "fuel_score":                 round(fuel_score, 1),
            "context_score":              round(context_score, 1),
            "volume_score":               round(volume_score, 1),
            "signals":                    signals,
            "daily_cost_pct":             round(daily_cost, 2),
            "estimated_hours_to_squeeze": est_hours,
            "level_label":                level_label,
        }

    except Exception as e:
        log.debug(f"[FundingEngine] Erro {coin_data.get('symbol', '?')}: {e}")
        return _normal_result(
            float(coin_data.get("funding_rate", 0)),
            float(coin_data.get("funding_rate", 0)) * 3 * 365 * 100,
        )


def _normal_result(funding_rate: float, funding_ann: float) -> dict:
    return {
        "tier":                       "NORMAL",
        "composite_mult":             1.0,
        "composite_score":            0,
        "funding_rate":               funding_rate,
        "funding_ann":                round(funding_ann, 1),
        "level_score":                0,
        "fuel_score":                 0,
        "context_score":              0,
        "volume_score":               0,
        "signals":                    [],
        "daily_cost_pct":             0,
        "estimated_hours_to_squeeze": 999,
        "level_label":                "NORMAL",
    }
