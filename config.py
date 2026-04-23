"""
config.py — Carregamento centralizado de configurações
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Exchange
MEXC_API_KEY     = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY  = os.getenv("MEXC_SECRET_KEY", "")

# AI
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")

# Telegram
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# APIs externas
COINGLASS_API_KEY   = os.getenv("COINGLASS_API_KEY", "07b9e463298b43cc9d0008b1c13d8194")
GLASSNODE_API_KEY   = os.getenv("GLASSNODE_API_KEY", "")
CRYPTOQUANT_API_KEY = os.getenv("CRYPTOQUANT_API_KEY", "")

# FRED (Federal Reserve Economic Data) — gratuito, sem limite
FRED_API_KEY = os.getenv("FRED_API_KEY", "816262af9534ed9260cd1171a22fd964")

# Parâmetros do agente
SYMBOL                   = os.getenv("SYMBOL", "BTC/USDT:USDT")
TIMEFRAME                = os.getenv("TIMEFRAME", "15m")
SCORE_THRESHOLD          = int(os.getenv("SCORE_THRESHOLD", "65"))
SUMMARY_INTERVAL_MINUTES = int(os.getenv("SUMMARY_INTERVAL_MINUTES", "120"))   # resumo simples (sem IA)
SIGNAL_INTERVAL_MINUTES  = int(os.getenv("SIGNAL_INTERVAL_MINUTES", "240"))    # análise + Claude + sinal completo

# Parâmetros do agente institucional
INST_INTERVAL_MINUTES = int(os.getenv("INST_INTERVAL_MINUTES", "60"))   # análise institucional
INST_SCORE_THRESHOLD  = int(os.getenv("INST_SCORE_THRESHOLD", "60"))    # mínimo para enviar sinal

# Scoring V2 (BoostManager) — ver trinity/scoring/*.
# SCORING_V2_ENABLED=True  -> V2 computado em shadow junto com V1 (sem efeito na decisão)
# SCORING_V2_LIVE=True     -> V2 DIRIGE decisão; V1 vira sombra. (PROMOÇÃO MANUAL pós-validação.)
SCORING_V2_ENABLED = os.getenv("SCORING_V2_ENABLED", "true").lower() == "true"
SCORING_V2_LIVE    = os.getenv("SCORING_V2_LIVE", "false").lower() == "true"

# Pesos do score probabilístico (devem somar 100)
WEIGHTS = {
    "regime":       10,   # Regime de mercado
    "trend":        25,   # Tendência
    "momentum":     15,   # Momentum
    "volume":       10,   # Volume
    "derivatives":  15,   # Derivativos (OI, Funding, L/S ratio)
    "liquidations": 15,   # Liquidações + dados derivados (Coinglass + CryptoQuant)
    "onchain":       5,   # On-chain
    "sentiment":     5,   # Sentimento
}
