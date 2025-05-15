import asyncio
from logging.config import fileConfig
import os
import sys
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
import re
from datetime import datetime
from sqlalchemy import engine_from_config
from dotenv import load_dotenv

# Force reload environment variables
load_dotenv(override=True)

# Add the project root directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.settings import Settings
from src.database.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Set the version table name with the 'polls' prefix
version_table = 'polls_alembic_version'

def get_url():
    """Get the database URL from environment variable."""
    url = os.getenv('DATABASE_URL')
    if not url:
        raise EnvironmentError("DATABASE_URL environment variable is required for migrations")
    return url

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=version_table,
    )

    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table=version_table,
    )

    with context.begin_transaction():
        context.run_migrations()

def process_revision_directives(context, revision, directives):
    if not directives:
        return
        
    migration_script = directives[0]
    # Extract the next revision number
    migration_dir = os.path.dirname(migration_script.path)
    existing_files = [f for f in os.listdir(migration_dir) if f.endswith('.py')]
    
    # Get the highest numbered migration
    revision_numbers = []
    for f in existing_files:
        match = re.match(r'(\d+)_', f)
        if match:
            try:
                num = int(match.group(1))
                revision_numbers.append(num)
            except ValueError:
                continue
    
    next_rev_num = max(revision_numbers) + 1 if revision_numbers else 1
    new_rev_id = f'{next_rev_num:03d}'
    
    # Create the new filename
    new_filename = f"{new_rev_id}_add_state_tracking_tables.py"
    
    # Update the revision ID and file name
    migration_script.rev_id = new_rev_id
    migration_script.path = os.path.join(migration_dir, new_filename)

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Use the sync PostgreSQL driver instead of asyncpg for Alembic
    sync_url = get_url().replace('postgresql+asyncpg://', 'postgresql://')
    
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        url=sync_url
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            process_revision_directives=process_revision_directives,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            version_table=version_table,
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
