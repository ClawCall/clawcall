"""
Base chain payment verification service.

Verifies ERC-20 (USDC / USDT) transfers on Base mainnet.

How it works:
  1. Get transaction receipt via eth_getTransactionReceipt
  2. Check tx succeeded (status == 1)
  3. Find ERC-20 Transfer log from the expected token contract
     - Topic0 = keccak256("Transfer(address,address,uint256)")
     - Topic2 = recipient address (our wallet)
  4. Decode amount from log data
  5. Confirm amount >= expected

ERC-20 Transfer event:
  Transfer(address indexed from, address indexed to, uint256 value)
  topic0: 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef
  topic1: from (padded)
  topic2: to   (padded)
  data:   value (uint256)
"""
import logging
import requests
from src.config import (
    BASE_RPC_URL, CLAWCALL_EVM_WALLET,
    SUPPORTED_TOKENS, TIER_PRICE_BASE,
)

logger = logging.getLogger(__name__)

# ERC-20 Transfer event topic
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _rpc(method: str, params: list) -> dict:
    r = requests.post(
        BASE_RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def _normalize_address(addr: str) -> str:
    """Lowercase, 0x-prefixed address."""
    return addr.lower() if addr.startswith("0x") else f"0x{addr.lower()}"


def _pad_address(addr: str) -> str:
    """Pad address to 32 bytes (64 hex chars + 0x prefix)."""
    clean = addr.lower().replace("0x", "").zfill(64)
    return f"0x{clean}"


def verify_base_payment(tx_hash: str, expected_amount: int, token: str) -> dict:
    """
    Verify a USDC or USDT transfer on Base mainnet.

    Args:
        tx_hash:         Ethereum tx hash (0x...)
        expected_amount: Amount in token units (6 decimals). e.g. 9_000_000 = 9 USDC
        token:           'usdc' or 'usdt'

    Returns:
        {"ok": True,  "amount": int, "from": "0x..."}
        {"ok": False, "error": str}
    """
    token = token.lower()
    if token not in SUPPORTED_TOKENS:
        return {"ok": False, "error": f"Unsupported token: {token}. Use 'usdc' or 'usdt'"}

    if not CLAWCALL_EVM_WALLET:
        return {"ok": False, "error": "CLAWCALL_EVM_WALLET not configured"}

    token_contract = _normalize_address(SUPPORTED_TOKENS[token])
    our_wallet     = _normalize_address(CLAWCALL_EVM_WALLET)

    # 1. Get receipt
    try:
        result = _rpc("eth_getTransactionReceipt", [tx_hash])
    except Exception as e:
        return {"ok": False, "error": f"RPC error: {e}"}

    receipt = result.get("result")
    if not receipt:
        return {"ok": False, "error": "Transaction not found or not yet confirmed"}

    # 2. Check success
    status = receipt.get("status", "0x0")
    if status != "0x1":
        return {"ok": False, "error": "Transaction failed on-chain"}

    # 3. Scan logs for matching Transfer event
    logs = receipt.get("logs", [])
    our_wallet_padded = _pad_address(our_wallet)

    for log in logs:
        log_address = _normalize_address(log.get("address", ""))
        topics      = log.get("topics", [])
        data        = log.get("data", "0x")

        # Must be from the right token contract
        if log_address != token_contract:
            continue

        # Must be a Transfer event
        if not topics or topics[0].lower() != TRANSFER_TOPIC:
            continue

        # topic2 = recipient — must be our wallet
        if len(topics) < 3:
            continue
        to_addr = topics[2].lower()
        if to_addr != our_wallet_padded.lower():
            continue

        # Decode amount from data (uint256, big-endian hex)
        try:
            amount = int(data, 16)
        except (ValueError, TypeError):
            continue

        if amount < expected_amount:
            return {
                "ok": False,
                "error": (
                    f"Insufficient amount: received {amount / 1_000_000:.2f} "
                    f"{token.upper()}, expected {expected_amount / 1_000_000:.2f}"
                ),
            }

        # Decode sender from topic1
        sender = "0x" + topics[1][-40:] if len(topics) > 1 else None

        logger.info(
            f"Payment verified: {amount/1_000_000:.2f} {token.upper()} "
            f"from {sender} in {tx_hash}"
        )
        return {"ok": True, "amount": amount, "from": sender, "token": token.upper()}

    return {
        "ok": False,
        "error": (
            f"No {token.upper()} Transfer to {our_wallet} found in this transaction. "
            f"Make sure you sent to the correct address."
        ),
    }


def get_token_symbol(token: str) -> str:
    return token.upper() if token in SUPPORTED_TOKENS else token.upper()
