import asyncio
import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
import json
from pathlib import Path
import time

import discord
from discord.ext import commands

from src.config.settings import Settings, PollConfig
from src.database.database import Database, initialize_database
from src.services.guild_service import GuildService
from src.services.poll_service import PollService

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Run the Discord Poll Bot')
    parser.add_argument(
        '--config',
        type=str,
        help='Comma-separated list of poll configuration JSON files',
        required=True
    )
    parser.add_argument(
        '--shards',
        type=int,
        default=1,
        help='Number of shards to use (default: 1)'
    )
    return parser.parse_args()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            'discord_bot.log',
            maxBytes=10000000,
            backupCount=5
        ),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

class PollBot(commands.Bot):
    def __init__(self, shard_count=1, shard_ids=None, application_id=None):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        
        super().__init__(
            command_prefix="!",
            intents=intents,
            application_id=application_id,
            shard_count=shard_count,
            shard_ids=shard_ids
        )
        
        # Load settings
        self.settings = Settings()
        
        # Initialize database
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            raise EnvironmentError("DATABASE_URL environment variable is required but not set")
        self.database = initialize_database(database_url)
        self.db = self.database.AsyncSessionLocal
        
        # Store poll configurations
        self.poll_configs = {}
        self._load_poll_configs()

        # Clear all commands on init
        self.tree.clear_commands(guild=None)
        for guild_id in self.poll_configs.keys():
            self.tree.clear_commands(guild=discord.Object(id=guild_id))
        
        # Track rate limited guilds
        self.rate_limited_guilds = {}
    
    def _load_poll_configs(self):
        """Load poll configurations from JSON files."""
        try:
            # Get config files from command line arguments
            args = parse_args()
            config_files = [Path(path.strip()) for path in args.config.split(',') if path.strip()]
            
            for config_file in config_files:
                if not config_file.exists():
                    logger.warning(f"Config file does not exist: {config_file}")
                    continue
                    
                with open(config_file) as f:
                    config = json.load(f)
                    guild_id = int(config.get("discord_guild_id"))
                    if guild_id not in self.poll_configs:
                        self.poll_configs[guild_id] = []
                    # Convert config to PollConfig object
                    poll_config = PollConfig(
                        poll_type=config['poll_type'],
                        guild_id=guild_id,
                        admin_role_id=int(config['discord_admin_role_id']),
                        dashboard_command=config['dashboard_command']
                    )
                    self.poll_configs[guild_id].append(poll_config)
                    logger.info(f"Loaded poll config from {config_file}: {config}")
        except Exception as e:
            logger.error(f"Error loading poll configs: {e}", exc_info=True)
            raise

    async def safe_sync_commands(self, *, force=False, guild=None):
        """Safely sync commands with Discord's API, handling rate limits."""
        try:
            # Log what we're about to do
            target = f"guild {guild.id}" if guild else "globally"
            logger.info(f"Syncing commands {target}")
            
            # Clear commands if force sync is requested
            if force and guild is not None:
                self.tree.clear_commands(guild=guild)
                logger.info(f"Cleared commands for {target}")
            
            # Get commands that will be synced
            commands = self.tree.get_commands(guild=guild)
            logger.info(f"About to sync {len(commands)} commands {target}: {[cmd.name for cmd in commands]}")
            
            # Try up to 3 times if we hit rate limits
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    await self.tree.sync(guild=guild)
                    logger.info(f"Successfully synced commands {target} on attempt {attempt}")
                    return
                except discord.HTTPException as e:
                    if e.status == 429 and attempt < max_attempts:  # Rate limit error
                        retry_after = e.retry_after or 5  # Default to 5 seconds if not specified
                        logger.warning(f"Rate limited when syncing commands {target}. Retrying in {retry_after} seconds...")
                        
                        # If we're syncing to a specific guild, add it to the rate-limited list
                        if guild is not None:
                            self.rate_limited_guilds[str(guild.id)] = time.time() + retry_after
                        
                        await asyncio.sleep(retry_after)
                    else:
                        # Other HTTP error or we've reached max attempts
                        logger.error(f"Failed to sync commands {target} after {attempt} attempts: HTTP {e.status} - {e.text}")
                        raise
            
        except Exception as e:
            logger.error(f"Unexpected error syncing commands {target}: {e}", exc_info=True)
            raise

    async def setup_hook(self):
        """Set up the bot's initial state."""
        try:
            logger.info("Starting bot setup...")
            
            # Initialize database
            await self.database.init_db()
            
            # Only clear global commands during setup, leave guild commands to the extensions
            self.tree.clear_commands(guild=None)
            await self.safe_sync_commands()
            logger.info("Initialized command tree")
            
            # Ensure guilds and admin roles are set up
            async with self.db() as session:
                guild_service = GuildService(session)
                
                # Process each guild's configurations
                for guild_id, configs in self.poll_configs.items():
                    # Ensure guild exists
                    guild = await guild_service.get_or_create_guild(guild_id)
                    
                    # Set up admin roles for each poll type
                    for config in configs:
                        await guild_service.set_admin_role(
                            guild_id=guild_id,
                            poll_type=config.poll_type,
                            role_id=config.admin_role_id
                        )
                
                await session.commit()
            
            # List of extensions to load
            extensions = [
                'src.bot.cogs.poll_commands',
                'src.bot.cogs.dashboard_commands',
                'src.bot.cogs.help_commands'
            ]
            
            for extension in extensions:
                try:
                    logger.info(f"Loading extension: {extension}")
                    # Get command tree state before loading
                    global_cmds = self.tree.get_commands()
                    logger.info(f"Command tree before loading {extension}: {[cmd.name for cmd in global_cmds]}")
                    
                    # Load the extension
                    await self.load_extension(extension)
                    
                    # Get command tree state after loading
                    global_cmds = self.tree.get_commands()
                    logger.info(f"Command tree after loading {extension}: {[cmd.name for cmd in global_cmds]}")
                    
                    # Add delay between extension loads
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"Failed to load extension {extension}: {e}")
                    raise
            
            # Schedule a task to periodically retry syncing rate-limited guilds
            self.loop.create_task(self._periodic_guild_sync())
            
            logger.info("Bot setup completed successfully")
            global_cmds = self.tree.get_commands()
            logger.info(f"Final command tree state: {[cmd.name for cmd in global_cmds]}")
            
            # Log configured guilds
            for guild_id, configs in self.poll_configs.items():
                guild_obj = discord.Object(id=int(guild_id))
                guild_cmds = self.tree.get_commands(guild=guild_obj)
                logger.info(f"Commands for guild {guild_id}: {[cmd.name for cmd in guild_cmds]}")
                
        except Exception as e:
            logger.error(f"Error in setup: {e}", exc_info=True)
            raise
            
    async def _periodic_guild_sync(self):
        """Periodically try to sync commands for rate-limited guilds."""
        # Wait for bot to be ready
        await self.wait_until_ready()
        
        # Track rate-limited guilds that need retry
        self.rate_limited_guilds = {}
        
        while not self.is_closed():
            try:
                # Check if there are any guilds that need to be synced
                current_time = time.time()
                guilds_to_sync = []
                
                for guild_id, retry_time in list(self.rate_limited_guilds.items()):
                    if current_time >= retry_time:
                        guilds_to_sync.append(guild_id)
                        del self.rate_limited_guilds[guild_id]
                
                # Sync commands for each guild that needs it
                for guild_id in guilds_to_sync:
                    try:
                        guild = discord.Object(id=int(guild_id))
                        logger.info(f"Retrying command sync for rate-limited guild {guild_id}")
                        await self.tree.sync(guild=guild)
                        logger.info(f"Successfully synced commands for previously rate-limited guild {guild_id}")
                    except discord.HTTPException as e:
                        if e.status == 429:  # Rate limit error
                            retry_after = e.retry_after or 300  # Default to 5 minutes if not specified
                            self.rate_limited_guilds[guild_id] = current_time + retry_after
                            logger.warning(f"Still rate-limited for guild {guild_id}, will retry in {retry_after} seconds")
                        else:
                            logger.error(f"Failed to sync commands for guild {guild_id}: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error syncing commands for guild {guild_id}: {e}")
                
                # Wait before checking again
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error in periodic guild sync: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes on error before retrying

    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info(f"Logged in as {self.user} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        # Run diagnostics on command registration
        self.loop.create_task(self._run_command_diagnostics())
        
        await self.change_presence(activity=discord.Game(name="Collecting votes"))
        
    async def _run_command_diagnostics(self):
        """Diagnose command registration issues."""
        await self.wait_until_ready()
        
        try:
            logger.info("=== COMMAND REGISTRATION DIAGNOSTICS ===")
            
            # Check global commands
            global_cmds = self.tree.get_commands()
            app_commands = await self.tree.fetch_commands()
            
            logger.info(f"LOCAL GLOBAL COMMANDS: {len(global_cmds)}")
            for cmd in global_cmds:
                logger.info(f"  - {cmd.name} (type: {cmd.__class__.__name__})")
            
            logger.info(f"REGISTERED GLOBAL COMMANDS: {len(app_commands)}")
            for cmd in app_commands:
                logger.info(f"  - {cmd.name} (id: {cmd.id})")
            
            # Check guild commands
            for guild_id, configs in self.poll_configs.items():
                try:
                    guild = discord.Object(id=int(guild_id))
                    
                    # Local commands
                    local_guild_cmds = self.tree.get_commands(guild=guild)
                    logger.info(f"LOCAL COMMANDS FOR GUILD {guild_id}: {len(local_guild_cmds)}")
                    for cmd in local_guild_cmds:
                        logger.info(f"  - {cmd.name} (type: {cmd.__class__.__name__})")
                    
                    # Registered commands
                    try:
                        registered_guild_cmds = await self.tree.fetch_commands(guild=guild)
                        logger.info(f"REGISTERED COMMANDS FOR GUILD {guild_id}: {len(registered_guild_cmds)}")
                        for cmd in registered_guild_cmds:
                            logger.info(f"  - {cmd.name} (id: {cmd.id})")
                    except discord.HTTPException as e:
                        logger.error(f"Could not fetch registered commands for guild {guild_id}: {e}")
                        
                except Exception as e:
                    logger.error(f"Error checking commands for guild {guild_id}: {e}")
            
            logger.info("=== END COMMAND REGISTRATION DIAGNOSTICS ===")
            
        except Exception as e:
            logger.error(f"Error in command diagnostics: {e}", exc_info=True)

async def main():
    args = parse_args()
    
    # Load environment variables - direct loading method
    token = None
    application_id = None
    if os.path.exists('.env'):
        try:
            # Read token and application_id directly from file to avoid any caching issues
            import re
            with open('.env', 'r') as f:
                env_content = f.read()
                
            # Extract token
            token_match = re.search(r'DISCORD_TOKEN=([^\n]+)', env_content)
            if token_match:
                token = token_match.group(1)
                logger.info(f"Token loaded directly from file, length: {len(token)}")
                
            # Extract application_id
            app_id_match = re.search(r'DISCORD_APPLICATION_ID=([^\n]+)', env_content)
            if app_id_match:
                application_id = int(app_id_match.group(1))  # Convert to integer
                logger.info(f"Application ID loaded directly from file: {application_id}")
            else:
                # Fallback to dotenv if regex fails
                from dotenv import load_dotenv
                load_dotenv(override=True)
                token = os.getenv('DISCORD_TOKEN')
                application_id = int(os.getenv('DISCORD_APPLICATION_ID'))
                logger.info(f"Token loaded via dotenv, length: {len(token) if token else 0}")
                logger.info(f"Application ID loaded via dotenv: {application_id}")
        except Exception as e:
            logger.error(f"Error loading environment variables: {e}")
            from dotenv import load_dotenv
            load_dotenv(override=True)
            token = os.getenv('DISCORD_TOKEN')
            try:
                application_id = int(os.getenv('DISCORD_APPLICATION_ID'))
            except:
                logger.error("Failed to convert application ID to integer")
                application_id = None
    
    # Determine shard count and create bot
    shard_count = args.shards
    logger.info(f"Starting bot with {shard_count} shard(s)")
    
    # Create and run bot with sharding - pass application_id explicitly
    async with PollBot(shard_count=shard_count, application_id=application_id) as bot:
        try:
            logger.info("Attempting to start bot...")
            await bot.start(token)
        except Exception as e:
            logger.error(f"Error starting bot: {type(e).__name__}: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(main())
