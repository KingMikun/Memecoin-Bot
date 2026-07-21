"""
EVM leg — Ethereum, Base, and Robinhood Chain (Arbitrum-Orbit, EVM-compatible,
mainnet since July 1 2026) all flow through the same Alchemy Address Activity
webhook. One parser, three chains.

Setup:
  1. Alchemy dashboard → create an Address Activity webhook per chain
     pointed at {PUBLIC_BASE_URL}/webhook/evm
  2. Add tracked wallet addresses via /addwallet — the bot PATCHes the
     webhook automatically (see bot/handlers.py)
  3. Robinhood Chain: use its RPC/network slug once Alchemy lists it as a
     supported network in your dashboard; the parser below is chain-agnostic.
"""
from fastapi import APIRouter, Request
from sqlalchemy import select

from database import Wallet, Trade, get_session
from scoring.confluence import score_token
from scoring.security import check_token
from alerts.notifier import send_alert, send_trade_notification
from database import Alert as AlertLog
from utils.price import fetch_token_market_data

router = APIRouter()

_NETWORK_TO_CHAIN = {
    "ETH_MAINNET": "ethereum",
    "BASE_MAINNET": "base",
    "ROBINHOOD_MAINNET": "robinhood",  # follows Alchemy's ETH_MAINNET/BASE_MAINNET convention;
                                        # confirmed the RPC subdomain is "robinhood-mainnet", so this
                                        # is very likely right, but verify against a real webhook payload once one arrives
}


@router.post("/webhook/evm")
async def evm_webhook(request: Request):
    payload = await request.json()
    activity = payload.get("event", {}).get("activity", [])
    network = payload.get("event", {}).get("network", "")
    chain = _NETWORK_TO_CHAIN.get(network, "ethereum")

    for item in activity:
        await _handle_activity(chain, item)

    return {"ok": True}


async def _handle_activity(chain: str, item: dict):
    wallet_address = item.get("fromAddress") or item.get("toAddress")
    token_address = item.get("rawContract", {}).get("address")
    if not wallet_address or not token_address:
        return

    session = get_session()
    try:
        wallet = session.execute(
            select(Wallet).where(Wallet.address == wallet_address.lower(), Wallet.chain == chain)
        ).scalar_one_or_none()
        if wallet is None:
            return

        # fromAddress == tracked wallet -> they sent the token out -> sell
        # toAddress == tracked wallet -> they received it -> buy
        action = "sell" if item.get("fromAddress", "").lower() == wallet_address.lower() else "buy"
        token_amount = float(item.get("value") or 0)
        price, market_cap = await fetch_token_market_data(chain, token_address)
        amount_usd = token_amount * price if price is not None else 0.0

        trade = Trade(
            wallet_id=wallet.id,
            chain=chain,
            token_address=token_address,
            token_symbol=item.get("asset", ""),
            action=action,
            token_amount=token_amount,
            amount_usd=amount_usd,
            entry_mcap=market_cap,
            tx_hash=item.get("hash", ""),
        )
        session.add(trade)
        session.commit()
        wallet_label, wallet_addr = wallet.label, wallet.address
    finally:
        session.close()

    await send_trade_notification(
        wallet_label, wallet_addr, chain, action, token_address, item.get("asset", ""),
        token_amount, amount_usd, market_cap,
    )

    if action != "buy":
        return

    opp = score_token(chain, token_address)
    if not opp.is_alertable:
        return

    sec = await check_token(chain, token_address)
    if not sec.passed:
        print(f"[evm] {token_address} on {chain} scored {opp.score} but failed security: {sec.reasons}")
        return

    log_session = get_session()
    try:
        log_session.add(AlertLog(
            chain=chain, token_address=token_address,
            score=opp.score, wallet_count=opp.wallet_count, passed_security=True,
        ))
        log_session.commit()
    finally:
        log_session.close()

    await send_alert(opp, sec, item.get("asset", ""))
