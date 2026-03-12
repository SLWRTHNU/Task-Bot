"""Database setup and operations for ADHD Task Bot."""

import aiosqlite
import asyncio
from datetime import datetime, timedelta
from typing import Optional
import os

DB_PATH = os.getenv("DATABASE_PATH", "tasks.db")

CREATE_TASKS_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    recurrence TEXT DEFAULT 'none',
    recurrence_interval INTEGER DEFAULT 1,
    due_date TEXT,
    reminder_start TEXT,
    reminder_escalation_minutes TEXT DEFAULT '0,30,60,120,240',
    current_escalation_level INTEGER DEFAULT 0,
    last_reminder_sent TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    snoozed_until TEXT,
    priority TEXT DEFAULT 'medium',
    tags TEXT DEFAULT ''
);
"""

CREATE_REMINDER_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS reminder_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    sent_at TEXT DEFAULT (datetime('now')),
    escalation_level INTEGER DEFAULT 0,
    message TEXT,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);
"""


async def init_db():
    """Initialize the database and create tables."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(CREATE_TASKS_TABLE)
        await db.execute(CREATE_REMINDER_LOG_TABLE)
        await db.commit()


async def get_all_tasks(status: Optional[str] = None):
    """Fetch all tasks, optionally filtered by status."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cursor = await db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY due_date ASC, priority DESC",
                (status,)
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM tasks ORDER BY status ASC, due_date ASC"
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def get_task(task_id: int):
    """Fetch a single task by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None


async def create_task(
    title: str,
    description: str = "",
    recurrence: str = "none",
    recurrence_interval: int = 1,
    due_date: Optional[str] = None,
    reminder_start: Optional[str] = None,
    escalation_minutes: str = "0,30,60,120,240",
    priority: str = "medium",
    tags: str = "",
):
    """Create a new task and return its ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO tasks
               (title, description, recurrence, recurrence_interval, due_date,
                reminder_start, reminder_escalation_minutes, priority, tags, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (title, description, recurrence, recurrence_interval, due_date,
             reminder_start or due_date, escalation_minutes, priority, tags)
        )
        await db.commit()
        return cursor.lastrowid


async def update_task(task_id: int, **fields):
    """Update specific fields of a task."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [task_id]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE tasks SET {set_clause} WHERE id = ?", values
        )
        await db.commit()


async def complete_task(task_id: int):
    """Mark a task as complete and regenerate if recurring."""
    task = await get_task(task_id)
    if not task:
        return None

    now = datetime.now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tasks SET status = 'completed', completed_at = ?, current_escalation_level = 0 WHERE id = ?",
            (now.isoformat(), task_id)
        )
        await db.commit()

    # Auto-regenerate if recurring
    new_id = None
    if task["recurrence"] != "none":
        new_due = calculate_next_due(task)
        new_id = await create_task(
            title=task["title"],
            description=task["description"],
            recurrence=task["recurrence"],
            recurrence_interval=task["recurrence_interval"],
            due_date=new_due,
            reminder_start=new_due,
            escalation_minutes=task["reminder_escalation_minutes"],
            priority=task["priority"],
            tags=task["tags"],
        )

    return new_id


async def delete_task(task_id: int):
    """Delete a task and its reminder logs."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM reminder_log WHERE task_id = ?", (task_id,))
        await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        await db.commit()


async def snooze_task(task_id: int, minutes: int = 30):
    """Snooze a task's reminders for N minutes."""
    snoozed_until = (datetime.now() + timedelta(minutes=minutes)).isoformat()
    await update_task(task_id, snoozed_until=snoozed_until, current_escalation_level=0)


async def log_reminder(task_id: int, escalation_level: int, message: str):
    """Log a sent reminder."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reminder_log (task_id, escalation_level, message) VALUES (?, ?, ?)",
            (task_id, escalation_level, message)
        )
        await db.commit()


async def get_due_tasks():
    """Get tasks that are pending and due for a reminder."""
    now = datetime.now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            """SELECT * FROM tasks
               WHERE status = 'pending'
               AND (reminder_start IS NOT NULL AND reminder_start <= ?)
               AND (snoozed_until IS NULL OR snoozed_until <= ?)
               ORDER BY priority DESC, due_date ASC""",
            (now, now)
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


def calculate_next_due(task: dict) -> str:
    """Calculate the next due date based on recurrence settings."""
    base = task.get("due_date") or datetime.now().isoformat()
    try:
        base_dt = datetime.fromisoformat(base)
    except (ValueError, TypeError):
        base_dt = datetime.now()

    interval = task.get("recurrence_interval", 1) or 1
    recurrence = task.get("recurrence", "daily")

    if recurrence == "daily":
        next_dt = base_dt + timedelta(days=interval)
    elif recurrence == "weekly":
        next_dt = base_dt + timedelta(weeks=interval)
    elif recurrence == "monthly":
        # Add ~30 days per month
        next_dt = base_dt + timedelta(days=30 * interval)
    elif recurrence == "hourly":
        next_dt = base_dt + timedelta(hours=interval)
    else:
        next_dt = base_dt + timedelta(days=interval)

    return next_dt.isoformat()
