"""
Run once to clear ALL bot commands from ALL scopes.
python clear_commands.py
"""
import asyncio
from telegram import (
    Bot,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllChatAdministrators,
)
from config import config

async def main():
    bot = Bot(token=config.TELEGRAM_TOKEN)
    scopes = [
        BotCommandScopeDefault(),
        BotCommandScopeAllPrivateChats(),
        BotCommandScopeAllGroupChats(),
        BotCommandScopeAllChatAdministrators(),
    ]
    for scope in scopes:
        try:
            await bot.delete_my_commands(scope=scope)
            print(f"Cleared: {scope.__class__.__name__}")
        except Exception as e:
            print(f"Failed {scope.__class__.__name__}: {e}")
    print("Done.")

asyncio.run(main())
