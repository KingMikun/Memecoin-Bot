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
        "/label `<address>` `<new label>` — rename a tracked wallet\n"
        "/untrack `<address>` — stop tracking\n"
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

    # NOTE: to go fully live, this is where you'd also PATCH the Helius or
    # Alchemy webhook to add `normalized_address` to the watched-address list.
    # Left as a manual step / TODO — see README "Wiring up webhooks live".

    await update.message.reply_text(
        f"Tracking {label or normalized_address} on {CHAINS[chain]['label']} ✅\n"
        f"Remember to add this address to your Helius/Alchemy webhook so trades flow in."
    )


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
    if len(args) < 2:
        await update.message.reply_text("Usage: /label <address> <new label>")
        return

    address, *label_parts = args
    new_label = " ".join(label_parts)

    session = get_session()
    try:
        wallet = session.execute(
            select(Wallet).where(Wallet.address == address.lower())
        ).scalar_one_or_none()
        if not wallet:
            await update.message.reply_text("Wallet not found.")
            return
        wallet.label = new_label
        session.commit()
    finally:
        session.close()

    await update.message.reply_text(f"Relabeled to '{new_label}' ✅")


async def untrack_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /untrack <address>")
        return

    address = args[0].lower()
    session = get_session()
    try:
        wallet = session.execute(
            select(Wallet).where(Wallet.address == address)
        ).scalar_one_or_none()
        if not wallet:
            await update.message.reply_text("Wallet not found.")
            return
        session.delete(wallet)
        session.commit()
    finally:
        session.close()

    await update.message.reply_text("Untracked ✅")


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
