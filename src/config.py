import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", 3000))
DATABASE_URL = os.environ.get("DATABASE_URL", "")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WEBHOOK_BASE_URL = os.environ.get("TWILIO_WEBHOOK_BASE_URL", "https://api.clawcall.com")

# ── Solana (kept for future use) ──────────────────────────────────────────
SOLANA_RPC_URL  = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
CLAWCALL_WALLET = os.environ.get("CLAWCALL_WALLET", "")   # Solana wallet (future)
HELIUS_API_KEY  = os.environ.get("HELIUS_API_KEY", "")
USDC_MINT       = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# ── Base chain (primary payments) ─────────────────────────────────────────
BASE_RPC_URL         = os.environ.get("BASE_RPC_URL", "https://mainnet.base.org")
CLAWCALL_EVM_WALLET  = os.environ.get("CLAWCALL_EVM_WALLET", "")  # EVM wallet receives payments

# Token contracts on Base mainnet (both 6 decimals)
BASE_USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
BASE_USDT_ADDRESS = "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"

SUPPORTED_TOKENS = {
    "usdc": BASE_USDC_ADDRESS,
    "usdt": BASE_USDT_ADDRESS,
}

# Pricing in stablecoin units (6 decimals — same for USDC & USDT on Base)
TIER_PRICE_BASE = {
    "pro":  9_000_000,   # 9 USDC/USDT
    "team": 29_000_000,  # 29 USDC/USDT
}

SUBSCRIPTION_DAYS = 30

# Twilio Polly neural voices available for selection
POLLY_VOICES = {
    "aria":    "Polly.Aria-Neural",     # US English, Female (default)
    "joanna":  "Polly.Joanna-Neural",   # US English, Female
    "matthew": "Polly.Matthew-Neural",  # US English, Male
    "amy":     "Polly.Amy-Neural",      # British English, Female
    "brian":   "Polly.Brian-Neural",    # British English, Male
    "emma":    "Polly.Emma-Neural",     # British English, Female
    "olivia":  "Polly.Olivia-Neural",   # Australian English, Female
}
DEFAULT_VOICE = "Polly.Aria-Neural"

# Max agents per tier
TIER_MAX_AGENTS = {
    "free": 1,
    "pro":  1,
    "team": 5,
}

# Tier minute limits per month
TIER_LIMITS = {
    "free": 10,
    "pro": 120,
    "team": 500,
}

# Overage rate: $0.05 per minute, in USDC raw units (6 decimals)
OVERAGE_RATE_RAW = 50_000   # 0.05 USDC

# Filler phrases spoken while agent is processing
FILLER_PHRASES = [
    "On it, give me a second.",
    "Working on that now.",
    "Let me check that for you.",
    "One moment please.",
    "Looking into that.",
    "Just a sec.",
]

# Seconds to wait for agent response before playing filler
AGENT_RESPONSE_TIMEOUT = 8
