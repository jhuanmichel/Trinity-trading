# Phase 0 Inventory — Institutional Direction Engine + current-state

## Engine

- **Arquivo**: `main.py`
- **Função de entrada**: `run_institutional_analysis()` (linha 476)
- **Orquestra 17 etapas**:
  1. OHLCV + preço (linha 491-492)
  2. Market Structure (496)
  3. Trend (501)
  4. Volume (504)
  5. Derivatives + Liquidations (507-508)
  6. Correlation (511)
  7. Regime (515)
  8. Institutional Score (519)
  9. MTF (532)
  10. Smart Money Concepts (538-543) — com try/except interno
  11. Market Maker Engine (548-561) — com try/except
  12. Pressure Meter (566-582) — com try/except
  13. Rare Setup Detector (588-605) — com try/except
  14. Geopolitical (610-625) — com try/except
  15. Bitcoin Cycle (630-650) — com try/except
  16. Institutional Direction Engine (655-678) — com try/except
  17. Neural Intelligence (685-726) — com try/except
- Trinity Score final (748-760)
- Invincible Mode branch (791-818) → 1º `_write_dashboard_state()` (linha 795)
- Funding Rate Gate (827-852) → 2º `_write_dashboard_state()` (linha 841)
- HCF (854-884) → 3º `_write_dashboard_state()` (linha 870)
- Sinal válido → 4º `_write_dashboard_state()` (linha 893)

## Persistência

- **Função**: `_write_dashboard_state(...)` (linha 204-417 `main.py`)
- **Storage**: arquivo local `dashboard/current_state.json` (linha 415)
- **Método**: `state_path.write_text(json.dumps(state, cls=_Enc, ...))` (linha 417)
- **Encoder custom**: `_Enc` trata `np.bool_/integer/floating`
- **Path Render**: `/app/dashboard/current_state.json` (fs ephemeral mas persiste durante runtime)
- **Git tracking**: o arquivo está committado (removido de .gitignore em `76a9465` — ver `~/.claude/projects/.../memory/MEMORY.md`)

## Endpoint

- **Arquivo**: `dashboard/server.py`
- **Handler**: `get_current_state()` (linha 1077)
- **Lê de**: `STATE_FILE = BASE_DIR / "dashboard" / "current_state.json"` (linha 21)
- **Comportamento**: se arquivo existe, retorna `json.loads(STATE_FILE.read_text())`; senão `{"status":"no_data"}`

## Gap identificado (Ramo B)

**Causa**: `run_institutional_analysis()` tem um `try/except` gigante envolvendo todas as 17 etapas + score + modes.

- Try: linha 488
- **Todos os 4 callsites de `_write_dashboard_state()` estão dentro do try** (linhas 795, 841, 870, 893)
- Except: linhas 1001-1003

```python
except Exception as e:
    log.error(f"💥 ERRO (institucional): {e}", exc_info=True)
    alerts.send_error(f"[INSTITUCIONAL] {e}")
    # ← aqui NADA escreve em dashboard/current_state.json
```

**Consequência**: se qualquer etapa não-envolta em try interno (etapas 1-9, ou os calls fora do try interno como `calculate_institutional_score`, `_run_mtf_market_structure`, `_calc_levels`) lançar exception:
- Log registra o erro
- Telegram notifica
- `last_updated` NÃO é atualizado (state stale)

**Evidência empírica (22/abril/2026, 01:26 UTC)**:
- T1=T2=T3 retornam `last_updated: 2026-04-23T00:25:02.627647` (última análise bem-sucedida)
- Engine deveria ter rodado às 01:25 UTC (INST_INTERVAL_MINUTES=60)
- Não atualizou → pipeline quebrou em alguma etapa antes dos `_write_dashboard_state`
- Exception swallowed silenciosamente (user só viu via logs do Render 22/04 as análises iniciando mas não finalizando)

## Fix aplicado

Nova função `_write_dashboard_state_error(err_msg)` em `main.py` que:
- Lê `dashboard/current_state.json` existente (preserva últimos valores conhecidos)
- Atualiza `last_updated` com timestamp atual
- Injeta campo `last_error = {message, at}` para debug
- Persiste com atomic write via tmp+rename

No `except` de `run_institutional_analysis` (linha 1001), adicionar chamada a essa função. Garante que:
- `last_updated` reflete horário real do último ciclo (mesmo falhando)
- Dashboard vê que engine está "tentando" mesmo quando quebra
- Campo `last_error` permite debug sem precisar de Render logs

## Validação pós-fix

Sequência de 3 chamadas com gap 60s:
- Cada chamada retorna timestamp diferente do anterior
- Nenhum igual ao stale `2026-04-03T22:42:39` ou `2026-04-23T00:25:02`
- Se houver exception, campo `last_error` aparece
