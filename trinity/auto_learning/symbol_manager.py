"""
Module 3 - Symbol Manager

Gerencia automaticamente blacklist e whitelist de simbolos baseado em WR historico.

Logica:
    Whitelist (alerta com threshold mais baixo):
        - Simbolo com >= 15 trades resolvidos em 30d
        - WR >= 70%

    Blacklist (nao alerta):
        - Simbolo com >= 15 trades resolvidos em 30d
        - WR <= 25%

    Remocao automatica:
        - Whitelist: simbolo cai abaixo de 60% WR -> remover
        - Blacklist: simbolo passa de 35% WR -> remover (da nova chance)
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone

from trinity.auto_learning import safety, metrics, state

logger = logging.getLogger("auto_learning.symbol_manager")

MODULE_NAME = "SYMBOL_MANAGE"

WHITELIST_ADD_WR = 0.70
WHITELIST_REMOVE_WR = 0.60
BLACKLIST_ADD_WR = 0.25
BLACKLIST_REMOVE_WR = 0.35
MIN_TRADES = 15


def _decide_changes(
    wr_by_symbol: dict,
    current_blacklist: set,
    current_whitelist: set,
) -> tuple[set, set, list[dict]]:
    """
    Decide novas blacklist/whitelist.

    Retorna (new_blacklist, new_whitelist, changes_log).
    """
    new_blacklist = set(current_blacklist)
    new_whitelist = set(current_whitelist)
    changes_log = []

    for symbol, stats in wr_by_symbol.items():
        wr = stats["wr"]
        trades = stats["trades"]
        sym = symbol.upper()

        if trades < MIN_TRADES:
            continue

        # Whitelist
        if sym not in current_whitelist and wr >= WHITELIST_ADD_WR:
            new_whitelist.add(sym)
            if sym in new_blacklist:
                new_blacklist.discard(sym)
            changes_log.append({
                "symbol": sym,
                "action": "whitelist_add",
                "wr": wr,
                "trades": trades,
            })
        elif sym in current_whitelist and wr < WHITELIST_REMOVE_WR:
            new_whitelist.discard(sym)
            changes_log.append({
                "symbol": sym,
                "action": "whitelist_remove",
                "wr": wr,
                "trades": trades,
            })

        # Blacklist (so se nao estiver na whitelist)
        if sym not in new_whitelist:
            if sym not in current_blacklist and wr <= BLACKLIST_ADD_WR:
                new_blacklist.add(sym)
                changes_log.append({
                    "symbol": sym,
                    "action": "blacklist_add",
                    "wr": wr,
                    "trades": trades,
                })
            elif sym in current_blacklist and wr > BLACKLIST_REMOVE_WR:
                new_blacklist.discard(sym)
                changes_log.append({
                    "symbol": sym,
                    "action": "blacklist_remove",
                    "wr": wr,
                    "trades": trades,
                })

    return (new_blacklist, new_whitelist, changes_log)


def run() -> dict:
    """Executa symbol manager."""
    result = {
        "module": MODULE_NAME,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "enabled": False,
        "changes": [],
        "errors": [],
    }

    if not safety.is_module_enabled(MODULE_NAME):
        result["status"] = "disabled"
        return result
    result["enabled"] = True

    is_emergency, emergency_reason = safety.is_emergency_state(metrics)
    if is_emergency:
        safety.log_kill(MODULE_NAME, f"emergency_state: {emergency_reason}", {})
        safety.update_health(MODULE_NAME, "killed", {"reason": emergency_reason})
        result["status"] = "emergency_killed"
        return result

    try:
        wr_by_symbol = metrics.get_wr_by_symbol(days=30, min_trades=MIN_TRADES)

        if not wr_by_symbol:
            result["status"] = "insufficient_data"
            return result

        config = state.load_config()
        current_blacklist = {s.upper() for s in config.get("symbol_blacklist", [])}
        current_whitelist = {s.upper() for s in config.get("symbol_whitelist", [])}

        new_blacklist, new_whitelist, change_log = _decide_changes(
            wr_by_symbol=wr_by_symbol,
            current_blacklist=current_blacklist,
            current_whitelist=current_whitelist,
        )

        ok_bl, msg_bl = safety.validate_symbol_list_change(
            current_blacklist, new_blacklist, "blacklist", MODULE_NAME
        )
        ok_wl, msg_wl = safety.validate_symbol_list_change(
            current_whitelist, new_whitelist, "whitelist", MODULE_NAME
        )

        if not ok_bl:
            logger.info(f"[{MODULE_NAME}] Blacklist BLOQUEADA: {msg_bl}")
            new_blacklist = current_blacklist
            result["errors"].append({"list": "blacklist", "reason": msg_bl})

        if not ok_wl:
            logger.info(f"[{MODULE_NAME}] Whitelist BLOQUEADA: {msg_wl}")
            new_whitelist = current_whitelist
            result["errors"].append({"list": "whitelist", "reason": msg_wl})

        if not ok_bl and not ok_wl:
            result["status"] = "all_validations_failed"
            return result

        if (
            new_blacklist == current_blacklist
            and new_whitelist == current_whitelist
        ):
            result["status"] = "no_changes"
            return result

        snapshot_path = safety.create_snapshot(
            reason="symbol_lists_change",
            config_data=config,
        )

        config["symbol_blacklist"] = sorted(new_blacklist)
        config["symbol_whitelist"] = sorted(new_whitelist)

        saved = state.save_config(config, snapshot=False, reason="symbol_manager")
        if not saved:
            result["errors"].append({"error": "save_failed"})
            result["status"] = "save_failed"
            return result

        bl_added = new_blacklist - current_blacklist
        bl_removed = current_blacklist - new_blacklist
        wl_added = new_whitelist - current_whitelist
        wl_removed = current_whitelist - new_whitelist

        change_record = {
            "blacklist_added": sorted(bl_added),
            "blacklist_removed": sorted(bl_removed),
            "whitelist_added": sorted(wl_added),
            "whitelist_removed": sorted(wl_removed),
            "changes_count": len(bl_added) + len(bl_removed) + len(wl_added) + len(wl_removed),
            "details": change_log,
        }

        safety.log_change(
            module=MODULE_NAME,
            change_type="symbol_lists_update",
            details=change_record,
            sample_size=sum(s["trades"] for s in wr_by_symbol.values()),
            snapshot_path=snapshot_path,
        )

        result["changes"].append(change_record)
        result["status"] = "success"

        logger.info(
            f"[{MODULE_NAME}] BL: +{len(bl_added)}/-{len(bl_removed)} | "
            f"WL: +{len(wl_added)}/-{len(wl_removed)}"
        )

        safety.update_health(MODULE_NAME, "ok", {
            "blacklist_size": len(new_blacklist),
            "whitelist_size": len(new_whitelist),
            "changes": change_record["changes_count"],
        })

    except Exception as e:
        logger.exception(f"[{MODULE_NAME}] Erro inesperado")
        result["status"] = "error"
        result["errors"].append({"exception": str(e)})
        safety.update_health(MODULE_NAME, "error", {"exception": str(e)})

    return result
