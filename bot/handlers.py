"""
The commands you actually type. Kept tight on purpose:
  /start            intro
  /addwallet        track + label a wallet
  /wallets          list what you're tracking
  /label            re-label an existing wallet
  /untrack          drop a wallet
  /stats            quick win-rate leaderboard
  /wallethistory    trade history, realized PnL, win rate for one wallet
  /help             command list
"""
import logging

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from database import Wallet, Subscriber, get_session
from config import CHAINS, EVM_CHAINS
from utils.webhook_sync import sync_wallet
from scoring.wallet_stats import get_wallet_stats, format_wallet_history
from utils.onchain_lookup import fetch_solana_preview, fetch_evm_preview, fetch_evm_preview_all_chains, format_preview

logger = logging.getLogger(__name__)

VALID_CHAINS = ", ".join(CHAINS.keys())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session()
    try:
        chat_id = str(update.effective_chat.id)
        exists = session.execute(
            select(Subscriber).where(Subscriber.chat_id == chat_id)
        ).scalar_one_or_none()
        if not exists:
            session.add(Subscriber(chat_id=chat_id))
            session.commit()
    finally:
        session.close()

    await update.message.reply_text(
        "King Analytics — Wallet Intelligence 🟢\n\n"
        "Sharp money leaves footprints. I watch the ones that matter.\n\n"
        "Get started:\n"
        "/addwallet <address> <chain> <label> — track a wallet\n"
        "/wallets — see what you're tracking\n"
        "/help — full command list"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Commands*\n\n"
        f"/addwallet `<address>` `<chain>` `<label>` — track a wallet\n"
        f"  chains: {VALID_CHAINS}\n"
        "/wallets — list tracked wallets\n"
        "/label `<address>` `<new label>` — rename (EVM auto-applies to all EVM chains)\n"
        "/untrack `<address>` — stop tracking (EVM removes from all EVM chains)\n"
        "/stats — win-rate leaderboard\n"
        "/wallethistory `<address>` — trade history, realized PnL, and win rate for one wallet\n",
        parse_mode="Markdown",
    )


async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /addwallet <address> <chain> <label>\n"
            f"Chains: {VALID_CHAINS}\n"
            "(EVM wallets auto-track across Ethereum, Base, and Robinhood Chain — "
            "the chain you enter just needs to be any one of the three.)"
        )
        return

    address, chain, *label_parts = args
    chain = chain.lower()
    label = " ".join(label_parts)

    if chain not in CHAINS:
        await update.message.reply_text(f"Unknown chain '{chain}'. Pick from: {VALID_CHAINS}")
        return

    # A wallet address is either Solana-shaped or EVM-shaped — never both — so
    # one address always maps to exactly one *kind* of chain(s) to track.
    chains_to_track = EVM_CHAINS if CHAINS[chain]["kind"] == "evm" else [chain]
    normalized_address = address.lower() if CHAINS[chain]["kind"] == "evm" else address

    added_on, already_on, sync_notes = [], [], []

    session = get_session()
    try:
        for c in chains_to_track:
            existing = session.execute(
                select(Wallet).where(Wallet.address == normalized_address, Wallet.chain == c)
            ).scalar_one_or_none()
            if existing:
                already_on.append(CHAINS[c]["label"])
                continue

            session.add(Wallet(
                address=normalized_address,
                chain=c,
                label=label,
                added_by=str(update.effective_user.id),
            ))
            added_on.append(c)
        session.commit()
    finally:
        session.close()

    for c in added_on:
        result = await sync_wallet(normalized_address, c, CHAINS[c]["kind"], action="add")
        status = "✅" if result.ok else f"⚠️ {result.message}"
        sync_notes.append(f"{CHAINS[c]['label']}: {status}")

    if not added_on:
        await update.message.reply_text(
            f"Already tracking {label or normalized_address} on: {', '.join(already_on)}\n"
            "Use /label to rename it."
        )
        return

    reply = f"Tracking {label or normalized_address} ✅\n" + "\n".join(sync_notes)
    if already_on:
        reply += f"\n(already tracked on: {', '.join(already_on)})"
    await update.message.reply_text(reply)


async def list_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session()
    try:
        wallets = session.execute(select(Wallet)).scalars().all()
    finally:
        session.close()

    if not wallets:
        await update.message.reply_text("Nothing tracked yet. /addwallet to start.")
        return

    lines = []
    for w in wallets:
        wr = f" — {w.win_rate}% WR" if w.win_rate is not None else ""
        lines.append(f"• {w.label or 'unlabeled'} | {CHAINS[w.chain]['label']} | `{w.address}`{wr}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _resolve_target_chains(address: str, explicit_chain: str | None = None):
    """
    Same logic as add_wallet: an EVM address is tracked on every EVM chain,
    so by default /label and /untrack act on all of them at once. Passing an
    explicit chain narrows the action to just that one (e.g. to untrack from
    Base only, leaving Ethereum and Robinhood Chain tracked).

    Solana addresses never contain '0' (excluded from base58) and EVM
    addresses always start with "0x" — so the two are never ambiguous.
    """
    if explicit_chain:
        explicit_chain = explicit_chain.lower()
        if explicit_chain not in CHAINS:
            return None, None
        normalized = address.lower() if CHAINS[explicit_chain]["kind"] == "evm" else address
        return [explicit_chain], normalized

    if address.lower().startswith("0x"):
        return EVM_CHAINS, address.lower()
    return ["solana"], address


async def label_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /label <address> <new label> [chain]\n"
            "No chain needed for EVM wallets — relabels across Ethereum, Base, "
            "and Robinhood Chain at once. Add a chain at the end to override."
        )
        return

    # Optional trailing chain override, e.g. "/label 0xabc mikun base"
    if args[-1].lower() in CHAINS:
        address, *label_parts, explicit_chain = args
    else:
        address, *label_parts = args
        explicit_chain = None
    new_label = " ".join(label_parts)

    chains, normalized = _resolve_target_chains(address, explicit_chain)
    if chains is None:
        await update.message.reply_text(f"Unknown chain '{explicit_chain}'. Pick from: {VALID_CHAINS}")
        return

    session = get_session()
    try:
        wallets = session.execute(
            select(Wallet).where(Wallet.address == normalized, Wallet.chain.in_(chains))
        ).scalars().all()
        if not wallets:
            await update.message.reply_text("Wallet not found.")
            return
        for w in wallets:
            w.label = new_label
        session.commit()
        n = len(wallets)
    finally:
        session.close()

    await update.message.reply_text(f"Relabeled to '{new_label}' across {n} chain(s) ✅")


async def untrack_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /untrack <address> [chain]\n"
            "No chain needed for EVM wallets — untracks from Ethereum, Base, and "
            "Robinhood Chain at once. Add a chain to remove from just that one."
        )
        return

    address = args[0]
    explicit_chain = args[1].lower() if len(args) > 1 else None

    chains, normalized = _resolve_target_chains(address, explicit_chain)
    if chains is None:
        await update.message.reply_text(f"Unknown chain '{explicit_chain}'. Pick from: {VALID_CHAINS}")
        return

    session = get_session()
    try:
        wallets = session.execute(
            select(Wallet).where(Wallet.address == normalized, Wallet.chain.in_(chains))
        ).scalars().all()
        if not wallets:
            await update.message.reply_text("Wallet not found.")
            return
        removed = [(w.address, w.chain, CHAINS[w.chain]["kind"]) for w in wallets]
        for w in wallets:
            session.delete(w)
        session.commit()
    finally:
        session.close()

    sync_failures = []
    for addr, c, kind in removed:
        result = await sync_wallet(addr, c, kind, action="remove")
        if not result.ok:
            sync_failures.append(f"{CHAINS[c]['label']}: {result.message}")

    reply = f"Untracked from {len(removed)} chain(s) ✅"
    if sync_failures:
        reply += "\n⚠️ Webhook cleanup failed for:\n" + "\n".join(f"• {f}" for f in sync_failures)
        reply += "\n(safe to ignore — just means the address stays on that webhook)"
    await update.message.reply_text(reply)




async def import_wallets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Bulk add. Usage:
      /importwallets
      <address>, <chain>, <label>
      <address>, <chain>, <label>
      ...
    One wallet per line, comma-separated.
    """
    text = update.message.text or ""
    lines = text.split("\n")[1:]  # drop the /importwallets line itself
    if not lines or not any(l.strip() for l in lines):
        await update.message.reply_text(
            "Usage:\n/importwallets\n<address>, <chain>, <label>\n<address>, <chain>, <label>\n\n"
            f"Chains: {VALID_CHAINS}"
        )
        return

    session = get_session()
    added, skipped, to_sync = [], [], []
    try:
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                skipped.append(line)
                continue

            address, chain = parts[0], parts[1].lower()
            label = parts[2] if len(parts) > 2 else ""

            if chain not in CHAINS:
                skipped.append(f"{line} (unknown chain)")
                continue

            chains_to_track = EVM_CHAINS if CHAINS[chain]["kind"] == "evm" else [chain]
            normalized = address.lower() if CHAINS[chain]["kind"] == "evm" else address

            line_added_any = False
            for c in chains_to_track:
                existing = session.execute(
                    select(Wallet).where(Wallet.address == normalized, Wallet.chain == c)
                ).scalar_one_or_none()
                if existing:
                    continue

                session.add(Wallet(
                    address=normalized, chain=c, label=label,
                    added_by=str(update.effective_user.id),
                ))
                to_sync.append((normalized, c, CHAINS[c]["kind"]))
                line_added_any = True

            if line_added_any:
                added.append(label or normalized)
            else:
                skipped.append(f"{line} (already tracked)")
        session.commit()
    finally:
        session.close()

    sync_failures = []
    for address, chain, kind in to_sync:
        result = await sync_wallet(address, chain, kind, action="add")
        if not result.ok:
            sync_failures.append(f"{address[:8]}... ({chain}): {result.message}")

    msg = f"Imported {len(added)} wallet(s) ✅"
    if added:
        msg += "\n" + "\n".join(f"• {a}" for a in added)
    if skipped:
        msg += f"\n\nSkipped {len(skipped)}:\n" + "\n".join(f"• {s}" for s in skipped)
    if sync_failures:
        msg += f"\n\n⚠️ Webhook sync failed for {len(sync_failures)}:\n" + "\n".join(f"• {s}" for s in sync_failures)
    await update.message.reply_text(msg)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = get_session()
    try:
        wallets = session.execute(select(Wallet)).scalars().all()
    finally:
        session.close()

    rated = sorted(
        [w for w in wallets if w.win_rate is not None],
        key=lambda w: w.win_rate, reverse=True,
    )
    if not rated:
        await update.message.reply_text("No win-rate data yet — needs closed trades logged.")
        return

    lines = [f"{i+1}. {w.label or w.address[:6]} — {w.win_rate}%" for i, w in enumerate(rated[:10])]
    await update.message.reply_text("*Wallet leaderboard*\n" + "\n".join(lines), parse_mode="Markdown")


async def wallet_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    logger.info(f"[wallet_history] called with args={args}")
    if not args:
        await update.message.reply_text(
            "Usage: /wallethistory <address> [chain]\n"
            "No chain needed for EVM wallets — pulls trades across Ethereum, Base, "
            "and Robinhood Chain together. Add a chain to see just one."
        )
        return

    try:
        address = args[0]
        explicit_chain = args[1].lower() if len(args) > 1 else None

        chains, normalized = _resolve_target_chains(address, explicit_chain)
        logger.info(f"[wallet_history] resolved chains={chains} normalized={normalized}")
        if chains is None:
            await update.message.reply_text(f"Unknown chain '{explicit_chain}'. Pick from: {VALID_CHAINS}")
            return

        session = get_session()
        try:
            wallet_rows = session.execute(
                select(Wallet).where(Wallet.address == normalized, Wallet.chain.in_(chains))
            ).scalars().all()
            logger.info(f"[wallet_history] found {len(wallet_rows)} wallet row(s)")

            if not wallet_rows:
                # Not tracked yet — fall back to a live on-chain lookup so you
                # can evaluate a wallet before committing to /addwallet, rather
                # than hitting a dead end.
                if chains == ["solana"]:
                    trades = await fetch_solana_preview(normalized)
                    chain_label = "solana"
                elif explicit_chain:
                    # User asked for one specific EVM chain only
                    trades = await fetch_evm_preview(normalized, chains[0])
                    chain_label = chains[0]
                else:
                    # No chain specified for an EVM address — check all three,
                    # since the same address is equally valid on each and most
                    # memecoin activity happens on Base, not Ethereum.
                    trades = await fetch_evm_preview_all_chains(normalized, chains)
                    chain_label = "EVM (Ethereum/Base/Robinhood)"
                logger.info(f"[wallet_history] not tracked, live preview on {chain_label}: "
                            f"{'unavailable' if trades is None else f'{len(trades)} trade(s)'}")
                reply = format_preview(trades, chain_label, normalized)
                await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)
                return

            stats = get_wallet_stats(session, wallet_rows)
            reply = format_wallet_history(stats)
            logger.info(f"[wallet_history] built reply, {len(reply)} chars")
        finally:
            session.close()

        try:
            await update.message.reply_text(reply, parse_mode="Markdown", disable_web_page_preview=True)
            logger.info("[wallet_history] reply sent successfully (Markdown)")
        except Exception as markdown_error:
            # A bad Markdown entity (stray _ or * in a token symbol, say) would
            # otherwise fail silently from the user's side — fall back to
            # plain text rather than losing the reply entirely.
            logger.warning(f"[wallet_history] Markdown send failed ({markdown_error}), retrying as plain text")
            plain = reply.replace("*", "").replace("`", "")
            await update.message.reply_text(plain)
            logger.info("[wallet_history] reply sent successfully (plain text fallback)")

    except Exception as e:
        # Last-resort net: whatever broke, the user sees the actual error
        # directly in Telegram instead of silence — no Railway log access
        # required to know something failed and roughly why.
        logger.exception(f"[wallet_history] Unhandled error for args={args}")
        await update.message.reply_text(
            f"⚠️ /wallethistory failed: {type(e).__name__}: {e}\n\n"
            "This is the raw error so it's visible without checking logs. "
            "If this looks like a DB or missing-column error, restart the "
            "app once — schema auto-migration runs on every startup."
        )
