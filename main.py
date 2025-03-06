import asyncio
import argparse
import logging
import os
from logging.handlers import RotatingFileHandler
import json
from pathlib import Path
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

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
        """Load poll configurations from JSON files or Cloud Storage."""
        try:
            # Get config files from command line arguments
            args = parse_args()
            
            # Check if we should load from Cloud Storage
            if args.config.lower() == "cloud":
                logger.info("Loading poll configurations from Cloud Storage...")
                from src.config.cloud_storage import load_configs_from_cloud_storage
                cloud_configs = load_configs_from_cloud_storage()
                
                for config in cloud_configs:
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
                    logger.info(f"Loaded poll config from Cloud Storage: {config}")
                return
            
            # Load from local files if not using Cloud Storage
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
    # Parse command line arguments
    args = parse_args()
    
    # Get the token and application ID from environment variables
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.error("DISCORD_TOKEN environment variable is required but not set")
        return
    
    application_id = os.getenv('DISCORD_APPLICATION_ID')
    if not application_id:
        logger.error("DISCORD_APPLICATION_ID environment variable is required but not set")
        return
    
    # Initialize and start the bot
    try:
        # Start HTTP server for Cloud Run health checks
        port = int(os.environ.get("PORT", "8080"))
        start_http_server(port)
        
        # Create and run the bot
        async with PollBot(
            shard_count=args.shards,
            application_id=application_id
        ) as bot:
            logger.info(f"Starting bot with {args.shards} shard(s)")
            await bot.start(token)
    except Exception as e:
        logger.error(f"Error starting bot: {e}", exc_info=True)
        raise

# Simple HTTP server to satisfy Cloud Run requirements
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Discord Poll Bot is running")
    
    def log_message(self, format, *args):
        # Suppress logs for health check requests to avoid log spam
        return

def start_http_server(port):
    """Start HTTP server in a separate thread for Cloud Run health checks"""
    logger.info(f"Starting health check HTTP server on port {port}")
    server = HTTPServer(("", port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True  # So the thread will exit when the main program exits
    thread.start()
    logger.info(f"Health check HTTP server started on port {port}")

if __name__ == "__main__":
    asyncio.run(main())
