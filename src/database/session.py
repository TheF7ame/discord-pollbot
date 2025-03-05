from typing import Optional, Any, Callable
import logging
import asyncio
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class SessionManager:
    """Manages database session lifecycle."""
    def __init__(self, database_url: str, pool_size: int = 20, max_overflow: int = 10):
        self.engine = create_async_engine(
            database_url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=30,
            pool_recycle=1800,  # Recycle connections every 30 minutes
            pool_pre_ping=True  # Enable connection health checks
        )
        self.session_maker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
        self.last_cleanup = datetime.utcnow()
        self.cleanup_interval = timedelta(hours=1)

    async def get_session(self) -> AsyncSession:
        """Get a database session with retry logic."""
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                session = self.session_maker()
                # Verify connection is alive
                await session.execute("SELECT 1")
                return session
            except SQLAlchemyError as e:
                if attempt == max_retries - 1:
                    logger.error(f"Failed to create session after {max_retries} attempts: {e}")
                    raise
                logger.warning(f"Session creation attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(retry_delay * (attempt + 1))
                continue

    async def cleanup_old_sessions(self):
        """Cleanup old sessions and connections."""
        if datetime.utcnow() - self.last_cleanup < self.cleanup_interval:
            return

        try:
            # Dispose engine pool to clean up old connections
            await self.engine.dispose()
            self.last_cleanup = datetime.utcnow()
            logger.info("Successfully cleaned up database sessions")
        except Exception as e:
            logger.error(f"Error during session cleanup: {e}")

    @asynccontextmanager
    async def begin(self):
        """Begin a new transaction."""
        try:
            async with self.session_maker() as session:
                yield session
        except Exception as e:
            logger.error(f"Transaction error: {e}", exc_info=True)
            await session.rollback()
            raise
        finally:
            await session.close()

    async def execute_in_transaction(self, operation: Callable, *args, **kwargs) -> Any:
        """Execute an operation within a transaction."""
        try:
            async with self.session_maker() as session:
                result = await operation(*args, **kwargs)
                return result
        except Exception as e:
            logger.error(f"Transaction error: {e}", exc_info=True)
            await session.rollback()
            raise

class TransactionManager:
    """Manages database transactions with retry logic."""
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = logging.getLogger(__name__)

    async def execute_with_retry(self, operation, max_retries: int = 3) -> Optional[any]:
        """Execute a database operation with retry logic."""
        for attempt in range(max_retries):
            try:
                result = await operation()
                await self.session.commit()
                return result
            except SQLAlchemyError as e:
                await self.session.rollback()
                if attempt == max_retries - 1:
                    self.logger.error(f"Transaction failed after {max_retries} attempts: {e}")
                    raise
                self.logger.warning(f"Transaction attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(1 * (attempt + 1))
                continue
            except Exception as e:
                await self.session.rollback()
                self.logger.error(f"Unexpected error in transaction: {e}")
                raise

    async def __aenter__(self) -> 'TransactionManager':
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def execute_in_transaction(self, operation: Callable, *args, **kwargs) -> Any:
        """Execute an operation in a transaction with automatic retry."""
        try:
            return await self.execute_with_retry(operation, *args, **kwargs)
        except Exception as e:
            self.logger.error(f"Transaction failed: {e}", exc_info=True)
            raise

    @asynccontextmanager
    async def transaction(self):
        """Context manager for transactions with automatic retry."""
        try:
            async with self.session.begin():
                yield self.session
        except Exception as e:
            self.logger.error(f"Transaction error: {e}", exc_info=True)
            await self.session.rollback()
            raise 