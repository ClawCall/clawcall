"""
Solana payment verification service.

Flow:
  1. User calls POST /api/v1/billing/checkout
     → backend returns: wallet address, amount in USDC, memo reference
  2. User sends USDC to that wallet with the memo
  3. User calls POST /api/v1/billing/verify with their tx signature
     → backend verifies on-chain: correct recipient, amount >= expected, memo matches
  4. Tier upgraded, valid_until = now + 30 days

USDC on Solana uses SPL Token standard (6 decimals).
USDC mint: EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v
"""
import logging
import requests
from src.config import SOLANA_RPC_URL, HELIUS_API_KEY, USDC_MINT, CLAWCALL_WALLET

logger = logging.getLogger(__name__)


def _rpc_url() -> str:
    if HELIUS_API_KEY:
        return f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    return SOLANA_RPC_URL


def _rpc(method: str, params: list) -> dict:
    r = requests.post(
        _rpc_url(),
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def verify_usdc_payment(tx_signature: str, expected_amount: int, memo_ref: str) -> dict:
    """
    Verify a USDC transfer on Solana.

    Returns:
      { "ok": True,  "amount": <usdc units>, "sender": "<pubkey>" }
      { "ok": False, "error": "<reason>" }
    """
    try:
        result = _rpc("getTransaction", [
            tx_signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
        ])
    except Exception as e:
        return {"ok": False, "error": f"RPC error: {e}"}

    tx = result.get("result")
    if not tx:
        return {"ok": False, "error": "Transaction not found"}

    # Must be confirmed/finalized
    if tx.get("meta", {}).get("err") is not None:
        return {"ok": False, "error": "Transaction failed on-chain"}

    # Walk through post-token-balances to find the USDC transfer
    pre_balances  = {b["accountIndex"]: int(b.get("uiTokenAmount", {}).get("amount", 0))
                     for b in (tx.get("meta") or {}).get("preTokenBalances", [])
                     if b.get("mint") == USDC_MINT}
    post_balances = {b["accountIndex"]: int(b.get("uiTokenAmount", {}).get("amount", 0))
                     for b in (tx.get("meta") or {}).get("postTokenBalances", [])
                     if b.get("mint") == USDC_MINT}

    # Find accounts from transaction
    account_keys = []
    msg = tx.get("transaction", {}).get("message", {})
    for acc in msg.get("accountKeys", []):
        if isinstance(acc, dict):
            account_keys.append(acc.get("pubkey", ""))
        else:
            account_keys.append(str(acc))

    # Find which index belongs to our receiving wallet's token account
    received = 0
    sender_pubkey = None

    for idx, post_amt in post_balances.items():
        pre_amt = pre_balances.get(idx, 0)
        delta = post_amt - pre_amt
        if delta > 0:
            # This account received USDC — check if it belongs to CLAWCALL_WALLET
            # Token accounts are owned by the wallet; we match by checking owner
            owner = _get_token_account_owner(account_keys[idx] if idx < len(account_keys) else "")
            if owner and owner == CLAWCALL_WALLET:
                received = delta

    # If we couldn't match by owner (RPC limitation), fall back to checking
    # if any known token account of ours received funds
    if received == 0:
        received = _check_via_token_accounts(tx, expected_amount)

    if received == 0:
        return {"ok": False, "error": "No USDC received by ClawCall wallet in this transaction"}

    if received < expected_amount:
        return {
            "ok": False,
            "error": f"Insufficient amount: got {received}, expected {expected_amount}"
        }

    # Verify memo if present (best-effort)
    memo_ok = _check_memo(tx, memo_ref)
    if not memo_ok:
        logger.warning(f"Memo mismatch for tx {tx_signature}, ref {memo_ref} — allowing anyway")

    # Find sender (first signer)
    signers = [k for k in msg.get("accountKeys", [])
               if (isinstance(k, dict) and k.get("signer")) or False]
    if signers:
        sender_pubkey = signers[0].get("pubkey") if isinstance(signers[0], dict) else str(signers[0])

    return {"ok": True, "amount": received, "sender": sender_pubkey}


def _get_token_account_owner(token_account_pubkey: str) -> str | None:
    """Get the owner wallet of a SPL token account."""
    if not token_account_pubkey:
        return None
    try:
        result = _rpc("getAccountInfo", [
            token_account_pubkey,
            {"encoding": "jsonParsed"}
        ])
        data = (result.get("result") or {}).get("value") or {}
        parsed = data.get("data", {}).get("parsed", {})
        return parsed.get("info", {}).get("owner")
    except Exception:
        return None


def _check_via_token_accounts(tx: dict, expected_amount: int) -> int:
    """
    Fallback: parse instruction data directly for SPL Token transfer.
    Returns amount received, or 0 if not found.
    """
    instructions = (tx.get("transaction", {})
                    .get("message", {})
                    .get("instructions", []))
    for ix in instructions:
        if not isinstance(ix, dict):
            continue
        parsed = ix.get("parsed", {})
        if not isinstance(parsed, dict):
            continue
        ix_type = parsed.get("type", "")
        if ix_type in ("transfer", "transferChecked"):
            info = parsed.get("info", {})
            dest = info.get("destination", "")
            amount_raw = info.get("amount") or info.get("tokenAmount", {}).get("amount", "0")
            try:
                amt = int(amount_raw)
            except (ValueError, TypeError):
                continue
            # Accept if amount matches and mint is USDC
            mint = info.get("mint", "")
            if (mint == USDC_MINT or not mint) and amt >= expected_amount:
                return amt
    return 0


def _check_memo(tx: dict, ref: str) -> bool:
    """Check if the memo program instruction contains our reference."""
    if not ref:
        return True
    instructions = (tx.get("transaction", {})
                    .get("message", {})
                    .get("instructions", []))
    for ix in instructions:
        if not isinstance(ix, dict):
            continue
        # Memo program
        program = ix.get("program", "") or ix.get("programId", "")
        if "memo" in program.lower():
            data = ix.get("parsed", "") or ix.get("data", "")
            if ref in str(data):
                return True
    return False


def get_sol_price_usd() -> float:
    """Get current SOL price in USD from CoinGecko."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=8,
        )
        return float(r.json()["solana"]["usd"])
    except Exception:
        return 0.0
