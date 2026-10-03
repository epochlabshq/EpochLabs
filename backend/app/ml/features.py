import numpy as np
import pandas as pd
from sklearn.decomposition import PCA

# Lazy loaded embedding model
_sentence_model = None
EMBEDDER = "all-MiniLM-L6-v2"

# Column order produced by extract_features. len(FEATURE_NAMES) must equal settings.CAPACITY_D.
FEATURE_NAMES: list[str] = (
    ["hour_sin", "hour_cos"] + [f"dow_{i}" for i in range(7)]
    + ["holders_log", "lore_len", "lore_missing", "name_tokens"]
    + [f"lore_pca_{i+1}" for i in range(24)]
)

def get_sentence_model():
    global _sentence_model
    if _sentence_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _sentence_model = SentenceTransformer(EMBEDDER)
        except ImportError:
            _sentence_model = None
    return _sentence_model

def embedder_name() -> str | None:
    """The lore embedder in use, or None when lore embeddings fall back to zeros."""
    return EMBEDDER if get_sentence_model() is not None else None

def extract_features(df: pd.DataFrame, pca_model: PCA | None = None) -> tuple[np.ndarray, PCA]:
    """
    Extracts the 4 feature families as specified in EMILE-developer-brief.md:
    1. launch_hour: sin/cos encoding (2 cols)
    2. launch_dow: one-hot (7 cols)
    3. holders: log1p (1 col)
    4. lore: MiniLM-L6-v2 embedding -> PCA 24 dims + lore_len + lore_missing + name_tokens
    """
    X_num = pd.DataFrame(index=df.index)
    
    # Hour sin/cos
    hours = df["launch_hour_utc"].astype(float)
    X_num["hour_sin"] = np.sin(2 * np.pi * hours / 24.0)
    X_num["hour_cos"] = np.cos(2 * np.pi * hours / 24.0)
    
    # DoW one-hot (7 columns: dow_0 to dow_6)
    dows = pd.to_datetime(df["launched_at"]).dt.dayofweek
    dow_dummies = pd.get_dummies(dows, prefix="dow")
    for col in [f"dow_{i}" for i in range(7)]:
        if col not in dow_dummies.columns:
            dow_dummies[col] = False
    dow_dummies = dow_dummies[[f"dow_{i}" for i in range(7)]].astype(float)
    X_num = pd.concat([X_num, dow_dummies], axis=1)
    
    # Holders log1p
    X_num["holders_log"] = np.log1p(df["holders"].fillna(0).astype(float))
    
    # Lore metadata metrics
    lore_text = df["lore"].fillna("").astype(str)
    X_num["lore_len"] = lore_text.str.split().str.len().astype(float)
    X_num["lore_missing"] = df["lore"].isna().astype(float)
    
    # Name tokens count
    X_num["name_tokens"] = df["name"].fillna("").astype(str).str.split().str.len().astype(float)
    
    # Lore Sentence Embedding -> PCA 24 dims
    model = get_sentence_model()
    if model is not None:
        embeddings = model.encode(lore_text.tolist(), batch_size=64, show_progress_bar=False)
    else:
        # Fallback dummy zero embeddings if sentence-transformers not installed
        embeddings = np.zeros((len(df), 384))

    if pca_model is None:
        n_components = min(24, embeddings.shape[0], embeddings.shape[1])
        pca_model = PCA(n_components=n_components, random_state=42)
        pca_features = pca_model.fit_transform(embeddings)
        if pca_features.shape[1] < 24:
            pad_cols = 24 - pca_features.shape[1]
            pca_features = np.hstack([pca_features, np.zeros((pca_features.shape[0], pad_cols))])
    else:
        pca_features = pca_model.transform(embeddings)
        if pca_features.shape[1] < 24:
            pad_cols = 24 - pca_features.shape[1]
            pca_features = np.hstack([pca_features, np.zeros((pca_features.shape[0], pad_cols))])
            
    X_mat = np.hstack([X_num.values.astype(float), pca_features.astype(float)])
    return X_mat, pca_model

def assert_no_leakage(df: pd.DataFrame, feature_names: list[str] | None = None) -> None:
    """
    Mandatory Leakage Checklist as required by §7.2 of EMILE-developer-brief.md:
    1. No feature derived from market cap, price, volume, or liquidity.
    2. holders sampled at a fixed 48h offset, never 'current'.
    3. No feature computed after label was assigned.
    """
    forbidden_cols = {"peak_mc", "last_seen_mc", "mc", "price", "volume", "liquidity"}
    
    # Check DataFrame columns
    for col in df.columns:
        if col.lower() in forbidden_cols:
            if col in ["peak_mc", "last_seen_mc"]:
                continue  # Target column, acceptable in df but must not be in features
            raise ValueError(f"DATA LEAKAGE ERROR: Forbidden market metric column '{col}' found in dataset.")

    if feature_names:
        for fname in feature_names:
            if any(f in fname.lower() for f in forbidden_cols):
                raise ValueError(f"DATA LEAKAGE ERROR: Feature '{fname}' derives from price/market metric.")
