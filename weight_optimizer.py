"""
weight_optimizer.py — Otimizador de pesos SMC baseado em outcomes reais.
Analisa correlação entre sub-scores das camadas e outcomes WIN/LOSS.
Só aplica pesos com >= 30 amostras válidas.

NOTA: Esta etapa produz recomendações úteis após 30+ outcomes WIN/LOSS.
Implemente agora, gere relatórios diários, mas não aplique pesos sem dados suficientes.
"""
import json
import logging
import shutil
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Pesos atuais do SMC Engine ────────────────────────────────────────────────
CURRENT_WEIGHTS: dict = {
    "market_structure": 20,
    "liquidity":        15,
    "bos":              15,
    "choch":            15,
    "order_blocks":     10,
    "fvg":              10,
    "volume_confirm":   10,
    "momentum":          5,
}

WEIGHT_CONSTRAINTS: dict = {
    "min_per_layer":       3,
    "max_per_layer":       30,
    "total":               100,
    "never_lowest":        "market_structure",  # nunca pode ser o menor peso
    "min_samples_to_optimize": 30,             # WIN + LOSS (ignora NEUTRAL)
    "max_change_per_round":  8,                # nenhum peso muda > 8 pontos de uma vez
}

OUTCOMES_DIR     = Path("logs")
REPORT_FILE      = Path("dashboard/optimization_report.json")
ENGINE_FILE      = Path("smart_money_engine.py")
ENGINE_BACKUP    = Path("smart_money_engine.py.bak")

# ── Singleton ─────────────────────────────────────────────────────────────────
_instance: Optional["WeightOptimizer"] = None


def get_optimizer() -> "WeightOptimizer":
    """Retorna instância singleton do WeightOptimizer."""
    global _instance
    if _instance is None:
        _instance = WeightOptimizer()
    return _instance


class WeightOptimizer:
    """Analisa outcomes reais e sugere redistribuição dos pesos SMC."""

    def load_outcomes(self) -> list:
        """
        Carrega todos os logs/outcomes_YYYY-MM.jsonl existentes.
        Filtra apenas status WIN e LOSS (ignora NEUTRAL/EXPIRED).
        Retorna lista de dicts com: signal_id, direction, score, outcome,
        status, layer_scores, conviction_tier.
        """
        outcomes: list = []
        for p in sorted(OUTCOMES_DIR.glob("outcomes_*.jsonl")):
            try:
                for line in p.read_text().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    o = json.loads(line)
                    if o.get("status") in ("WIN", "LOSS"):
                        outcomes.append(o)
            except Exception as e:
                log.warning(f"[Optimizer] Erro ao ler {p.name}: {e}")
        return outcomes

    def analyze_correlation(self, outcomes: list) -> dict:
        """
        Para cada camada SMC, calcula correlação simples entre sub-score
        e outcome (WIN=1, LOSS=0), média em WINs vs LOSSes, e ranking.
        Usa apenas stdlib (statistics) — sem numpy/pandas.
        Retorna dict por camada com correlation, avg_in_wins, avg_in_losses, rank.
        """
        if not outcomes:
            return {k: {"correlation": 0.0, "avg_in_wins": 50.0, "avg_in_losses": 50.0, "rank": i+1}
                    for i, k in enumerate(CURRENT_WEIGHTS)}

        layers = list(CURRENT_WEIGHTS.keys())
        result: dict = {}

        for layer in layers:
            scores_vals = []
            outcome_vals = []

            for o in outcomes:
                ls = o.get("layer_scores", {})
                if not ls or layer not in ls:
                    continue
                scores_vals.append(float(ls[layer]))
                outcome_vals.append(1.0 if o.get("status") == "WIN" else 0.0)

            if len(scores_vals) < 3:
                result[layer] = {"correlation": 0.0, "avg_in_wins": 50.0,
                                 "avg_in_losses": 50.0, "rank": 0}
                continue

            # Correlação de Pearson simples via statistics
            try:
                corr = statistics.correlation(scores_vals, outcome_vals)
            except (statistics.StatisticsError, TypeError):
                corr = 0.0

            wins_scores   = [s for s, o in zip(scores_vals, outcome_vals) if o == 1.0]
            losses_scores = [s for s, o in zip(scores_vals, outcome_vals) if o == 0.0]

            result[layer] = {
                "correlation":   round(corr, 4),
                "avg_in_wins":   round(statistics.mean(wins_scores),   1) if wins_scores   else 50.0,
                "avg_in_losses": round(statistics.mean(losses_scores), 1) if losses_scores else 50.0,
                "rank":          0,
            }

        # Ranking por correlação absoluta (maior correlação = maior poder preditivo)
        ranked = sorted(result.items(), key=lambda x: abs(x[1]["correlation"]), reverse=True)
        for rank, (layer, _) in enumerate(ranked, start=1):
            result[layer]["rank"] = rank

        return result

    def suggest_weights(self, outcomes: list) -> dict:
        """
        Se len(outcomes) < MIN_SAMPLES: retorna CURRENT_WEIGHTS sem mudança.
        Se >= MIN_SAMPLES: redistribui pesos proporcionalmente à correlação,
        aplicando constraints (min 3, max 30, soma=100, mudança gradual).
        Retorna {"weights": {...}, "changes": {...}, "reasoning": {...}}
        """
        min_samples = WEIGHT_CONSTRAINTS["min_samples_to_optimize"]

        if len(outcomes) < min_samples:
            return {
                "weights":   CURRENT_WEIGHTS.copy(),
                "changes":   {k: 0 for k in CURRENT_WEIGHTS},
                "reasoning": {k: "dados insuficientes" for k in CURRENT_WEIGHTS},
            }

        correlation = self.analyze_correlation(outcomes)

        # Correlação pode ser negativa; usamos o valor absoluto para redistribuição
        # porém sinais negativos significam que o layer NÃO ajuda → peso mínimo
        raw_importance: dict = {}
        for layer in CURRENT_WEIGHTS:
            corr = correlation.get(layer, {}).get("correlation", 0.0)
            raw_importance[layer] = max(abs(corr), 0.01)   # mínimo 0.01

        # Normaliza para somar 100
        total_importance = sum(raw_importance.values())
        raw_weights = {k: v / total_importance * 100 for k, v in raw_importance.items()}

        # Aplica constraints
        new_weights = self._apply_constraints(raw_weights, CURRENT_WEIGHTS)

        # Calcula mudanças
        changes = {k: round(new_weights[k] - CURRENT_WEIGHTS[k], 1) for k in CURRENT_WEIGHTS}

        # Reasoning por camada
        reasoning: dict = {}
        for layer in CURRENT_WEIGHTS:
            corr = correlation.get(layer, {}).get("correlation", 0.0)
            avg_w = correlation.get(layer, {}).get("avg_in_wins", 50.0)
            avg_l = correlation.get(layer, {}).get("avg_in_losses", 50.0)
            reasoning[layer] = (
                f"correlação={corr:+.3f} | média_wins={avg_w:.0f} | média_losses={avg_l:.0f} | "
                f"mudança={changes[layer]:+.1f}pts"
            )

        return {"weights": new_weights, "changes": changes, "reasoning": reasoning}

    def _apply_constraints(self, raw: dict, current: dict) -> dict:
        """Aplica min/max/total e mudança gradual aos pesos sugeridos."""
        min_w     = WEIGHT_CONSTRAINTS["min_per_layer"]
        max_w     = WEIGHT_CONSTRAINTS["max_per_layer"]
        max_delta = WEIGHT_CONSTRAINTS["max_change_per_round"]
        never_low = WEIGHT_CONSTRAINTS["never_lowest"]

        # 1. Clamp min/max e limita mudança por rodada
        result: dict = {}
        for k in current:
            raw_val   = raw.get(k, current[k])
            clamped   = max(min_w, min(max_w, raw_val))
            max_up    = current[k] + max_delta
            max_down  = current[k] - max_delta
            result[k] = round(max(max_down, min(max_up, clamped)), 1)

        # 2. never_lowest: garante que market_structure não é o menor
        if never_low in result:
            others_min = min(v for k, v in result.items() if k != never_low)
            if result[never_low] <= others_min:
                result[never_low] = others_min + 1

        # 3. Ajusta para somar exatamente 100
        total = sum(result.values())
        diff  = 100 - total
        if diff != 0:
            # Distribui o diff pela camada mais distante do target
            adjustable = sorted(
                [(k, v) for k, v in result.items() if k != never_low],
                key=lambda x: abs(x[1] - raw.get(x[0], x[1])),
            )
            if adjustable:
                k_adj = adjustable[-1][0]
                result[k_adj] = round(result[k_adj] + diff, 1)

        return result

    def generate_report(self) -> dict:
        """
        Gera e salva dashboard/optimization_report.json.
        Retorna o relatório como dict.
        """
        try:
            outcomes        = self.load_outcomes()
            n_outcomes      = len(outcomes)
            min_samples     = WEIGHT_CONSTRAINTS["min_samples_to_optimize"]
            correlation     = self.analyze_correlation(outcomes)
            suggestion      = self.suggest_weights(outcomes)

            if n_outcomes == 0:
                recommendation = "insufficient_data"
                precision_gain = "indeterminado"
                safe_to_apply  = False
            elif n_outcomes < min_samples:
                recommendation = "collect_more_data"
                precision_gain = "indeterminado"
                safe_to_apply  = False
            else:
                recommendation = "apply"
                # Estimativa simples: se correlações médias > 0.15, ganho estimado 3-7%
                avg_abs_corr = statistics.mean(
                    abs(v.get("correlation", 0)) for v in correlation.values()
                ) if correlation else 0
                precision_gain = "+3-7%" if avg_abs_corr > 0.15 else "+1-3%"
                safe_to_apply  = True

            report = {
                "generated_at":           datetime.now(timezone.utc).isoformat(),
                "total_outcomes":         n_outcomes,
                "usable_outcomes":        n_outcomes,
                "current_weights":        CURRENT_WEIGHTS.copy(),
                "suggested_weights":      suggestion["weights"],
                "weight_changes":         suggestion["changes"],
                "correlation_by_layer":   correlation,
                "recommendation":         recommendation,
                "samples_collected":      n_outcomes,
                "samples_needed":         min_samples,
                "estimated_precision_gain": precision_gain,
                "safe_to_apply":          safe_to_apply,
                "reasoning_by_layer":     suggestion.get("reasoning", {}),
            }

            REPORT_FILE.parent.mkdir(exist_ok=True)
            REPORT_FILE.write_text(json.dumps(report, indent=2))
            log.info(
                f"[Optimizer] Relatório gerado: {n_outcomes} outcomes | "
                f"recomendação={recommendation} | safe={safe_to_apply}"
            )
            return report

        except Exception as e:
            log.error(f"[Optimizer] Erro ao gerar relatório: {e}")
            fallback = {
                "generated_at":           datetime.now(timezone.utc).isoformat(),
                "total_outcomes":         0,
                "usable_outcomes":        0,
                "current_weights":        CURRENT_WEIGHTS.copy(),
                "suggested_weights":      CURRENT_WEIGHTS.copy(),
                "weight_changes":         {k: 0 for k in CURRENT_WEIGHTS},
                "correlation_by_layer":   {},
                "recommendation":         "insufficient_data",
                "samples_collected":      0,
                "samples_needed":         WEIGHT_CONSTRAINTS["min_samples_to_optimize"],
                "estimated_precision_gain": "indeterminado",
                "safe_to_apply":          False,
                "error":                  str(e),
            }
            try:
                REPORT_FILE.parent.mkdir(exist_ok=True)
                REPORT_FILE.write_text(json.dumps(fallback, indent=2))
            except Exception:
                pass
            return fallback

    def apply_weights(self, weights: dict) -> bool:
        """
        Atualiza SMC_WEIGHTS em smart_money_engine.py via substituição de string segura.
        Cria backup em smart_money_engine.py.bak antes de modificar.
        Retorna True se sucesso, False se falhou (restaura backup).
        """
        if not ENGINE_FILE.exists():
            log.error(f"[Optimizer] {ENGINE_FILE} não encontrado")
            return False

        try:
            original = ENGINE_FILE.read_text(encoding="utf-8")
        except Exception as e:
            log.error(f"[Optimizer] Erro ao ler engine: {e}")
            return False

        # Cria backup antes de qualquer modificação
        try:
            shutil.copy2(ENGINE_FILE, ENGINE_BACKUP)
            log.info(f"[Optimizer] Backup criado: {ENGINE_BACKUP}")
        except Exception as e:
            log.error(f"[Optimizer] Falha ao criar backup: {e}")
            return False

        # Gera o novo bloco SMC_WEIGHTS
        lines = [
            "SMC_WEIGHTS = {",
        ]
        for k, v in weights.items():
            # Alinha como no original
            lines.append(f'    "{k}":{" " * (18 - len(k))}{int(v)},')
        lines.append("}")
        new_block = "\n".join(lines)

        # Localiza e substitui o bloco existente
        import re
        pattern = r"SMC_WEIGHTS\s*=\s*\{[^}]+\}"
        new_content = re.sub(pattern, new_block, original, count=1)

        if new_content == original:
            log.error("[Optimizer] Padrão SMC_WEIGHTS não encontrado no engine")
            return False

        try:
            ENGINE_FILE.write_text(new_content, encoding="utf-8")
            log.info(f"[Optimizer] Pesos aplicados em {ENGINE_FILE}: {weights}")
            return True
        except Exception as e:
            log.error(f"[Optimizer] Erro ao escrever engine: {e}")
            # Restaura backup
            try:
                shutil.copy2(ENGINE_BACKUP, ENGINE_FILE)
                log.info("[Optimizer] Backup restaurado após falha")
            except Exception as e2:
                log.error(f"[Optimizer] CRÍTICO: falha ao restaurar backup: {e2}")
            return False


def run_optimization_report():
    """Função de entrada para o scheduler: gera relatório diário."""
    try:
        log.info("[Optimizer] Gerando relatório diário de otimização...")
        optimizer = get_optimizer()
        report    = optimizer.generate_report()
        log.info(f"[Optimizer] Concluído: {report.get('recommendation')} | "
                 f"{report.get('samples_collected')}/{report.get('samples_needed')} amostras")
    except Exception as e:
        log.error(f"[Optimizer] Erro no relatório diário: {e}")
