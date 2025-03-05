import discord
from discord import app_commands
from discord.ext import commands
import logging

from src.config.settings import settings

logger = logging.getLogger(__name__)

@app_commands.guild_only()
class DevCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("Initializing DevCommands cog")

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Only allow users with admin role to use these commands."""
        if not isinstance(ctx.author, discord.Member):
            return False
        return settings.DISCORD_ADMIN_ROLE_ID in [role.id for role in ctx.author.roles]

    @commands.command()
    @commands.is_owner()
    async def sync(self, ctx):
        """Sync commands with Discord."""
        try:
            logger.info("Manually syncing commands...")
            synced = await self.bot.tree.sync()
            await ctx.send(f"Synced {len(synced)} commands: {[cmd.name for cmd in synced]}")
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}")
            await ctx.send(f"Failed to sync commands: {e}")

    @app_commands.command(name="check")
    @app_commands.guild_only()
    async def check_commands(self, interaction: discord.Interaction):
        """Check current command registration state."""
        try:
            app_commands = self.bot.tree.get_commands()
            regular_commands = self.bot.commands
            
            status = (
                f"Application Commands: {[cmd.name for cmd in app_commands]}\n"
                f"Regular Commands: {[cmd.name for cmd in regular_commands]}\n"
                f"Total Commands: {len(app_commands) + len(regular_commands)}"
            )
            
            logger.info(f"Command status: {status}")
            await interaction.response.send_message(f"```\n{status}\n```")
        except Exception as e:
            logger.error(f"Error checking commands: {e}", exc_info=True)
            await interaction.response.send_message(f"Error: {e}")

    @app_commands.command(name="sync")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def sync_commands(self, interaction: discord.Interaction):
        """Manually sync commands with Discord."""
        try:
            logger.info("Manually syncing commands...")
            
            # Sync to guild first
            guild = interaction.guild
            self.bot.tree.copy_global_to(guild=guild)
            synced_guild = await self.bot.tree.sync(guild=guild)
            logger.info(f"Synced {len(synced_guild)} commands to guild: {[cmd.name for cmd in synced_guild]}")
            
            # Then sync globally
            synced_global = await self.bot.tree.sync()
            logger.info(f"Synced {len(synced_global)} commands globally: {[cmd.name for cmd in synced_global]}")
            
            await interaction.response.send_message(
                f"Synced {len(synced_guild)} commands to guild and {len(synced_global)} commands globally."
            )
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}", exc_info=True)
            await interaction.response.send_message(f"Failed to sync commands: {e}")

async def setup(bot: commands.Bot):
    """Setup function for the development commands cog."""
    if not hasattr(bot, "dev_mode") or not bot.dev_mode:
        logger.info("Bot not in dev mode, skipping DevCommands cog")
        return
    await bot.add_cog(DevCommands(bot)) 