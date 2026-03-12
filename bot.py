"""Telegram bot handlers and escalating reminder logic for ADHD Task Bot."""

import os
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import anthropic

from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from telegram.error import TelegramError

import database as db

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# Escalation messages — progressively more urgent for ADHD brains
ESCALATION_TEMPLATES = [
    # Level 0 — gentle nudge
    ("🌱", "Hey! Just a nudge:", "You've got something to do."),
    # Level 1 — friendly reminder
    ("⏰", "Reminder:", "Don't forget about this one!"),
    # Level 2 — more direct
    ("🔔", "Still waiting:", "This task is still pending. You got this!"),
    # Level 3 — urgent
    ("🚨", "URGENT:", "This needs your attention NOW. You can do it!"),
    # Level 4 — critical
    ("🔴", "CRITICAL:", "DROP EVERYTHING — this task is overdue! Complete it NOW."),
]

PRIORITY_EMOJI = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
    "urgent": "💥",
}


def build_task_message(task: dict, escalation_level: int) -> str:
    """Build a formatted Telegram message for a task reminder."""
    level = min(escalation_level, len(ESCALATION_TEMPLATES) - 1)
    emoji, header, footer = ESCALATION_TEMPLATES[level]
    priority = task.get("priority", "medium")
    p_emoji = PRIORITY_EMOJI.get(priority, "🟡")

    due = task.get("due_date", "")
    due_str = ""
    if due:
        try:
            due_dt = datetime.fromisoformat(due)
            due_str = f"\n📅 Due: {due_dt.strftime('%b %d, %Y %H:%M')}"
        except ValueError:
            due_str = f"\n📅 Due: {due}"

    recurrence = task.get("recurrence", "none")
    rec_str = f"\n🔁 Recurring: {recurrence}" if recurrence != "none" else ""

    tags = task.get("tags", "")
    tag_str = f"\n🏷️ {tags}" if tags else ""

    msg = (
        f"{emoji} <b>{header}</b>\n\n"
        f"{p_emoji} <b>{task['title']}</b>"
        f"{due_str}{rec_str}{tag_str}\n"
    )
    if task.get("description"):
        msg += f"\n📝 {task['description']}\n"
    msg += f"\n<i>{footer}</i>"
    return msg


def build_task_keyboard(task_id: int, escalation_level: int) -> InlineKeyboardMarkup:
    """Build inline keyboard for a task reminder."""
    buttons = [
        [
            InlineKeyboardButton("✅ Done!", callback_data=f"done:{task_id}"),
            InlineKeyboardButton("😴 Snooze 15m", callback_data=f"snooze15:{task_id}"),
        ],
        [
            InlineKeyboardButton("⏳ Snooze 1h", callback_data=f"snooze60:{task_id}"),
            InlineKeyboardButton("📋 All Tasks", callback_data="list"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


async def send_reminder(bot: Bot, task: dict):
    """Send an escalating reminder for a task."""
    level = task.get("current_escalation_level", 0)
    escalation_minutes = task.get("reminder_escalation_minutes", "0,30,60,120,240")

    try:
        intervals = [int(x) for x in escalation_minutes.split(",")]
    except (ValueError, AttributeError):
        intervals = [0, 30, 60, 120, 240]

    message = build_task_message(task, level)
    keyboard = build_task_keyboard(task["id"], level)

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await db.log_reminder(task["id"], level, message)

        # Advance escalation level for next reminder
        next_level = min(level + 1, len(ESCALATION_TEMPLATES) - 1)
        next_interval = intervals[min(next_level, len(intervals) - 1)]
        next_reminder = (datetime.now() + timedelta(minutes=next_interval)).isoformat()

        await db.update_task(
            task["id"],
            current_escalation_level=next_level,
            last_reminder_sent=datetime.now().isoformat(),
            reminder_start=next_reminder,
        )
        logger.info(f"Sent level-{level} reminder for task {task['id']}: {task['title']}")
    except TelegramError as e:
        logger.error(f"Failed to send reminder for task {task['id']}: {e}")


async def check_and_send_reminders(bot: Bot):
    """Check for due tasks and send escalating reminders."""
    try:
        due_tasks = await db.get_due_tasks()
        for task in due_tasks:
            await send_reminder(bot, task)
    except Exception as e:
        logger.error(f"Error in reminder check: {e}")


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    text = (
        "🧠 <b>ADHD Task Bot</b> is ready!\n\n"
        "I'll send you escalating reminders so nothing slips through the cracks.\n\n"
        "<b>Commands:</b>\n"
        "/tasks — View all pending tasks\n"
        "/add — Add a new task\n"
        "/ask — Add a task using natural language (AI-powered)\n"
        "/done &lt;id&gt; — Complete a task\n"
        "/snooze &lt;id&gt; [minutes] — Snooze a task\n"
        "/delete &lt;id&gt; — Delete a task\n"
        "/help — Show this message\n\n"
        "You can also manage tasks via the web dashboard! 🌐"
    )
    await update.message.reply_text(text, parse_mode="HTML")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await cmd_start(update, context)


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all pending tasks."""
    tasks = await db.get_all_tasks(status="pending")
    if not tasks:
        await update.message.reply_text(
            "🎉 No pending tasks! You're all caught up.\n\n"
            "Use /add to create a new task."
        )
        return

    lines = ["📋 <b>Pending Tasks:</b>\n"]
    for task in tasks:
        p_emoji = PRIORITY_EMOJI.get(task.get("priority", "medium"), "🟡")
        rec = " 🔁" if task.get("recurrence", "none") != "none" else ""
        due = ""
        if task.get("due_date"):
            try:
                due_dt = datetime.fromisoformat(task["due_date"])
                due = f" — {due_dt.strftime('%b %d %H:%M')}"
            except ValueError:
                pass
        lines.append(f"{p_emoji} <b>#{task['id']}</b> {task['title']}{rec}{due}")

    lines.append("\n<i>Tap a task ID with /done &lt;id&gt; to complete it.</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List ALL tasks including completed."""
    tasks = await db.get_all_tasks()
    if not tasks:
        await update.message.reply_text("No tasks found. Use /add to create one.")
        return

    pending = [t for t in tasks if t["status"] == "pending"]
    completed = [t for t in tasks if t["status"] == "completed"]

    lines = [f"📋 <b>All Tasks ({len(tasks)}):</b>\n"]
    if pending:
        lines.append("⏳ <b>Pending:</b>")
        for t in pending:
            p_emoji = PRIORITY_EMOJI.get(t.get("priority", "medium"), "🟡")
            lines.append(f"  {p_emoji} #{t['id']} {t['title']}")
    if completed:
        lines.append("\n✅ <b>Completed:</b>")
        for t in completed[-5:]:  # Show last 5 completed
            lines.append(f"  ✅ #{t['id']} {t['title']}")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick-add a task: /add [title]"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /add <title>\n"
            "Example: /add Take medication\n\n"
            "For full options (recurrence, priority, etc.) use the web dashboard."
        )
        return

    title = " ".join(args)
    # Default: remind in 5 minutes
    reminder_start = (datetime.now() + timedelta(minutes=5)).isoformat()
    task_id = await db.create_task(
        title=title,
        reminder_start=reminder_start,
        due_date=reminder_start,
    )
    await update.message.reply_text(
        f"✅ Task <b>#{task_id}</b> created: {title}\n"
        f"First reminder in 5 minutes!\n\n"
        f"Use the web dashboard to set recurrence and priority.",
        parse_mode="HTML"
    )


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark a task as complete: /done <id>"""
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /done <task_id>\nExample: /done 3")
        return

    task_id = int(args[0])
    task = await db.get_task(task_id)
    if not task:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    new_id = await db.complete_task(task_id)
    msg = f"🎉 Task <b>#{task_id}</b> complete: {task['title']}\n\nGreat job!"
    if new_id:
        msg += f"\n\n🔁 Recurring task regenerated as <b>#{new_id}</b>."
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_snooze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Snooze a task: /snooze <id> [minutes]"""
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text(
            "Usage: /snooze <task_id> [minutes]\nExample: /snooze 3 30"
        )
        return

    task_id = int(args[0])
    minutes = int(args[1]) if len(args) > 1 and args[1].isdigit() else 30
    task = await db.get_task(task_id)
    if not task:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    await db.snooze_task(task_id, minutes)
    await update.message.reply_text(
        f"😴 Task <b>#{task_id}</b> snoozed for {minutes} minutes.\n"
        f"I'll remind you again at {(datetime.now() + timedelta(minutes=minutes)).strftime('%H:%M')}.",
        parse_mode="HTML"
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a task: /delete <id>"""
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /delete <task_id>\nExample: /delete 3")
        return

    task_id = int(args[0])
    task = await db.get_task(task_id)
    if not task:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    await db.delete_task(task_id)
    await update.message.reply_text(f"🗑️ Task <b>#{task_id}</b> deleted: {task['title']}", parse_mode="HTML")


_ASK_SYSTEM_PROMPT = """\
You are a task parser. The user will describe a task in natural language.
Extract the following fields and return ONLY a valid JSON object with these keys:
- "title": short task title (string, required)
- "due_date": ISO 8601 datetime string (e.g. "2024-06-15T10:00:00") or null if not specified
- "recurrence": one of "none", "hourly", "daily", "weekly", "monthly" (default "none")
- "recurrence_interval": integer >= 1, how many units between recurrences (default 1)
- "priority": one of "low", "medium", "high", "urgent" (default "medium")
- "tags": comma-separated tag string or empty string

Today's date and time is {now}. Use it to resolve relative dates like "tomorrow", "next Sunday", etc.
Return ONLY the JSON object, no explanation, no markdown fences."""


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a task from natural language: /ask <description>"""
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /ask <natural language task description>\n"
            "Example: /ask clean the bathrooms every Sunday at 10am high priority"
        )
        return

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        await update.message.reply_text(
            "⚠️ ANTHROPIC_API_KEY is not set. Please add it to your environment."
        )
        return

    user_text = " ".join(args)
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    system_prompt = _ASK_SYSTEM_PROMPT.format(now=now_str)

    await update.message.reply_text("🤔 Parsing your task...", parse_mode="HTML")

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_text}],
        )
        raw = response.content[0].text.strip()
        parsed = json.loads(raw)
    except anthropic.AuthenticationError:
        await update.message.reply_text("⚠️ Invalid ANTHROPIC_API_KEY. Please check your key.")
        return
    except anthropic.APIStatusError as e:
        logger.error(f"/ask API error: {e}")
        await update.message.reply_text(f"⚠️ Claude API error ({e.status_code}). Please try again.")
        return
    except anthropic.APIConnectionError:
        logger.error("/ask connection error")
        await update.message.reply_text("⚠️ Could not reach the Claude API. Check your network.")
        return
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        logger.error(f"/ask JSON parse error: {e}")
        await update.message.reply_text("⚠️ Couldn't parse Claude's response. Please rephrase and try again.")
        return

    title = parsed.get("title", user_text)
    due_date = parsed.get("due_date") or None
    recurrence = parsed.get("recurrence", "none")
    recurrence_interval = int(parsed.get("recurrence_interval") or 1)
    priority = parsed.get("priority", "medium")
    tags = parsed.get("tags", "") or ""

    # Set reminder_start to due_date if given, else 5 minutes from now
    if due_date:
        reminder_start = due_date
    else:
        reminder_start = (datetime.now() + timedelta(minutes=5)).isoformat()

    task_id = await db.create_task(
        title=title,
        recurrence=recurrence,
        recurrence_interval=recurrence_interval,
        due_date=due_date,
        reminder_start=reminder_start,
        priority=priority,
        tags=tags,
    )

    # Build a plain-English confirmation
    p_emoji = PRIORITY_EMOJI.get(priority, "🟡")
    parts = [f"✅ Task <b>#{task_id}</b> created: <b>{title}</b>"]
    if due_date:
        try:
            due_dt = datetime.fromisoformat(due_date)
            parts.append(f"📅 Due: {due_dt.strftime('%A, %b %d at %I:%M %p')}")
        except ValueError:
            parts.append(f"📅 Due: {due_date}")
    if recurrence != "none":
        interval_str = f"every {recurrence_interval} " if recurrence_interval > 1 else "every "
        parts.append(f"🔁 Repeats: {interval_str}{recurrence}")
    parts.append(f"{p_emoji} Priority: {priority}")
    if tags:
        parts.append(f"🏷️ Tags: {tags}")

    await update.message.reply_text("\n".join(parts), parse_mode="HTML")


# ── Callback query handlers ───────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("done:"):
        task_id = int(data.split(":")[1])
        task = await db.get_task(task_id)
        if not task:
            await query.edit_message_text("Task not found or already completed.")
            return
        new_id = await db.complete_task(task_id)
        msg = f"🎉 Done! <b>{task['title']}</b>\n\nAwesome work! 💪"
        if new_id:
            msg += f"\n\n🔁 Recurring task regenerated as <b>#{new_id}</b>."
        await query.edit_message_text(msg, parse_mode="HTML")

    elif data.startswith("snooze"):
        parts = data.split(":")
        minutes = int(parts[0].replace("snooze", ""))
        task_id = int(parts[1])
        task = await db.get_task(task_id)
        if not task:
            await query.edit_message_text("Task not found.")
            return
        await db.snooze_task(task_id, minutes)
        wake_time = (datetime.now() + timedelta(minutes=minutes)).strftime("%H:%M")
        await query.edit_message_text(
            f"😴 Snoozed <b>{task['title']}</b> for {minutes} min.\n"
            f"I'll nudge you again at {wake_time}.",
            parse_mode="HTML"
        )

    elif data == "list":
        tasks = await db.get_all_tasks(status="pending")
        if not tasks:
            await query.edit_message_text("🎉 No pending tasks! You're all caught up.")
            return
        lines = ["📋 <b>Pending Tasks:</b>\n"]
        for task in tasks:
            p_emoji = PRIORITY_EMOJI.get(task.get("priority", "medium"), "🟡")
            lines.append(f"{p_emoji} <b>#{task['id']}</b> {task['title']}")
        await query.edit_message_text("\n".join(lines), parse_mode="HTML")


def create_bot_app() -> Application:
    """Create and configure the Telegram bot application."""
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("snooze", cmd_snooze))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CallbackQueryHandler(handle_callback))

    return app
