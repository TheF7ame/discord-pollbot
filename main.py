import asyncio
import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
import json
from pathlib import Path

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
    def __init__(self, config_paths=None, shard_count=1, shard_ids=None, application_id=None):
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
        self._load_poll_configs(config_paths)

        # Clear all commands on init
        self.tree.clear_commands(guild=None)
        for guild_id in self.poll_configs.keys():
            self.tree.clear_commands(guild=discord.Object(id=guild_id))
        
        # Track rate limited guilds
        self.rate_limited_guilds = set()
    
    def _load_poll_configs(self, config_paths=None):
        """Load poll configurations from JSON files."""
        try:
            if config_paths:
                # Load only the specified config files
                logger.info(f"Loading specified config files: {config_paths}")
                for config_path in config_paths:
                    config_file = Path(config_path)
                    if not config_file.exists():
                        logger.warning(f"Config file not found: {config_path}")
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
            else:
                # Fallback to loading all JSON files if no specific config paths provided
                logger.warning("No config paths specified, loading all JSON files from scripts directory")
                config_dir = Path("scripts")
                for config_file in config_dir.glob("*.json"):
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

    async def safe_sync_commands(self, guild=None, attempt=1):
        """Safely sync commands with rate limit handling."""
        max_attempts = 3
        try:
            guild_id = guild.id if guild else "global"
            logger.info(f"Syncing commands for {guild_id} (attempt {attempt}/{max_attempts})")
            
            await self.tree.sync(guild=guild)
            
            if guild:
                logger.info(f"Successfully synced commands for guild {guild_id}")
                # Remove from rate limited set if it was there
                if guild.id in self.rate_limited_guilds:
                    self.rate_limited_guilds.remove(guild.id)
            else:
                logger.info("Successfully synced global commands")
                
            return True
        except discord.HTTPException as e:
            if e.status == 429:  # Rate limit error
                retry_after = e.retry_after
                guild_id = guild.id if guild else "global"
                logger.warning(f"Rate limited when syncing commands for {guild_id}. Retry after: {retry_after}s")
                
                if guild:
                    self.rate_limited_guilds.add(guild.id)
                
                if attempt < max_attempts:
                    # Add some extra buffer to the retry time
                    await asyncio.sleep(retry_after + 5)
                    return await self.safe_sync_commands(guild, attempt + 1)
                else:
                    logger.warning(f"Max attempts reached for {guild_id}, will try again later")
                    # Schedule a retry much later
                    self.loop.create_task(self._retry_sync_much_later(guild))
                    return False
            else:
                logger.error(f"HTTP error syncing commands: {e}")
                return False
        except Exception as e:
            logger.error(f"Error syncing commands: {e}", exc_info=True)
            return False
            
    async def _retry_sync_much_later(self, guild):
        """Retry syncing commands after a long delay."""
        # Wait 10 minutes before retrying
        await asyncio.sleep(600)
        guild_id = guild.id if guild else "global"
        logger.info(f"Attempting delayed sync for {guild_id}")
        await self.safe_sync_commands(guild)

    async def setup_hook(self):
        """Set up the bot's initial state."""
        try:
            logger.info("Starting bot setup...")
            
            # Initialize database
            await self.database.init_db()
            
            # Log initial command state
            logger.info(f"Initial command tree state: {[cmd.name for cmd in self.tree.get_commands()]}")
            
            # Clear all commands again before loading extensions
            self.tree.clear_commands(guild=None)
            for guild_id in self.poll_configs.keys():
                self.tree.clear_commands(guild=discord.Object(id=guild_id))
            
            # Only sync global commands at this point
            await self.safe_sync_commands()
            
            logger.info("Cleared all commands before setup")
            
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
            
            # Load extensions sequentially with delay between each to avoid rate limits
            extensions = [
                "src.bot.cogs.poll_commands",
                "src.bot.cogs.dashboard_commands",
                "src.bot.cogs.help_commands"
            ]
            
            for extension in extensions:
                try:
                    logger.info(f"Loading extension: {extension}")
                    logger.info(f"Command tree before loading {extension}: {[cmd.name for cmd in self.tree.get_commands()]}")
                    await self.load_extension(extension)
                    logger.info(f"Command tree after loading {extension}: {[cmd.name for cmd in self.tree.get_commands()]}")
                    await asyncio.sleep(2)  # Add delay between extension loads
                except Exception as e:
                    logger.error(f"Failed to load extension {extension}: {e}")
                    raise
            
            # Schedule a task to periodically retry syncing rate-limited guilds
            self.loop.create_task(self._periodic_guild_sync())
            
            logger.info("Bot setup completed successfully")
            logger.info(f"Final command tree state: {[cmd.name for cmd in self.tree.get_commands()]}")
        except Exception as e:
            logger.error(f"Error in setup: {e}", exc_info=True)
            raise
            
    async def _periodic_guild_sync(self):
        """Periodically attempt to sync commands for rate-limited guilds."""
        await self.wait_until_ready()
        while not self.is_closed():
            if self.rate_limited_guilds:
                logger.info(f"Attempting to sync {len(self.rate_limited_guilds)} rate-limited guilds")
                guilds_to_retry = list(self.rate_limited_guilds)
                for guild_id in guilds_to_retry:
                    guild = discord.Object(id=guild_id)
                    success = await self.safe_sync_commands(guild=guild)
                    if success:
                        logger.info(f"Successfully synced previously rate-limited guild {guild_id}")
                    # Add significant delay between guild syncs
                    await asyncio.sleep(60)
            # Check every 15 minutes
            await asyncio.sleep(900)

    async def on_ready(self):
        """Called when the bot is ready."""
        logger.info(f"Logged in as {self.user.name}")
        logger.info(f"Bot ID: {self.user.id}")
        logger.info(f"Using {self.shard_count} shard(s)")
        logger.info("Connected to guilds:")
        
        for guild in self.guilds:
            logger.info(f"- {guild.name} (ID: {guild.id})")
            if guild.id in self.poll_configs:
                logger.info(f"  Found {len(self.poll_configs[guild.id])} poll configurations")
            else:
                logger.warning(f"  No poll configurations found for this guild")

async def main():
    args = parse_args()
    
    # Parse the comma-separated list of config files
    config_paths = [path.strip() for path in args.config.split(',')]
    logger.info(f"Using config files: {config_paths}")
    
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
    
    # Create and run bot with sharding - pass application_id and config_paths explicitly
    async with PollBot(config_paths=config_paths, shard_count=shard_count, application_id=application_id) as bot:
        try:
            logger.info("Attempting to start bot...")
            await bot.start(token)
        except Exception as e:
            logger.error(f"Error starting bot: {type(e).__name__}: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(main())
