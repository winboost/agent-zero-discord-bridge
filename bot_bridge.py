"""
Agent Zero <-> Discord Bot Bridge
Bridges Discord messages to Agent Zero's /api_message HTTP API.

Usage:
    docker exec -it agent-zero /opt/venv/bin/python3 /a0/usr/workdir/bot_bridge.py

Requirements (inside container):
    /opt/venv/bin/pip install aiohttp discord.py python-dotenv
"""

import sys
import os
import asyncio
import logging
import traceback

# Insert A0 path so we can import settings to auto-discover the API key
sys.path.insert(0, "/a0")

import aiohttp
import discord
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Load environment from A0's .env file
load_dotenv("/a0/usr/.env")

DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

# Agent Zero API configuration
A0_API_URL = os.getenv("A0_API_URL", "http://127.0.0.1:80/api_message")
A0_TIMEOUT = int(os.getenv("A0_TIMEOUT", "300"))  # seconds (agent can be slow)

# Optional: restrict the bot to specific channel IDs (comma-separated).
# If empty, the bot responds in ALL channels and DMs.
ALLOWED_CHANNELS = os.getenv("DISCORD_CHANNEL_IDS", "")
ALLOWED_CHANNEL_SET = (
    set(ALLOWED_CHANNELS.split(",")) if ALLOWED_CHANNELS.strip() else set()
)

# Bot command prefix for special commands
CMD_PREFIX = os.getenv("BOT_CMD_PREFIX", "!")

# Discord message length limit
DISCORD_MAX_LEN = 2000

# ---------------------------------------------------------------------------
# Auto-discover Agent Zero API key from runtime settings
# ---------------------------------------------------------------------------

def get_a0_api_key() -> str:
    """
    Try to read the mcp_server_token from Agent Zero's settings module.
    Falls back to A0_API_KEY env var if import fails.
    """
    # First check env var
    env_key = os.getenv("A0_API_KEY", "")
    if env_key:
        return env_key

    # Auto-discover from A0 settings
    try:
        from python.helpers.settings import get_settings
        token = get_settings().get("mcp_server_token", "")
        if token:
            return token
    except Exception as e:
        print(f"[WARN] Could not auto-discover API key from A0 settings: {e}")

    return ""


A0_API_KEY = get_a0_api_key()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot_bridge")

# ---------------------------------------------------------------------------
# Discord Client
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# Conversation context mapping: Discord channel ID -> Agent Zero context_id
# This gives each channel its own persistent conversation with the agent.
channel_contexts: dict[str, str] = {}


def split_message(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """Split a long message into chunks that fit Discord's character limit."""
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        # Try to split at a newline
        split_pos = text.rfind("\n", 0, limit)
        if split_pos == -1:
            # Try to split at a space
            split_pos = text.rfind(" ", 0, limit)
        if split_pos == -1:
            # Hard split
            split_pos = limit

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


async def send_to_agent(message_text: str, context_id: str = "") -> dict:
    """
    Send a message to Agent Zero's /api_message endpoint.
    Returns the parsed JSON response dict.
    """
    payload = {
        "message": message_text,
        "context_id": context_id,
    }
    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": A0_API_KEY,
    }

    timeout = aiohttp.ClientTimeout(total=A0_TIMEOUT)

    async with aiohttp.ClientSession() as session:
        async with session.post(
            A0_API_URL, json=payload, headers=headers, timeout=timeout
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Agent Zero returned HTTP {resp.status}: {error_text[:500]}"
                )


# ---------------------------------------------------------------------------
# Event Handlers
# ---------------------------------------------------------------------------


@bot.event
async def on_ready():
    log.info(f"Bot is logged in as {bot.user}")
    log.info(f"A0 API URL: {A0_API_URL}")
    log.info(f"A0 API Key: {A0_API_KEY[:4]}{'*' * (len(A0_API_KEY) - 4) if A0_API_KEY else '(MISSING)'}")
    log.info(f"Timeout: {A0_TIMEOUT}s")
    if ALLOWED_CHANNEL_SET:
        log.info(f"Restricted to channels: {ALLOWED_CHANNEL_SET}")
    else:
        log.info("Responding in ALL channels and DMs")


@bot.event
async def on_message(message: discord.Message):
    # Ignore own messages and other bots
    if message.author == bot.user or message.author.bot:
        return

    # Channel filter
    if ALLOWED_CHANNEL_SET and str(message.channel.id) not in ALLOWED_CHANNEL_SET:
        return

    content = message.content.strip()
    if not content:
        return

    channel_id = str(message.channel.id)

    # ---- Special commands ----
    if content.lower() == f"{CMD_PREFIX}reset":
        channel_contexts.pop(channel_id, None)
        await message.reply("🔄 Conversation reset. Starting fresh.")
        log.info(f"Context reset for channel {channel_id}")
        return

    if content.lower() == f"{CMD_PREFIX}status":
        ctx = channel_contexts.get(channel_id, "(none)")
        await message.reply(
            f"🤖 **Bot Status**\n"
            f"• API: `{A0_API_URL}`\n"
            f"• Context: `{ctx}`\n"
            f"• Timeout: {A0_TIMEOUT}s"
        )
        return

    if content.lower() == f"{CMD_PREFIX}help":
        await message.reply(
            f"🤖 **Agent Zero Bridge**\n"
            f"Send any message to chat with Agent Zero.\n\n"
            f"**Commands:**\n"
            f"• `{CMD_PREFIX}reset` — Start a new conversation\n"
            f"• `{CMD_PREFIX}status` — Show connection status\n"
            f"• `{CMD_PREFIX}help` — Show this message"
        )
        return

    # ---- Forward to Agent Zero ----
    context_id = channel_contexts.get(channel_id, "")

    log.info(f"[{message.author}] → Agent Zero: {content[:100]}{'...' if len(content) > 100 else ''}")

    # Show typing indicator while the agent processes
    try:
        async with message.channel.typing():
            data = await send_to_agent(content, context_id)

            # Store context ID for conversation continuity
            new_context = data.get("context_id", "")
            if new_context:
                channel_contexts[channel_id] = new_context

            reply = data.get("response", "")
            if not reply:
                reply = "(Agent returned an empty response)"

            log.info(f"Agent Zero → [{message.author}]: {reply[:100]}{'...' if len(reply) > 100 else ''}")

            # Send response, splitting if needed
            chunks = split_message(reply)
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await message.reply(chunk)
                else:
                    await message.channel.send(chunk)

    except asyncio.TimeoutError:
        log.warning(f"Timeout waiting for Agent Zero (>{A0_TIMEOUT}s)")
        await message.reply(
            f"⏳ Agent Zero took too long to respond (timeout: {A0_TIMEOUT}s). "
            f"Try again or use `{CMD_PREFIX}reset` to start fresh."
        )

    except aiohttp.ClientConnectorError as e:
        log.error(f"Connection error: {e}")
        await message.reply(
            "🔌 Cannot connect to Agent Zero API. Is the server running?\n"
            f"Target: `{A0_API_URL}`"
        )

    except Exception as e:
        log.error(f"Error: {traceback.format_exc()}")
        await message.reply(f"❌ Error: {str(e)[:500]}")


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not found in /a0/usr/.env")
        sys.exit(1)

    if not A0_API_KEY:
        print("ERROR: Could not determine Agent Zero API key.")
        print("Set A0_API_KEY in /a0/usr/.env or ensure A0 settings are accessible.")
        sys.exit(1)

    print("=" * 60)
    print("  Agent Zero <-> Discord Bridge")
    print("=" * 60)
    print(f"  API URL:  {A0_API_URL}")
    print(f"  API Key:  {A0_API_KEY[:4]}****")
    print(f"  Timeout:  {A0_TIMEOUT}s")
    print("=" * 60)

    bot.run(DISCORD_TOKEN)
