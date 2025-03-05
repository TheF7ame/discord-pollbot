import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from src.database.database import engine
from src.database.models import Base

async def init_database():
    """Initialize the database tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    print("Initializing database...")
    asyncio.run(init_database())
    print("Database initialization complete!")
