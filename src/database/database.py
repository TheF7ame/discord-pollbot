from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base
from sqlalchemy.ext.asyncio import async_sessionmaker
from contextlib import asynccontextmanager
from typing import AsyncGenerator
import logging
from discord.ext import commands

Base = declarative_base()
logger = logging.getLogger(__name__)

class Database:
    def __init__(self, database_url: str):
        self.engine = create_async_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            future=True
        )
        
        self.AsyncSessionLocal = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False
        )

    async def init_db(self) -> None:
        """Initialize the database, creating all tables."""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

# This will be initialized in main.py with the correct database URL
db: Database = None

def initialize_database(database_url: str):
    """Initialize the database with the given URL."""
    global db
    db = Database(database_url)
    return db

async def get_session() -> AsyncSession:
    """Get a database session using the global database instance."""
    if db is None:
        raise RuntimeError("Database not initialized. Call initialize_database first.")
    return db.AsyncSessionLocal()

async def init_db_pool(bot: commands.Bot):
    """Initialize database pool for the bot."""
    bot.db = db.AsyncSessionLocal
