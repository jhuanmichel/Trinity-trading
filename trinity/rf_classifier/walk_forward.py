"""
Walk-Forward Validation

Validacao cronologica (nao-aleatoria) - treina em janela X, testa em janela X+1.
Repete N folds. Metrica: AUC media + std.

Esta e a validacao MAIS HONESTA pra series temporais. Cross-validation aleatorio
(KFold) leak data do futuro pro passado.
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("rf_classifier.walk_forward")


def walk_forward_validate(
    X: list[list[float]],
    y: list[int],
    timestamps: list[str],
    model_factory,
    n_folds: int = 5,
    min_train_size: int = 200,
) -> dict:
    """
    Executa walk-forward validation.

    Args:
        X: features (lista de vetores)
        y: labels (0=loss, 1=win)
        timestamps: ISO timestamp de cada amostra (pra ordenacao)
        model_factory: callable que retorna modelo novo
        n_folds: quantos splits cronologicos
        min_train_size: tamanho minimo do conjunto de treino no primeiro fold

    Returns:
        {
            "n_folds": 5,
            "fold_aucs": [0.68, 0.71, ...],
            "auc_mean": 0.70,
            "auc_std": 0.015,
            "is_stable": True,
            "recommend_apply": True,
        }
    """
    try:
        from sklearn.metrics import roc_auc_score, accuracy_score
        import numpy as np
    except ImportError as e:
        return {"error": f"sklearn nao instalado: {e}"}

    if len(X) != len(y) or len(X) != len(timestamps):
        return {"error": "X, y, timestamps com tamanhos diferentes"}

    # Ordenar por timestamp (CRITICO pra walk-forward)
    indices = sorted(range(len(X)), key=lambda i: timestamps[i])
    X = [X[i] for i in indices]
    y = [y[i] for i in indices]
    timestamps = [timestamps[i] for i in indices]

    n = len(X)

    if n < min_train_size + n_folds * 50:
        return {
            "error": f"samples insuficientes: {n} < {min_train_size + n_folds * 50}",
            "n_samples": n,
        }

    fold_size = n // (n_folds + 1)

    fold_aucs = []
    fold_accs = []
    fold_details = []

    for fold in range(n_folds):
        train_end = min_train_size + fold * fold_size
        test_end = min(train_end + fold_size, n)

        if train_end >= n or test_end <= train_end:
            break

        X_train = X[:train_end]
        y_train = y[:train_end]
        X_test = X[train_end:test_end]
        y_test = y[train_end:test_end]

        unique_train = set(y_train)
        unique_test = set(y_test)

        if len(unique_train) < 2 or len(unique_test) < 2:
            logger.warning(
                f"[WF] Fold {fold}: classes insuficientes "
                f"(train={unique_train}, test={unique_test}) - skip"
            )
            continue

        try:
            model = model_factory()
            model.fit(X_train, y_train)

            try:
                y_proba = model.predict_proba(X_test)[:, 1]
                auc = roc_auc_score(y_test, y_proba)
            except Exception:
                auc = 0.5

            y_pred = model.predict(X_test)
            acc = accuracy_score(y_test, y_pred)

            fold_aucs.append(auc)
            fold_accs.append(acc)
            fold_details.append({
                "fold": fold,
                "train_size": len(X_train),
                "test_size": len(X_test),
                "auc": auc,
                "accuracy": acc,
            })

            logger.info(
                f"[WF] Fold {fold}: train={len(X_train)} test={len(X_test)} "
                f"AUC={auc:.3f} ACC={acc:.3f}"
            )
        except Exception as e:
            logger.warning(f"[WF] Fold {fold} erro: {e}")
            continue

    if not fold_aucs:
        return {"error": "nenhum fold valido", "n_samples": n}

    auc_mean = float(np.mean(fold_aucs))
    auc_std = float(np.std(fold_aucs))
    acc_mean = float(np.mean(fold_accs))

    is_stable = auc_std < 0.05
    recommend_apply = auc_mean >= 0.55 and is_stable

    return {
        "n_folds_completed": len(fold_aucs),
        "n_folds_requested": n_folds,
        "n_samples": n,
        "fold_aucs": fold_aucs,
        "fold_accuracies": fold_accs,
        "fold_details": fold_details,
        "auc_mean": auc_mean,
        "auc_std": auc_std,
        "accuracy_mean": acc_mean,
        "is_stable": is_stable,
        "recommend_apply": recommend_apply,
    }
