"""
ADHD Task Bot — Main Entry Point
Runs the FastAPI web server and Telegram bot concurrently,
with APScheduler checking for due reminders every minute.
"""

import asyncio
import logging
import os
import signal
import sys

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

from api import app as fastapi_app
from bot import create_bot_app, check_and_send_reminders
import database as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("PORT", os.getenv("WEB_PORT", "8000")))


async def run_scheduler(bot_app):
    """Run APScheduler to send escalating reminders every minute."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_and_send_reminders,
        "interval",
        seconds=60,
        args=[bot_app.bot],
        id="reminder_check",
        name="Check and send escalating reminders",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info("Scheduler started — checking reminders every 60 seconds")
    return scheduler


async def run_web_server():
    """Run the FastAPI web server."""
    config = uvicorn.Config(
        fastapi_app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    logger.info(f"Web dashboard starting at http://{WEB_HOST}:{WEB_PORT}")
    await server.serve()


async def main():
    """Main entry point — runs everything concurrently."""
    logger.info("🧠 ADHD Task Bot starting...")

    # Initialize database
    await db.init_db()
    logger.info("Database initialized")

    # Create Telegram bot app
    bot_app = create_bot_app()
    await bot_app.initialize()
    await bot_app.start()

    # Start polling for Telegram updates
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot polling started")

    # Start the reminder scheduler
    scheduler = await run_scheduler(bot_app)

    # Run web server (this blocks until shutdown)
    try:
        await run_web_server()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()
        logger.info("Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
