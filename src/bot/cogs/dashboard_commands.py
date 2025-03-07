from typing import List, Dict
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio

from src.database.database import get_session
from src.services.points_service import PointsService
from src.utils.exceptions import PointsError

logger = logging.getLogger(__name__)

@app_commands.guild_only()
class DashboardCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)

    async def cog_load(self):
        """Called when the cog is loaded. Register all dashboard commands."""
        try:
            self.logger.info("Registering dashboard commands for each guild and poll type...")
            
            # Process one guild at a time to avoid rate limits
            for guild_id, configs in self.bot.poll_configs.items():
                guild = discord.Object(id=guild_id)
                self.logger.info(f"Processing dashboard commands for guild {guild_id}")
                
                # Register all commands for this guild at once to minimize API calls
                commands_added = False
                
                # Track which dashboard commands are currently registered
                current_dashboard_commands = []
                
                for config in configs:
                    poll_type = config.poll_type
                    command_name = f"dashboard_{poll_type}"
                    self.logger.info(f"Registering dashboard command for poll type: {poll_type}")
                    
                    # Track this command as currently registered
                    current_dashboard_commands.append(command_name)
                    
                    # Create dashboard command with poll_type properly bound
                    dashboard_cmd = self._create_dashboard_command(poll_type)
                    
                    # Add command to the tree
                    self.bot.tree.add_command(dashboard_cmd, guild=guild)
                    commands_added = True
                
                # Log what commands we've registered
                self.logger.info(f"Registered dashboard commands for guild {guild_id}: {current_dashboard_commands}")
                
                # Only sync if we actually added commands
                if commands_added:
                    # Use the safer sync method
                    success = await self.bot.safe_sync_commands(guild=guild)
                    if success:
                        self.logger.info(f"Synced dashboard commands for guild {guild_id}")
                    
                    # Always add a delay between guild syncs regardless of success
                    await asyncio.sleep(5)
            
            self.logger.info("Dashboard commands registration process completed")
            
        except Exception as e:
            self.logger.error(f"Error registering dashboard commands: {e}", exc_info=True)
            raise
            
    def _create_dashboard_command(self, poll_type):
        """Create a dashboard command with the poll_type properly bound."""
        @app_commands.command(
            name=f"dashboard_{poll_type}",
            description=f"Show the {poll_type} leaderboard"
        )
        @app_commands.guild_only()
        async def dashboard(interaction: discord.Interaction):
            # The poll_type is properly bound from the outer function
            await self._show_dashboard(interaction, poll_type)
            
        return dashboard

    async def _show_dashboard(self, interaction: discord.Interaction, poll_type: str):
        """Show the dashboard for a specific poll type."""
        self.logger.info(f"Dashboard command called by {interaction.user} ({interaction.user.id})")
        self.logger.info(f"Guild ID: {interaction.guild_id}, Poll Type: {poll_type}")
        
        # Defer response since we'll be doing database operations
        await interaction.response.defer(ephemeral=True)
        
        try:
            async with self.bot.db() as session:
                from sqlalchemy import select, and_
                from src.database.models import Poll, PollOption, PollStatus, Vote, UserPollSelection
                
                points_service = PointsService(session)
                
                # Get user's personal stats for this poll type
                user_points = await points_service.get_user_poll_type_points(
                    guild_id=interaction.guild_id,
                    poll_type=poll_type,
                    user_id=str(interaction.user.id)
                )
                
                # ----- NEW CODE: Get active poll info -----
                # First check if there's an active poll for this type
                active_poll_stmt = select(Poll).where(
                    and_(
                        Poll.guild_id == interaction.guild_id,
                        Poll.poll_type == poll_type,
                        Poll.is_active == True,  # Explicitly check is_active
                        Poll.is_revealed == False  # Make sure it's not revealed
                    )
                )
                result = await session.execute(active_poll_stmt)
                active_poll = result.scalar_one_or_none()
                
                # If no active poll, check for closed but not revealed polls (users are waiting for results)
                if not active_poll:
                    self.logger.info(f"No active poll found, checking for closed but not revealed polls")
                    closed_not_revealed_stmt = select(Poll).where(
                        and_(
                            Poll.guild_id == interaction.guild_id,
                            Poll.poll_type == poll_type,
                            Poll.is_active == False,  # Closed
                            Poll.is_revealed == False  # Not revealed
                        )
                    ).order_by(Poll.created_at.desc()).limit(1)  # Get the most recent one
                    
                    result = await session.execute(closed_not_revealed_stmt)
                    active_poll = result.scalar_one_or_none()  # Reuse the active_poll variable
                
                # Print debug info to logs
                if active_poll:
                    self.logger.info(f"Found poll for current section: {active_poll.id}, question: {active_poll.question}, is_active: {active_poll.is_active}")
                else:
                    self.logger.info(f"No active or pending polls found for {poll_type}")
                
                # Find the last revealed poll
                last_poll_stmt = select(Poll).where(
                    and_(
                        Poll.guild_id == interaction.guild_id,
                        Poll.poll_type == poll_type,
                        Poll.is_revealed == True,  # Only get revealed polls
                        Poll.id != (active_poll.id if active_poll else -1)  # Exclude active poll
                    )
                ).order_by(Poll.created_at.desc()).limit(1)
                
                result = await session.execute(last_poll_stmt)
                last_poll = result.scalar_one_or_none()
                
                # Build the dashboard message
                message_parts = []
                
                # Format personal stats
                if user_points:
                    personal_stats = (
                        f"**Your Stats ({poll_type})**\n"
                        f"Points: {user_points.points}\n"
                        f"Successful Polls: {user_points.total_correct}\n"
                        f"Rank: {self._get_medal(user_points.rank)} #{user_points.rank}\n\n"
                        f"*Points are earned by selecting correct options (1 point per correct option).\n"
                        f"'Successful Polls' counts how many polls you got at least one correct answer in.*\n\n"
                    )
                else:
                    personal_stats = f"**Your Stats ({poll_type})**\nYou haven't participated in any polls yet.\n\n"
                
                message_parts.append(personal_stats)
                
                # ----- Format active poll info (if exists) -----
                if active_poll:
                    # Get the options for this poll
                    options_stmt = select(PollOption).where(PollOption.poll_id == active_poll.id).order_by(PollOption.index)
                    result = await session.execute(options_stmt)
                    options = {opt.index: opt.text for opt in result.scalars().all()}
                    
                    # Check if user has voted on this poll
                    user_vote_stmt = select(Vote).where(
                        and_(
                            Vote.poll_id == active_poll.id,
                            Vote.user_id == str(interaction.user.id)
                        )
                    )
                    result = await session.execute(user_vote_stmt)
                    user_vote = result.scalar_one_or_none()
                    
                    # If no vote in Vote table, check UserPollSelection table
                    if not user_vote:
                        user_selection_stmt = select(UserPollSelection).where(
                            and_(
                                UserPollSelection.poll_id == active_poll.id,
                                UserPollSelection.user_id == str(interaction.user.id)
                            )
                        )
                        result = await session.execute(user_selection_stmt)
                        user_selection = result.scalar_one_or_none()
                    else:
                        user_selection = None
                    
                    active_poll_info = f"**Current Poll: {active_poll.question}**\n"
                    
                    # Define emoji letters (A-Z)
                    emoji_letters = [chr(ord('🇦') + i) for i in range(26)]
                    
                    if user_vote:
                        # User has voted in Vote table
                        selected_options = []
                        for idx in user_vote.option_ids:
                            # Convert idx to int
                            try:
                                idx_int = int(idx)
                                if idx_int in options:
                                    letter = emoji_letters[idx_int] if idx_int < 26 else f"#{idx_int+1}"
                                    selected_options.append(f"{letter} {options[idx_int]}")
                            except (ValueError, TypeError):
                                continue
                        
                        if selected_options:
                            active_poll_info += "Your selections:\n- " + "\n- ".join(selected_options) + "\n\n"
                        else:
                            active_poll_info += "You have voted, but there was an issue displaying your selections.\n\n"
                    elif user_selection and user_selection.selections:
                        # User has voted in UserPollSelection table
                        selected_options = []
                        for idx in user_selection.selections:
                            # Convert idx to int
                            try:
                                idx_int = int(idx)
                                if idx_int in options:
                                    letter = emoji_letters[idx_int] if idx_int < 26 else f"#{idx_int+1}"
                                    selected_options.append(f"{letter} {options[idx_int]}")
                            except (ValueError, TypeError):
                                continue
                                
                        if selected_options:
                            active_poll_info += "Your selections:\n- " + "\n- ".join(selected_options) + "\n\n"
                        else:
                            active_poll_info += "You have voted, but there was an issue displaying your selections.\n\n"
                    else:
                        # User hasn't voted
                        active_poll_info += "You haven't made a choice for this poll yet.\n\n"
                    
                    # Add status information if poll is not active
                    if not active_poll.is_active:
                        active_poll_info += "*This poll is closed and awaiting results*\n\n"
                        
                    message_parts.append(active_poll_info)
                else:
                    # No active poll
                    message_parts.append(f"**Current Poll: No active or pending polls found for {poll_type}**\n\n")
                
                # ----- Format last poll info (if exists) -----
                if last_poll:
                    # Get the options for this poll
                    options_stmt = select(PollOption).where(PollOption.poll_id == last_poll.id).order_by(PollOption.index)
                    result = await session.execute(options_stmt)
                    options = {opt.index: opt.text for opt in result.scalars().all()}
                    
                    # Check if user has voted on this poll
                    user_vote_stmt = select(Vote).where(
                        and_(
                            Vote.poll_id == last_poll.id,
                            Vote.user_id == str(interaction.user.id)
                        )
                    )
                    result = await session.execute(user_vote_stmt)
                    user_vote = result.scalar_one_or_none()
                    
                    # If no vote in Vote table, check UserPollSelection table
                    if not user_vote:
                        user_selection_stmt = select(UserPollSelection).where(
                            and_(
                                UserPollSelection.poll_id == last_poll.id,
                                UserPollSelection.user_id == str(interaction.user.id)
                            )
                        )
                        result = await session.execute(user_selection_stmt)
                        user_selection = result.scalar_one_or_none()
                    else:
                        user_selection = None
                    
                    last_poll_info = f"**Last Poll: {last_poll.question}**\n"
                    
                    # Define emoji letters (A-Z)
                    emoji_letters = [chr(ord('🇦') + i) for i in range(26)]
                    
                    # If poll is revealed, show correct answers
                    if last_poll.is_revealed:
                        # Get and format correct answers
                        if last_poll.correct_answers:
                            correct_options = []
                            for idx in last_poll.correct_answers:
                                # Convert idx to int if it's a string
                                try:
                                    idx_int = int(idx) if isinstance(idx, str) else idx
                                    if idx_int in options:
                                        letter = emoji_letters[idx_int] if idx_int < 26 else f"#{idx_int+1}"
                                        correct_options.append(f"{letter} {options[idx_int]}")
                                except (ValueError, TypeError):
                                    continue
                                
                            if correct_options:
                                last_poll_info += "Correct answers:\n- " + "\n- ".join(correct_options) + "\n\n"
                            else:
                                last_poll_info += "No valid correct answers were found for this poll.\n\n"
                        else:
                            last_poll_info += "No correct answers were specified for this poll.\n\n"
                        
                        # If user participated, show their selections and points
                        has_participated = False
                        user_selections = []
                        
                        if user_vote:
                            has_participated = True
                            user_selections = user_vote.option_ids
                        elif user_selection and user_selection.selections:
                            has_participated = True
                            user_selections = user_selection.selections
                            
                        if has_participated:
                            # Format user's selections
                            selected_options = []
                            for idx in user_selections:
                                # Convert idx to int
                                try:
                                    idx_int = int(idx) if isinstance(idx, str) else idx
                                    if idx_int in options:
                                        letter = emoji_letters[idx_int] if idx_int < 26 else f"#{idx_int+1}"
                                        selected_options.append(f"{letter} {options[idx_int]}")
                                except (ValueError, TypeError):
                                    continue
                                    
                            if selected_options:
                                last_poll_info += "Your selections:\n- " + "\n- ".join(selected_options) + "\n\n"
                            
                            # Calculate points user got from this poll
                            if last_poll.correct_answers:
                                # Convert to sets of strings for consistent comparison
                                correct_set = set(str(x) for x in last_poll.correct_answers)
                                user_set = set(str(x) for x in user_selections)
                                
                                # Calculate points - number of correct selections
                                points = len(user_set & correct_set)
                                last_poll_info += f"Points earned: {points}\n\n"
                    else:
                        # Poll not revealed yet
                        last_poll_info += "Results not revealed yet.\n\n"
                        
                        # If user participated, show their selections
                        has_participated = False
                        user_selections = []
                        
                        if user_vote:
                            has_participated = True
                            user_selections = user_vote.option_ids
                        elif user_selection and user_selection.selections:
                            has_participated = True
                            user_selections = user_selection.selections
                            
                        if has_participated:
                            # Format user's selections
                            selected_options = []
                            for idx in user_selections:
                                # Convert idx to int
                                try:
                                    idx_int = int(idx) if isinstance(idx, str) else idx
                                    if idx_int in options:
                                        letter = emoji_letters[idx_int] if idx_int < 26 else f"#{idx_int+1}"
                                        selected_options.append(f"{letter} {options[idx_int]}")
                                except (ValueError, TypeError):
                                    continue
                                    
                            if selected_options:
                                last_poll_info += "Your selections:\n- " + "\n- ".join(selected_options) + "\n\n"
                        else:
                            last_poll_info += "You didn't participate in this poll.\n\n"
                    
                    message_parts.append(last_poll_info)
                else:
                    # No last revealed poll
                    message_parts.append(f"**Last Poll: No revealed polls found for {poll_type}**\n\n")
                
                # Get leaderboard for this poll type
                leaderboard = await points_service.get_poll_type_leaderboard(
                    guild_id=interaction.guild_id,
                    poll_type=poll_type,
                    limit=10
                )
                
                # Format leaderboard
                if leaderboard:
                    leaderboard_text = f"**{poll_type.upper()} Leaderboard**\n"
                    
                    for entry in leaderboard:
                        # Handle both object and dictionary access
                        if hasattr(entry, 'user_id'):
                            # It's an object
                            user_id = entry.user_id
                            points = entry.points
                            rank = entry.rank
                            total_correct = getattr(entry, 'total_correct', 0)
                        else:
                            # It's a dictionary
                            user_id = entry['user_id']
                            points = entry['points']
                            rank = entry['rank']
                            total_correct = entry.get('total_correct', 0)
                        
                        # Try to get the user's display name
                        try:
                            user = await self.bot.fetch_user(int(user_id))
                            username = user.display_name
                        except:
                            # Fallback to just showing user ID
                            username = f"User {user_id}"
                            
                        medal = self._get_medal(rank)
                        leaderboard_text += f"{medal} **#{rank}** {username}: {points} points"
                        
                        # Add success rate if available
                        if hasattr(entry, 'total_correct') and entry.total_correct > 0:
                            leaderboard_text += f" ({total_correct} successful polls)"
                            
                        leaderboard_text += "\n"
                    
                    message_parts.append(leaderboard_text)
                else:
                    message_parts.append("No one has earned points yet.")
                
                # Send combined message
                await interaction.followup.send(
                    "\n".join(message_parts),
                    ephemeral=True
                )
                    
        except Exception as e:
            self.logger.error(f"Error in dashboard command: {e}", exc_info=True)
            await interaction.followup.send(
                f"An error occurred: {str(e)}",
                ephemeral=True
            )

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