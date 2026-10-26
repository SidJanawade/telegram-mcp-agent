"""Telegram front-end. Run with:  python -m app.bot"""

from __future__ import annotations

import asyncio
import logging
import signal

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app import config
from app.agent import Agent, default_mcp_provider
from app.config import Settings
from app.memory import Store, make_store

log = logging.getLogger("bot")

TELEGRAM_MAX_CHARS = 4000  # hard limit is 4096; leave headroom


def chunk(text: str, size: int = TELEGRAM_MAX_CHARS) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


def build(settings: Settings, agent: Agent, store: Store) -> Application:
    app = Application.builder().token(settings.telegram_token).build()
    locks: dict[int, asyncio.Lock] = {}

    def allowed(update: Update) -> bool:
        user = update.effective_user
        return user is not None and user.id in settings.allowed_user_ids

    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update) or not update.message:
            return
        await update.message.reply_text(
            "Hi. Ask about your calendar or email, or tell me things to remember.\n"
            "/memory shows what I know about you."
        )

    async def memory_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update) or not update.message or not update.effective_chat:
            return
        facts = await asyncio.to_thread(store.facts, update.effective_chat.id)
        await update.message.reply_text(
            "\n".join(f"{k}: {v}" for k, v in facts.items()) or "Nothing saved yet."
        )

    async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not allowed(update) or not update.message or not update.message.text:
            return
        chat_id = update.effective_chat.id
        # Serialise per chat so two quick messages cannot interleave history.
        async with locks.setdefault(chat_id, asyncio.Lock()):
            await ctx.bot.send_chat_action(chat_id, ChatAction.TYPING)
            try:
                text = await agent.reply(chat_id, update.message.text)
            except Exception:
                log.exception("agent failure in chat %s", chat_id)
                text = "Something broke on my side. Try again in a moment."
            for part in chunk(text):
                await update.message.reply_text(part)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("memory", memory_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    return app


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # keeps bot token out of logs

    settings = config.load()
    settings.validate()

    store = make_store(settings.database_url)
    tools = default_mcp_provider()
    await tools.start()
    agent = Agent(settings, store, tools)
    app = build(settings, agent, store)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        async with app:
            await app.start()
            await app.updater.start_polling()
            log.info("Bot is running")
            await stop.wait()
            log.info("Shutting down")
            await app.updater.stop()
            await app.stop()
    finally:
        await tools.close()
        await asyncio.to_thread(store.close)


if __name__ == "__main__":
    asyncio.run(main())
