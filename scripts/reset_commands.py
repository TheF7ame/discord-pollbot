#!/usr/bin/env python3
"""
Command reset utility for Discord Poll Bot.

This script automatically cleans up commands for all guilds registered in the database.
It fetches all guild IDs from the guilds table and resets commands for each one.

Usage:
  python reset_commands.py [--guild GUILD_ID]

If the --guild parameter is provided, only that specific guild's commands will be reset.
If not provided, all guilds registered in the database will have their commands reset.
"""

import os
import argparse
import asyncio
import discord
from dotenv import load_dotenv
import sqlalchemy
from sqlalchemy import create_engine, text
import logging

logger = logging.getLogger(__name__)

def get_guild_ids_from_db():
    """Fetch all guild IDs from the database using synchronized SQLAlchemy connection."""
    try:
        # Get database URL from environment
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            logger.error("DATABASE_URL not found in environment variables")
            return []
        
        # Convert asyncpg URL to standard PostgreSQL URL if needed
        if '+asyncpg' in database_url:
            logger.info("Converting asyncpg URL to standard PostgreSQL URL")
            database_url = database_url.replace('+asyncpg', '')
        
        # Create database engine with synchronous driver
        engine = create_engine(database_url)
        
        # Query all guild IDs from the guilds table
        with engine.connect() as connection:
            result = connection.execute(text("SELECT guild_id FROM polls_guilds"))
            guild_ids = [str(row[0]) for row in result]
        
        logger.info(f"Found {len(guild_ids)} guilds in the database")
        return guild_ids
    except Exception as e:
        logger.error(f"Error retrieving guild IDs from database: {e}")
        return []

async def reset_commands_for_guild(client, tree, guild_id):
    """Reset commands for a specific guild."""
    try:
        guild = discord.Object(id=int(guild_id))
        print(f"Clearing commands for guild: {guild_id}")
        tree.clear_commands(guild=guild)
        await tree.sync(guild=guild)
        print(f"✅ Successfully reset commands for guild {guild_id}")
        # Add a short delay to avoid rate limits
        await asyncio.sleep(2)
        return True
    except Exception as e:
        print(f"❌ Error resetting commands for guild {guild_id}: {e}")
        return False

async def reset_commands(specific_guild_id=None):
    """Reset commands for a guild or all guilds from database."""
    # Load token from .env
    load_dotenv()
    token = os.getenv('DISCORD_TOKEN')
    application_id = int(os.getenv('DISCORD_APPLICATION_ID'))
    
    if not token or not application_id:
        print("Error: DISCORD_TOKEN and DISCORD_APPLICATION_ID must be set in .env file")
        return
    
    # Set up client with application ID
    client = discord.Client(intents=discord.Intents.default())
    
    # Store app commands
    print("Connecting to Discord...")
    await client.login(token)
    
    # Create tree
    tree = discord.app_commands.CommandTree(client)
    
    try:
        # Reset global commands first
        print("Clearing global commands")
        tree.clear_commands(guild=None)
        await tree.sync()
        print("✅ Successfully reset global commands")
        
        success_count = 0
        failed_count = 0
        
        if specific_guild_id:
            # Reset only the specified guild
            print(f"Resetting commands for specific guild: {specific_guild_id}")
            if await reset_commands_for_guild(client, tree, specific_guild_id):
                success_count += 1
            else:
                failed_count += 1
        else:
            # Reset commands for all guilds in the database - get IDs synchronously 
            print("Fetching all guild IDs from database...")
            guild_ids = get_guild_ids_from_db()  # Using synchronous version
            
            if not guild_ids:
                print("No guilds found in database or unable to retrieve guild IDs")
                return
            
            print(f"Resetting commands for {len(guild_ids)} guilds...")
            for guild_id in guild_ids:
                if await reset_commands_for_guild(client, tree, guild_id):
                    success_count += 1
                else:
                    failed_count += 1
        
        print(f"\nCommand reset summary:")
        print(f"  - Successfully reset commands for {success_count} guild(s)")
        if failed_count > 0:
            print(f"  - Failed to reset commands for {failed_count} guild(s)")
        
    except discord.HTTPException as e:
        print(f"HTTP Error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.close()
        print("Disconnected from Discord")

def main():
    """Parse arguments and run the script."""
    parser = argparse.ArgumentParser(description="Reset Discord bot commands")
    parser.add_argument("--guild", type=str, help="Guild ID to reset commands for (optional)")
    args = parser.parse_args()
    
    specific_guild_id = args.guild
    
    if specific_guild_id:
        print(f"Resetting commands for specified guild: {specific_guild_id}")
    else:
        print("Resetting commands for ALL guilds in database")
    
    asyncio.run(reset_commands(specific_guild_id))
    
if __name__ == "__main__":
    main() 