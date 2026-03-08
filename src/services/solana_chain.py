"""
Solana payment verification service.

Verifies USDC transfers on Solana mainnet.

How it works:
  1. Get transaction via getTransaction (jsonParsed)
  2. Check meta.err == null (success)
  3. Scan pre/post token balances for our wallet with USDC mint
  4. Calculate delta — confirm >= expected amount

USDC on Solana:
  Mint:     EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
  Decimals: 6
"""
import logging
import requests
from src.config import SOLANA_RPC_URL, CLAWCALL_WALLET, USDC_MINT

logger = logging.getLogger(__name__)


def _rpc(method: str, params: list) -> dict:
    r = requests.post(
        SOLANA_RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def verify_solana_payment(tx_signature: str, expected_amount: int) -> dict:
    """
    Verify a USDC transfer on Solana mainnet.

    Args:
        tx_signature:    Solana transaction signature (base58)
        expected_amount: Amount in USDC raw units (6 decimals). e.g. 9_000_000 = 9 USDC

    Returns:
        {"ok": True,  "amount": int, "token": "USDC"}
        {"ok": False, "error": str}
    """
    if not CLAWCALL_WALLET:
        return {"ok": False, "error": "CLAWCALL_WALLET not configured on server"}

    try:
        result = _rpc("getTransaction", [
            tx_signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ])
    except Exception as e:
        return {"ok": False, "error": f"Solana RPC error: {e}"}

    tx = result.get("result")
    if not tx:
        return {"ok": False, "error": "Transaction not found or not yet confirmed"}

    meta = tx.get("meta", {})
    if meta is None:
        return {"ok": False, "error": "Transaction metadata unavailable"}
    if meta.get("err") is not None:
        return {"ok": False, "error": f"Transaction failed on-chain: {meta['err']}"}

    # Build pre-balance map: {accountIndex -> amount} for our wallet + USDC mint
    pre = {}
    for b in (meta.get("preTokenBalances") or []):
        if b.get("mint") == USDC_MINT and b.get("owner") == CLAWCALL_WALLET:
            pre[b["accountIndex"]] = int(b["uiTokenAmount"]["amount"])

    # Build post-balance map and calculate inflow
    received = 0
    for b in (meta.get("postTokenBalances") or []):
        if b.get("mint") == USDC_MINT and b.get("owner") == CLAWCALL_WALLET:
            idx = b["accountIndex"]
            post_amt = int(b["uiTokenAmount"]["amount"])
            pre_amt  = pre.get(idx, 0)
            delta    = post_amt - pre_amt
            if delta > 0:
                received += delta

    if received == 0:
        return {
            "ok": False,
            "error": (
                f"No USDC transfer to {CLAWCALL_WALLET} found in this transaction. "
                "Ensure you sent to the correct Solana wallet address."
            ),
        }

    if received < expected_amount:
        return {
            "ok": False,
            "error": (
                f"Insufficient amount: received {received / 1_000_000:.2f} USDC, "
                f"expected {expected_amount / 1_000_000:.2f} USDC."
            ),
        }

    logger.info(
        f"Solana payment verified: {received / 1_000_000:.2f} USDC "
        f"→ {CLAWCALL_WALLET} | tx {tx_signature}"
    )
    return {"ok": True, "amount": received, "token": "USDC"}
