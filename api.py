"""FastAPI REST endpoints for the ADHD Task Bot web dashboard."""

import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import database as db
from database import local_now

logger = logging.getLogger(__name__)

app = FastAPI(title="ADHD Task Bot", version="1.0.0")

# Mount static files and templates
templates = Jinja2Templates(directory="templates")

# ── Pydantic models ────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    title: str
    description: str = ""
    recurrence: str = "none"
    recurrence_interval: int = 1
    due_date: Optional[str] = None
    reminder_start: Optional[str] = None
    escalation_minutes: str = "0,30,30,60,60"
    priority: str = "medium"
    tags: str = ""


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    recurrence: Optional[str] = None
    recurrence_interval: Optional[int] = None
    due_date: Optional[str] = None
    reminder_start: Optional[str] = None
    escalation_minutes: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[str] = None
    status: Optional[str] = None


class SnoozeRequest(BaseModel):
    minutes: int = 30


# ── Dashboard route ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── Task API routes ────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def list_tasks(status: Optional[str] = None):
    """List all tasks, optionally filtered by status."""
    tasks = await db.get_all_tasks(status=status)
    return {"tasks": tasks, "count": len(tasks)}


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: int):
    """Get a single task by ID."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.post("/api/tasks", status_code=201)
async def create_task(payload: TaskCreate):
    """Create a new task."""
    # Default due date: 1 hour from now if not specified
    if not payload.due_date:
        payload.due_date = (local_now() + timedelta(hours=1)).isoformat()
    if not payload.reminder_start:
        payload.reminder_start = payload.due_date

    task_id = await db.create_task(
        title=payload.title,
        description=payload.description,
        recurrence=payload.recurrence,
        recurrence_interval=payload.recurrence_interval,
        due_date=payload.due_date,
        reminder_start=payload.reminder_start,
        escalation_minutes=payload.escalation_minutes,
        priority=payload.priority,
        tags=payload.tags,
    )
    task = await db.get_task(task_id)
    return {"message": "Task created", "task": task}


@app.put("/api/tasks/{task_id}")
async def update_task(task_id: int, payload: TaskUpdate):
    """Update a task."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if "escalation_minutes" in updates:
        updates["reminder_escalation_minutes"] = updates.pop("escalation_minutes")

    await db.update_task(task_id, **updates)
    return {"message": "Task updated", "task": await db.get_task(task_id)}


@app.post("/api/tasks/{task_id}/complete")
async def complete_task(task_id: int):
    """Mark a task as complete."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    new_id = await db.complete_task(task_id)
    response = {"message": "Task completed", "task_id": task_id}
    if new_id:
        response["regenerated_task_id"] = new_id
        response["message"] += f" — recurring task regenerated as #{new_id}"
    return response


@app.post("/api/tasks/{task_id}/snooze")
async def snooze_task(task_id: int, payload: SnoozeRequest):
    """Snooze a task's reminders."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await db.snooze_task(task_id, payload.minutes)
    return {
        "message": f"Task snoozed for {payload.minutes} minutes",
        "snoozed_until": (local_now() + timedelta(minutes=payload.minutes)).isoformat()
    }


@app.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    """Delete a task."""
    task = await db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await db.delete_task(task_id)
    return {"message": "Task deleted"}


@app.get("/api/stats")
async def get_stats():
    """Get task statistics."""
    all_tasks = await db.get_all_tasks()
    pending = [t for t in all_tasks if t["status"] == "pending"]
    completed = [t for t in all_tasks if t["status"] == "completed"]
    recurring = [t for t in all_tasks if t.get("recurrence", "none") != "none"]

    return {
        "total": len(all_tasks),
        "pending": len(pending),
        "completed": len(completed),
        "recurring": len(recurring),
        "by_priority": {
            "urgent": len([t for t in pending if t.get("priority") == "urgent"]),
            "high": len([t for t in pending if t.get("priority") == "high"]),
            "medium": len([t for t in pending if t.get("priority") == "medium"]),
            "low": len([t for t in pending if t.get("priority") == "low"]),
        }
    }
