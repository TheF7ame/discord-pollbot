import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class HelpCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    @app_commands.command(
        name="help",
        description="Show help information for the bot"
    )
    async def help_command(self, interaction: discord.Interaction):
        """Show help information for the bot."""
        await interaction.response.defer(ephemeral=True)
        
        try:
            guild_id = interaction.guild_id
            
            # Check if there are poll configs for this guild
            has_configs = guild_id in self.bot.poll_configs
            
            # Create embed
            embed = discord.Embed(
                title="Discord Poll Bot Help",
                description="This bot allows creating and managing polls in your server.",
                color=discord.Color.blue()
            )
            
            if has_configs:
                # Get poll types for this guild
                poll_types = [config.poll_type for config in self.bot.poll_configs[guild_id]]
                poll_types_str = ", ".join(f"`{pt}`" for pt in poll_types)
                
                embed.add_field(
                    name="Available Poll Types",
                    value=f"This server has the following poll types: {poll_types_str}",
                    inline=False
                )
                
                embed.add_field(
                    name="Creating Polls",
                    value="Use `/create_<poll_type>` to create a new poll",
                    inline=False
                )
                
                embed.add_field(
                    name="Voting",
                    value="Use `/vote_<poll_type>` to vote on active polls",
                    inline=False
                )
                
                embed.add_field(
                    name="Admin Commands",
                    value="Admins can use:\n"
                          "• `/close_<poll_type>` - Close the current poll\n"
                          "• `/reveal_<poll_type>` - Reveal the answers\n",
                    inline=False
                )
                
                embed.add_field(
                    name="Leaderboard",
                    value="Use `/dashboard_<poll_type>` to see the leaderboard",
                    inline=False
                )
            else:
                embed.add_field(
                    name="No Poll Types Configured",
                    value="This server doesn't have any poll types configured.",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            self.logger.error(f"Error in help command: {e}", exc_info=True)
            await interaction.followup.send(
                "An error occurred while showing help information.",
                ephemeral=True
            )

    @app_commands.command(
        name="reset_commands",
        description="Reset all bot commands in this server (admin only)"
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def reset_commands(self, interaction: discord.Interaction):
        """Reset all bot commands in the current server."""
        guild_id = interaction.guild_id
        
        # Defer response while we work
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Clear all commands for this guild
            guild = discord.Object(id=guild_id)
            self.bot.tree.clear_commands(guild=guild)
            
            # Sync the empty command list to effectively remove all commands
            await self.bot.safe_sync_commands(guild=guild)
            
            # Now re-register only the commands for currently loaded configs
            if guild_id in self.bot.poll_configs:
                self.logger.info(f"Re-registering commands for guild {guild_id} after reset")
                
                # Re-register commands - we need to wait for extension re-registration
                # This will happen automatically on next restart
                
                # Respond with success
                await interaction.followup.send(
                    "All commands have been reset for this server. "
                    "You should restart the bot with the correct configuration to re-register commands.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "All commands have been reset for this server. "
                    "No poll configurations are currently loaded for this server.",
                    ephemeral=True
                )
        except Exception as e:
            self.logger.error(f"Error resetting commands: {e}", exc_info=True)
            await interaction.followup.send(
                f"Error resetting commands: {str(e)}",
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    """Setup function for the help commands cog."""
    await bot.add_cog(HelpCommands(bot))