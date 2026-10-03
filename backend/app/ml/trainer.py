import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.metrics import roc_auc_score

from app.ml.artifact import build_artifact
from app.ml.features import FEATURE_NAMES, extract_features, assert_no_leakage
from app.ml.jar_math import evaluate_jar_level
from app.core.config import settings

def evaluate_time_split_gap(X: np.ndarray, y: np.ndarray, df: pd.DataFrame) -> float:
    """
    Computes time-based CV split AUC gap as a sanity check against data leakage.
    Train on older 70% tokens, test on newer 30% tokens.
    Returns: abs(stratified_auc - time_split_auc)
    """
    if len(df) < 50:
        return 0.0

    # Sort by launched_at
    sorted_indices = df["launched_at"].argsort().values
    X_sorted = X[sorted_indices]
    y_sorted = y[sorted_indices]

    split_idx = int(len(df) * 0.7)
    X_train, X_test = X_sorted[:split_idx], X_sorted[split_idx:]
    y_train, y_test = y_sorted[:split_idx], y_sorted[split_idx:]

    if len(np.unique(y_train)) < 2 or len(np.unique(y_test)) < 2:
        return 0.0

    clf = LGBMClassifier(
        n_estimators=400,
        learning_rate=0.03,
        class_weight="balanced",
        min_child_samples=max(10, min(40, len(y_train) // 5)),
        random_state=42,
        verbose=-1
    )
    clf.fit(X_train, y_train)
    y_pred_proba = clf.predict_proba(X_test)[:, 1]
    time_auc = roc_auc_score(y_test, y_pred_proba)

    # 5-fold CV for comparison
    cv = StratifiedKFold(n_splits=min(5, max(2, len(df) // 20)), shuffle=True, random_state=42)
    strat_auc = cross_val_score(clf, X, y, cv=cv, scoring="roc_auc").mean()

    return abs(float(strat_auc - time_auc))

def train_model_and_evaluate(df: pd.DataFrame) -> dict:
    """
    Full Training Job Pipeline:
    1. Check leakage
    2. Extract features
    3. Fit LGBM with 5-fold Stratified CV
    4. Time-based split sanity check
    5. Evaluate VC bound, Bootstrap bound, Gates, and Jar level
    """
    if len(df) < 20:
        return {
            "error": "Insufficient labeled samples for training",
            "n_samples": len(df),
            "jar_level": 0.0,
            "proven_floor": 0.50,
            "blocked_by": "n_samples"
        }

    # 1. Assert no leakage
    assert_no_leakage(df)

    # 2. Extract features
    X, pca_model = extract_features(df)
    y = (df["status"] == "passed").astype(int).values

    # The VC penalty is only honest if d matches the real feature count.
    if X.shape[1] != settings.CAPACITY_D:
        raise ValueError(
            f"Feature matrix has {X.shape[1]} columns but CAPACITY_D={settings.CAPACITY_D}; "
            "update settings.CAPACITY_D and the published methodology together."
        )

    n_samples = len(y)
    n_positive = int(y.sum())

    if n_positive == 0 or n_positive == n_samples:
        return {
            "error": "Dataset lacks variance in target class",
            "n_samples": n_samples,
            "n_positive": n_positive,
            "jar_level": 0.0,
            "proven_floor": 0.50,
            "blocked_by": "n_positive"
        }

    # 3. 5-Fold Stratified Cross Validation
    n_splits = min(5, max(2, n_samples // 10))
    cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

    min_child = max(5, min(40, n_samples // 10))
    clf = LGBMClassifier(
        n_estimators=400,
        learning_rate=0.03,
        class_weight="balanced",
        min_child_samples=min_child,
        random_state=42,
        verbose=-1
    )

    cv_scores = cross_val_score(clf, X, y, cv=cv, scoring="roc_auc")
    auc_mean = float(cv_scores.mean())
    auc_std = float(cv_scores.std())

    # Fit model on full data for probability prediction
    clf.fit(X, y)
    y_pred_proba = clf.predict_proba(X)[:, 1]

    # 4. Time split gap
    time_gap = evaluate_time_split_gap(X, y, df)

    # 5. Evaluate Jar Math & Gates
    res = evaluate_jar_level(
        auc_mean=auc_mean,
        auc_std=auc_std,
        n_samples=n_samples,
        n_positive=n_positive,
        y_true=y,
        y_pred_proba=y_pred_proba,
        time_split_gap=time_gap,
        d=settings.CAPACITY_D
    )

    # Feature Importance calculation
    feature_names = FEATURE_NAMES
    importances = clf.feature_importances_
    total_imp = importances.sum()
    if total_imp > 0:
        norm_imp = importances / total_imp
    else:
        norm_imp = np.zeros_like(importances)

    fi_dict = {
        name: round(float(imp), 4)
        for name, imp in sorted(zip(feature_names, norm_imp), key=lambda x: x[1], reverse=True)[:10]
    }
    res["feature_importance"] = fi_dict

    # Hourly survival rates calculation
    df_temp = df.copy()
    df_temp["passed"] = y
    hour_rates = df_temp.groupby("launch_hour_utc")["passed"].mean().to_dict()
    res["hour_rates"] = {str(k): round(float(v), 3) for k, v in hour_rates.items()}

    # Everything needed to score a new token exactly as this run would (persisted by the model worker)
    res["artifact"] = build_artifact(clf, pca_model)

    return res
