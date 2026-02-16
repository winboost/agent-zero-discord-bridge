# Agent Zero Discord Bridge

A Discord bot that bridges messages to [Agent Zero](https://github.com/frdel/agent-zero)'s HTTP API, enabling you to chat with your AI agent directly from Discord.

![Python](https://img.shields.io/badge/python-3.10+-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## How It Works

This bot bridges Discord to Agent Zero's HTTP API running inside the Docker container. Here are the key technical details:

**API Endpoint:** The bot sends messages to `POST /api_message` on port 80 (the Agent Zero web UI server, powered by Flask + Socket.IO via uvicorn). This is the dedicated external API endpoint — it does not use the web UI's WebSocket protocol or the tunnel API on port 55520.

**Authentication:** The `/api_message` endpoint requires an API key via the `X-API-KEY` header. The key (`mcp_server_token`) is deterministically generated at runtime from `sha256(runtime_id:username:password)`, truncated to 16 characters. The bot auto-discovers this key by importing Agent Zero's settings module at startup — no need to hardcode or manually update it.

**Conversation Continuity:** Agent Zero uses `context_id` to track conversations. The bot maps each Discord channel to a unique `context_id`, so conversations persist across messages within the same channel. Use `!reset` in Discord to start a fresh context.

**Response Format:** The API returns `{"context_id": "...", "response": "..."}` synchronously — the request blocks until the agent finishes thinking (up to 5 minutes timeout). The bot shows a typing indicator during this wait.

**Runtime Environment:** The bot must run with `/opt/venv/bin/python3` (Agent Zero's virtual environment) so it can import the settings module for API key discovery. The script lives at `/a0/usr/workdir/discord_bridge.py`.

---

## Prerequisites

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)

2. Create an Application, then a Bot, then get a bot token:
   - **2.1.** Grant the required permissions. Most importantly, enable the **Message Content Intent** in the Discord Developer Portal (Bot → Privileged Gateway Intents).
   - **2.2.** Invite the bot to your server using the OAuth2 URL Generator.

   You can utilize the respective section of [this tutorial](https://memu.bot/tutorial/discord).

---

## How to Implement

### 1. Add your Discord bot token

Copy the bot token and add `DISCORD_BOT_TOKEN=your_token_here` to `/a0/usr/.env` (no quotes around the token).

You can do this via the Files browser in the Agent Zero GUI.

### 2. Upload the bot script

Upload `discord_bridge.py` into `/a0/usr/workdir`.

Again, you can utilize the Files browser in Agent Zero.

### 3. Install dependencies

Ensure `aiohttp` is installed (`discord.py` should already be there):

```bash
docker exec agent-zero /opt/venv/bin/pip install aiohttp
```

### 4. Launch the bot

I'd recommend launching with `-it` first to confirm everything starts cleanly:

```bash
docker exec -it agent-zero /opt/venv/bin/python3 /a0/usr/workdir/discord_bridge.py
```

You should see:

```
============================================================
  Agent Zero <-> Discord Bridge
============================================================
  API URL:  http://127.0.0.1:80/api_message
  API Key:  _tyX****
  Timeout:  300s
============================================================
Bot is logged in as YourBotName#1234
```

### 5. Test it

Send a message in Discord and the agent should reply!

### 6. Stop the test instance

Once you've confirmed the bot responds, kill the `-it` instance (closing the terminal doesn't always kill it):

```bash
docker exec agent-zero pkill -f discord_bridge.py
```

If you ever need an instant kill, `-9` sends SIGKILL which can't be caught — the process dies immediately:

```bash
docker exec agent-zero pkill -9 -f discord_bridge.py
```

> **Note:** Wait a minute or two and the bot should go grey/offline on Discord. Discord has a grace period of about 30–60 seconds before it marks a bot as offline.

### 7. Run in background (optional)

> Skip this step if you plan to use auto-start / supervisord (step 8) instead.

Switch to the `-d` approach to run it in background (since `-d` detaches immediately, you won't see the startup banner or any errors):

```bash
docker exec -d agent-zero /opt/venv/bin/python3 /a0/usr/workdir/discord_bridge.py
```

### 8. Auto-start with container (recommended)

If you have a running bot instance (from step 7 or otherwise), kill it first:

```bash
docker exec agent-zero pkill -9 -f discord_bridge.py
```

To make it auto-start with the container, add it to the container's supervisord config (which Agent Zero already uses to manage its processes). Run this command (or simply append the config block to `/etc/supervisor/conf.d/supervisord.conf`):

```bash
docker exec agent-zero bash -c 'cat >> /etc/supervisor/conf.d/supervisord.conf << EOF

[program:discord_bridge]
command=/opt/venv/bin/python3 /a0/usr/workdir/discord_bridge.py
environment=
user=root
directory=/a0
stopwaitsecs=10
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
autorestart=true
startretries=3
stopasgroup=true
killasgroup=true
EOF'
```

> **⚠️ Important:** If you ever run that command twice, you'd get a duplicate `[program:discord_bridge]` block in the file, which would cause supervisor to error. So it should only be run once. You can always verify the file looks correct with:
> ```bash
> docker exec agent-zero cat /etc/supervisor/conf.d/supervisord.conf
> ```

### 9. Reload and start

```bash
docker exec agent-zero supervisorctl reread && docker exec agent-zero supervisorctl update
```

### 10. Verify (optional)

```bash
docker exec agent-zero supervisorctl status discord_bridge
```

From now on the bot will auto-start with the container.

---

## Useful Commands

```bash
# Stop the bot
docker exec agent-zero supervisorctl stop discord_bridge

# Start the bot
docker exec agent-zero supervisorctl start discord_bridge

# Restart the bot
docker exec agent-zero supervisorctl restart discord_bridge

# Status of all services
docker exec agent-zero supervisorctl status

# Kill the bot (any running instance)
docker exec agent-zero pkill -f discord_bridge.py

# Instant kill
docker exec agent-zero pkill -9 -f discord_bridge.py

# Verify it's gone
docker exec agent-zero pgrep -f discord_bridge.py

# Check if the process is alive
docker exec agent-zero ps aux | grep discord_bridge

# View live logs (supervisord-managed)
docker logs agent-zero --tail 50

# Alternative: redirect output to a log file
docker exec -d agent-zero bash -c '/opt/venv/bin/python3 /a0/usr/workdir/discord_bridge.py > /a0/usr/workdir/discord_bridge.log 2>&1'

# Tail the log file
docker exec agent-zero tail -f /a0/usr/workdir/discord_bridge.log
```

## Discord Bot Commands

| Command | Description |
|---------|-------------|
| `!reset` | Start a new conversation (clears context) |
| `!status` | Show connection status |
| `!help` | Show available commands |

---

## Cost

The idle bot is effectively costless:

- **No LLM credits** — the bot only calls Agent Zero's `/api_message` when a Discord message arrives. No messages = no API calls = no tokens used.
- **No cron jobs** — the bot script has no scheduled tasks, polling loops, or periodic pings. It just sits on an open WebSocket to Discord's gateway, waiting for events.
- **Negligible resources** — the idle process uses ~30–50MB of RAM and essentially 0% CPU. The only network activity is Discord's heartbeat ping every ~25 seconds (a few bytes).

You only "pay" (in LLM credits) when someone actually sends a message through Discord.

---

## See Also

- [Agent Zero Telegram Bridge](https://github.com/winboost/agent-zero-telegram-bridge) — Same concept for Telegram

---

## License

MIT
