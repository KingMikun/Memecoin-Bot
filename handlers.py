"""
The commands you actually type. Kept tight on purpose:
  /start            intro
  /addwallet        track + label a wallet
  /wallets           list what you're tracking
  /label            re-label an existing wallet
  /untrack          drop a wallet
  /stats            quick win-rate leaderboard
  /help             command list
"""
from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from database import Wallet, Subscriber, get_session
from config import CHAINS
from utils.webhook_sync import sync_wallet

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
        "/label `<address>` `<chain>` `<new label>` — rename a tracked wallet\n"
        "/untrack `<address>` `<chain>` — stop tracking\n"
        "/stats — win-rate leaderboard\n",
        parse_mode="Markdown",
    )


async def add_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /addwallet <address> <chain> <label>\n"
            f"Chains: {VALID_CHAINS}"
        )
        return

    address, chain, *label_parts = args
    chain = chain.lower()
    label = " ".join(label_parts)

    if chain not in CHAINS:
        await update.message.reply_text(f"Unknown chain '{chain}'. Pick from: {VALID_CHAINS}")
        return

    normalized_address = address.lower() if CHAINS[chain]["kind"] == "evm" else address

    session = get_session()
    try:
        existing = session.execute(
            select(Wallet).where(Wallet.address == normalized_address, Wallet.chain == chain)
        ).scalar_one_or_none()
        if existing:
            await update.message.reply_text("Already tracking that wallet. Use /label to rename it.")
            return

        wallet = Wallet(
            address=normalized_address,
            chain=chain,
            label=label,
            added_by=str(update.effective_user.id),
        )
        session.add(wallet)
        session.commit()
    finally:
        session.close()

    sync_result = await sync_wallet(normalized_address, chain, CHAINS[chain]["kind"], action="add")

    reply = f"Tracking {label or normalized_address} on {CHAINS[chain]['label']} ✅\n"
    if sync_result.ok:
        reply += f"Webhook: {sync_result.message} ✅"
    else:
        reply += f"⚠️ Webhook sync failed: {sync_result.message}\nSaved locally — trades won't flow until this is fixed."

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


async def label_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /label <address> <chain> <new label>\n"
            f"Chains: {VALID_CHAINS}"
        )
        return

    address, chain, *label_parts = args
    chain = chain.lower()
    new_label = " ".join(label_parts)

    if chain not in CHAINS:
        await update.message.reply_text(f"Unknown chain '{chain}'. Pick from: {VALID_CHAINS}")
        return

    normalized = address.lower() if CHAINS[chain]["kind"] == "evm" else address

    session = get_session()
    try:
        wallet = session.execute(
            select(Wallet).where(Wallet.address == normalized, Wallet.chain == chain)
        ).scalar_one_or_none()
        if not wallet:
            await update.message.reply_text("Wallet not found on that chain.")
            return
        wallet.label = new_label
        session.commit()
    finally:
        session.close()

    await update.message.reply_text(f"Relabeled to '{new_label}' ✅")


async def untrack_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /untrack <address> <chain>\n"
            f"Chains: {VALID_CHAINS}"
        )
        return

    address, chain = args[0], args[1].lower()
    if chain not in CHAINS:
        await update.message.reply_text(f"Unknown chain '{chain}'. Pick from: {VALID_CHAINS}")
        return

    normalized = address.lower() if CHAINS[chain]["kind"] == "evm" else address
    kind = CHAINS[chain]["kind"]

    session = get_session()
    try:
        wallet = session.execute(
            select(Wallet).where(Wallet.address == normalized, Wallet.chain == chain)
        ).scalar_one_or_none()
        if not wallet:
            await update.message.reply_text("Wallet not found on that chain.")
            return
        session.delete(wallet)
        session.commit()
    finally:
        session.close()

    sync_result = await sync_wallet(normalized, chain, kind, action="remove")
    reply = "Untracked ✅"
    if not sync_result.ok:
        reply += f"\n⚠️ Webhook cleanup failed: {sync_result.message} (safe to ignore — just means the address stays on the webhook)"
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

            normalized = address.lower() if CHAINS[chain]["kind"] == "evm" else address

            existing = session.execute(
                select(Wallet).where(Wallet.address == normalized, Wallet.chain == chain)
            ).scalar_one_or_none()
            if existing:
                skipped.append(f"{line} (already tracked)")
                continue

            session.add(Wallet(
                address=normalized, chain=chain, label=label,
                added_by=str(update.effective_user.id),
            ))
            added.append(label or normalized)
            to_sync.append((normalized, chain, CHAINS[chain]["kind"]))
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
