# 🧠 ADHD Task Bot

A Telegram bot for managing recurring tasks with **escalating reminders** — built specifically for ADHD brains that need gentle-but-persistent nudges.

## Features

- **Escalating reminders** — starts gentle, gets more urgent if you ignore it (5 levels)
- **Recurring tasks** — auto-regenerate on completion (hourly/daily/weekly/monthly)
- **Snooze** — 15m, 30m, 1h, 2h, 8h, or tomorrow from Telegram or dashboard
- **Web dashboard** — dark-mode UI to create, edit, complete, and filter tasks
- **Priority levels** — Low 🟢 / Medium 🟡 / High 🔴 / Urgent 💥
- **Tags** — organize tasks with comma-separated tags

## Escalation Levels

| Level | Timing | Emoji | Tone |
|-------|--------|-------|------|
| 0 | First reminder | 🌱 | Gentle nudge |
| 1 | +30 min | ⏰ | Friendly |
| 2 | +60 min | 🔔 | Direct |
| 3 | +2 hrs | 🚨 | Urgent |
| 4 | +4 hrs | 🔴 | Critical |

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
DATABASE_PATH=tasks.db
WEB_HOST=0.0.0.0
WEB_PORT=8000
```

### 3. Run

```bash
python main.py
```

The bot will:
- Start polling Telegram for commands
- Check for due reminders every 60 seconds
- Serve the dashboard at `http://localhost:8000`

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Show help |
| `/tasks` | List pending tasks |
| `/all` | List all tasks (including completed) |
| `/add <title>` | Quick-add a task (reminds in 5 min) |
| `/done <id>` | Mark a task complete |
| `/snooze <id> [minutes]` | Snooze reminders |
| `/delete <id>` | Delete a task |

## Project Structure

```
.
├── main.py          # Entry point — runs bot + web server + scheduler
├── bot.py           # Telegram bot handlers & escalating reminder logic
├── api.py           # FastAPI REST endpoints
├── database.py      # SQLite async database layer
├── templates/
│   └── dashboard.html  # Web dashboard (single-file SPA)
├── requirements.txt
└── .env
```
