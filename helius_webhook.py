"""
Solana leg. Helius pushes parsed swap events here the instant a tracked
wallet trades — no polling, no missed fills.

Setup once your bot is deployed:
  1. Get a Helius API key: https://dev.helius.xyz
  2. Create a webhook (dashboard or API) pointed at:
       {PUBLIC_BASE_URL}/webhook/helius
     transaction type: SWAP, account addresses: your tracked Solana wallets
  3. Every time you /addwallet a Solana address, the bot should also PATCH
     the Helius webhook to add it — see bot/handlers.py add_wallet().
"""
from fastapi import APIRouter, Request
from sqlalchemy import select

from database import Wallet, Trade, get_session
from scoring.confluence import score_token
from scoring.security import check_token
from alerts.notifier import send_alert, send_trade_notification
from database import Alert as AlertLog

router = APIRouter()


@router.post("/webhook/helius")
async def helius_webhook(request: Request):
    payload = await request.json()
    # Helius sends a list of enhanced transactions
    events = payload if isinstance(payload, list) else [payload]

    for event in events:
        await _handle_event(event)

    return {"ok": True}


async def _handle_event(event: dict):
    swap = _extract_swap(event)
    if swap is None:
        return

    wallet_address, token_address, token_symbol, action, amount_usd, tx_hash = swap

    session = get_session()
    try:
        wallet = session.execute(
            select(Wallet).where(Wallet.address == wallet_address, Wallet.chain == "solana")
        ).scalar_one_or_none()
        if wallet is None:
            return  # not one of ours

        trade = Trade(
            wallet_id=wallet.id,
            chain="solana",
            token_address=token_address,
            token_symbol=token_symbol,
            action=action,
            amount_usd=amount_usd,
            tx_hash=tx_hash,
        )
        session.add(trade)
        session.commit()
        wallet_label, wallet_addr = wallet.label, wallet.address
    finally:
        session.close()

    # Every trade from a tracked wallet gets a notification — buy or sell,
    # regardless of whether it later clears the confluence bar.
    await send_trade_notification(
        wallet_label, wallet_addr, "solana", action, token_address, token_symbol, amount_usd,
    )

    if action != "buy":
        return

    opp = score_token("solana", token_address)
    if not opp.is_alertable:
        return

    sec = await check_token("solana", token_address)
    if not sec.passed:
        print(f"[helius] {token_address} scored {opp.score} but failed security: {sec.reasons}")
        return

    log_session = get_session()
    try:
        log_session.add(AlertLog(
            chain="solana", token_address=token_address,
            score=opp.score, wallet_count=opp.wallet_count, passed_security=True,
        ))
        log_session.commit()
    finally:
        log_session.close()

    await send_alert(opp, sec, token_symbol)


def _extract_swap(event: dict):
    """
    Parses a Helius enhanced-transaction SWAP event.
    Real payload shape: https://docs.helius.dev/webhooks-and-websockets/webhooks
    Adjust field paths if Helius changes their schema.
    """
    if event.get("type") != "SWAP":
        return None

    fee_payer = event.get("feePayer")
    swap_data = event.get("events", {}).get("swap", {})
    token_outputs = swap_data.get("tokenOutputs", [])
    token_inputs = swap_data.get("tokenInputs", [])

    if token_outputs:
        token_address = token_outputs[0].get("mint")
        token_symbol = token_outputs[0].get("symbol", "")
        action = "buy"
        amount_usd = float(token_outputs[0].get("tokenAmount", 0))
    elif token_inputs:
        token_address = token_inputs[0].get("mint")
        token_symbol = token_inputs[0].get("symbol", "")
        action = "sell"
        amount_usd = float(token_inputs[0].get("tokenAmount", 0))
    else:
        return None

    if not fee_payer or not token_address:
        return None

    return fee_payer, token_address, token_symbol, action, amount_usd, event.get("signature", "")
