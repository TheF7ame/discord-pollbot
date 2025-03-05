from typing import List, Dict
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import datetime

from src.database.database import get_session
from src.services.points_service import PointsService
from src.utils.exceptions import PointsError
from src.services.poll_service import PollService
from src.views.dashboard_view import DashboardView

logger = logging.getLogger(__name__)

@app_commands.guild_only()
class DashboardCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._register_task = None

    async def cog_load(self):
        """Register dashboard commands when the cog is loaded."""
        self._register_task = self.bot.loop.create_task(self._register_commands())
        
    async def cog_unload(self):
        # Cancel the registration task if it's still running
        if self._register_task and not self._register_task.done():
            self._register_task.cancel()
            
    async def _register_commands(self):
        """Register all dashboard commands with proper error handling."""
        try:
            logger.info("Registering dashboard commands")
            
            # Process one guild at a time to avoid rate limits
            for guild_id, configs in self.bot.poll_configs.items():
                try:
                    guild = discord.Object(id=int(guild_id))
                    
                    # Log existing commands before we add new ones
                    existing_cmds = self.bot.tree.get_commands(guild=guild)
                    logger.info(f"Guild {guild_id} has {len(existing_cmds)} commands before adding dashboard commands")
                    
                    added = False
                    
                    # Register a dashboard command for each poll type in this guild
                    for config in configs:
                        poll_type = config.poll_type
                        command_name = f"dashboard_{poll_type}"
                        
                        logger.info(f"Registering dashboard command for poll type '{poll_type}' in guild {guild_id}")
                        
                        # Create and register the dashboard command
                        dashboard_cmd = app_commands.Command(
                            name=command_name,
                            description=f"Show the dashboard for {poll_type} polls",
                            callback=self._dashboard_callback,
                            guild_ids=[int(guild_id)],
                        )
                        
                        # Restrict to users with appropriate admin role
                        dashboard_cmd.add_check(self._check_admin_role)
                        
                        # Add the command to the bot's command tree
                        self.bot.tree.add_command(dashboard_cmd, guild=guild)
                        logger.info(f"Added {command_name} command to guild {guild_id}")
                        added = True
                    
                    # Sync commands if we added any
                    if added:
                        # Verify the commands were added to the command tree
                        cmds_to_sync = self.bot.tree.get_commands(guild=guild)
                        logger.info(f"Commands to sync for guild {guild_id}: {[cmd.name for cmd in cmds_to_sync]}")
                        
                        # Sync the commands
                        logger.info(f"Syncing commands for guild {guild_id}")
                        await self.bot.safe_sync_commands(guild=guild)
                        
                        # Add delay between guild syncs
                        await asyncio.sleep(2)
                    else:
                        logger.warning(f"No dashboard commands were added for guild {guild_id}")
                        
                except Exception as e:
                    logger.error(f"Error registering dashboard commands for guild {guild_id}: {e}", exc_info=True)
            
            logger.info("Dashboard command registration completed")
            
        except Exception as e:
            logger.error(f"Error in dashboard command registration: {e}", exc_info=True)
            
    async def _check_admin_role(self, interaction: discord.Interaction) -> bool:
        """Check if user has admin role for the poll type."""
        # Extract poll type from command name (e.g., 'dashboard_election' -> 'election')
        cmd_parts = interaction.command.name.split('_')
        if len(cmd_parts) < 2:
            return False
            
        poll_type = cmd_parts[1]
        
        # Get the admin role for this poll type
        guild_id = str(interaction.guild_id)
        if guild_id not in self.bot.poll_configs:
            logger.warning(f"No poll configs found for guild {guild_id}")
            return False
            
        # Find config for this poll type
        admin_role_id = None
        for config in self.bot.poll_configs[guild_id]:
            if config.poll_type == poll_type:
                admin_role_id = config.admin_role_id
                break
                
        if not admin_role_id:
            logger.warning(f"No admin role found for poll type {poll_type} in guild {guild_id}")
            return False
            
        # Check if user has the role
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
            
        admin_role = discord.utils.get(member.roles, id=admin_role_id)
        return admin_role is not None
        
    async def _dashboard_callback(self, interaction: discord.Interaction):
        """Generic callback for dashboard commands."""
        # Extract poll type from command name (e.g., 'dashboard_election' -> 'election')
        poll_type = interaction.command.name.split('_')[1]
        logger.info(f"Dashboard callback invoked for poll type: {poll_type}")
        
        # Forward to actual implementation
        await self.dashboard(interaction, poll_type)

    async def dashboard(self, interaction: discord.Interaction, poll_type: str):
        """Show the dashboard/leaderboard for a specific poll type."""
        try:
            logger.info(f"Showing dashboard for poll type: {poll_type} in guild {interaction.guild_id}")
            
            await interaction.response.defer(ephemeral=True)
            
            async with self.bot.db() as session:
                poll_service = PollService(session)
                polls = await poll_service.get_all_polls_by_type(
                    guild_id=interaction.guild_id,
                    poll_type=poll_type
                )
                
                if not polls:
                    await interaction.followup.send(f"No {poll_type} polls found.", ephemeral=True)
                    return
                
                # Create an embed for the dashboard
                embed = discord.Embed(
                    title=f"{poll_type.capitalize()} Dashboard",
                    description=f"Overview of all {poll_type} polls in this server.",
                    color=discord.Color.blue()
                )
                
                # Active polls
                active_polls = [p for p in polls if p.status == "active"]
                if active_polls:
                    active_text = "\n".join([f"• **{p.title}** (ID: {p.id})" for p in active_polls[:5]])
                    if len(active_polls) > 5:
                        active_text += f"\n... and {len(active_polls) - 5} more"
                    embed.add_field(name="Active Polls", value=active_text, inline=False)
                else:
                    embed.add_field(name="Active Polls", value="No active polls", inline=False)
                
                # Closed polls
                closed_polls = [p for p in polls if p.status == "closed"]
                if closed_polls:
                    closed_text = "\n".join([f"• **{p.title}** (ID: {p.id})" for p in closed_polls[:5]])
                    if len(closed_polls) > 5:
                        closed_text += f"\n... and {len(closed_polls) - 5} more"
                    embed.add_field(name="Closed Polls", value=closed_text, inline=False)
                
                # Some stats
                embed.add_field(name="Total Polls", value=str(len(polls)), inline=True)
                embed.add_field(name="Active", value=str(len(active_polls)), inline=True)
                embed.add_field(name="Closed", value=str(len(closed_polls)), inline=True)
                
                # Add timestamp
                embed.timestamp = datetime.datetime.now()
                
                # Create action row with buttons for managing polls
                view = DashboardView(self.bot, poll_type, interaction.user.id)
                
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                
        except Exception as e:
            logger.error(f"Error showing dashboard: {e}", exc_info=True)
            await interaction.followup.send("An error occurred while showing the dashboard.", ephemeral=True)

    def _get_medal(self, rank: int) -> str:
        """Return the appropriate medal emoji for a rank."""
        if rank == 1:
            return "🥇"
        elif rank == 2:
            return "🥈"
        elif rank == 3:
            return "🥉"
        else:
            return ""

async def setup(bot: commands.Bot):
    """Setup function for the dashboard commands cog."""
    await bot.add_cog(DashboardCommands(bot))