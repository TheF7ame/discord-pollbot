from typing import List, Optional, Dict
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
from datetime import datetime, timedelta, timezone
import logging
from sqlalchemy import select

from src.config.settings import settings
from src.database.database import get_session
from src.services.poll_service import PollService
from src.services.points_service import PointsService
from src.utils.constants import Messages, CommandNames, PollType
from src.bot.views.poll_view import PollView, PollAdminView
from src.database.models import Poll, PollStatus, PollOption
from src.utils.exceptions import PollError

logger = logging.getLogger(__name__)

class PollCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.logger = logging.getLogger(__name__)
        self.poll_update_tasks = {}
        self._update_active_polls.start()  # Start the background task
        self._check_expired_polls.start()  # Start the task that checks for expired polls

    async def cog_load(self):
        """Called when the cog is loaded. Register all poll type specific commands."""
        self.logger.info("Loading PollCommands cog")
        
        # Make sure the background tasks have started
        if not self._update_active_polls.is_running():
            self._update_active_polls.start()
            self.logger.info("Started _update_active_polls task")
        if not self._check_expired_polls.is_running():
            self._check_expired_polls.start()
            self.logger.info("Started _check_expired_polls task")
        
        # Continue with delayed command registration to avoid blocking startup
        self.bot.loop.create_task(self._register_commands_delayed())
        self.logger.info("Scheduled command registration to run in background")
    
    async def _register_commands_delayed(self):
        """Register commands with a delay to avoid blocking bot startup."""
        try:
            # Wait for the bot to fully initialize before registering commands
            await asyncio.sleep(5)
            self.logger.info("Starting background command registration...")
            
            # Ensure the tree is synced with our commands
            try:
                await self.bot.safe_sync_commands()
                self.logger.info("Command tree synced successfully")
            except Exception as e:
                self.logger.error(f"Error syncing global commands: {e}")
            
            self.logger.info("Registering poll commands for each guild and poll type...")
            
            # Log initial command tree state
            self.logger.info(f"Initial command tree commands: {[cmd.name for cmd in self.bot.tree.get_commands()]}")
            
            # Register commands for each guild and poll type
            for guild_id, configs in self.bot.poll_configs.items():
                try:
                    guild = discord.Object(id=int(guild_id))
                    self.logger.info(f"Processing commands for guild {guild_id}")
                    
                    # Register all commands at once for this guild to minimize API calls
                    commands_registered = False
                    
                    # Track commands registered for this guild
                    registered_commands = []
                    
                    for config in configs:
                        try:
                            poll_type = config.poll_type
                            self.logger.info(f"Registering commands for poll type: {poll_type}")
                            
                            # Create command functions with poll_type properly bound
                            create_poll_cmd = self._create_poll_command(poll_type)
                            close_poll_cmd = self._close_poll_command(poll_type)
                            reveal_poll_cmd = self._reveal_poll_command(poll_type)
                            vote_cmd = self._vote_command(poll_type)
                            
                            # Track command names being registered
                            poll_commands = [
                                f"create_{poll_type}",
                                f"close_{poll_type}",
                                f"reveal_{poll_type}",
                                f"vote_{poll_type}"
                            ]
                            registered_commands.extend(poll_commands)
                            
                            # Add commands to the tree
                            self.bot.tree.add_command(create_poll_cmd, guild=guild)
                            self.bot.tree.add_command(close_poll_cmd, guild=guild)
                            self.bot.tree.add_command(reveal_poll_cmd, guild=guild)
                            self.bot.tree.add_command(vote_cmd, guild=guild)
                            commands_registered = True
                        except Exception as cmd_error:
                            self.logger.error(f"Error registering commands for poll type {poll_type}: {cmd_error}", exc_info=True)
                            # Continue with other poll types even if one fails
                            continue
                    
                    # Log what commands were registered
                    self.logger.info(f"Registered poll commands for guild {guild_id}: {registered_commands}")
                    
                    # Only sync if we actually registered commands
                    if commands_registered:
                        # Use the safe sync method
                        success = await self.bot.safe_sync_commands(guild=guild)
                        if success:
                            self.logger.info(f"Synced commands for guild {guild_id}")
                    
                    # Add a delay between guild syncs regardless of success
                    # Give a much longer delay to avoid rate limits
                    await asyncio.sleep(10)
                    
                except Exception as guild_error:
                    self.logger.error(f"Error processing guild {guild_id}: {guild_error}", exc_info=True)
                    # Continue with other guilds even if one fails
                    continue
            
            self.logger.info("Poll commands registration process completed")
            
        except Exception as e:
            self.logger.error(f"Error registering poll commands: {e}", exc_info=True)

    async def _retry_sync_later(self, guild, delay):
        """Retry syncing commands for a guild after a delay."""
        self.logger.info(f"Scheduling command sync retry for guild {guild.id} in {delay} seconds")
        await asyncio.sleep(delay)
        
        # Use the safe sync method from the bot
        success = await self.bot.safe_sync_commands(guild=guild)
        if success:
            self.logger.info(f"Successfully synced commands for guild {guild.id} after rate limit")
        else:
            self.logger.warning(f"Failed to sync commands for guild {guild.id} after retry - it will be retried later by the periodic sync task")

    def _create_poll_command(self, poll_type):
        """Create a poll creation command with the poll_type properly bound."""
        @app_commands.command(
            name=f"create_{poll_type}",
            description=f"Create a new {poll_type} poll"
        )
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        @app_commands.describe(
            question="The poll question",
            description="Optional description for the poll",
            options="Comma-separated list of options",
            max_selections="Maximum number of options a user can select (default: 1)",
            duration="Duration format: '5d' (days), '24h' (hours), '30m' (minutes). Default: 5 days",
            show_votes_while_active="Whether to show vote counts while the poll is active (default: False)"
        )
        async def create_poll(
            interaction: discord.Interaction,
            question: str,
            options: str,
            description: str = None,
            max_selections: int = 1,
            duration: str = "5d",
            show_votes_while_active: bool = False
        ):
            # The poll_type is properly bound from the outer function
            await self._handle_create_poll(
                interaction,
                poll_type,
                question,
                description,
                options,
                max_selections,
                duration,
                show_votes_while_active
            )
        
        return create_poll
        
    async def _handle_create_poll(
        self, 
        interaction: discord.Interaction, 
        poll_type: str,
        question: str, 
        description: str = None, 
        options: str = None, 
        max_selections: int = 1,
        duration: str = "5d",
        show_votes_while_active: bool = False
    ):
        """
        Handle the create poll command.
        
        Args:
            interaction: The Discord interaction
            poll_type: The type of poll to create
            question: The poll question
            description: Optional description
            options: Comma-separated list of options
            max_selections: Maximum number of options a user can select
            duration: Duration string
            show_votes_while_active: Whether to show vote counts while the poll is active
            
        This method handles all the logic for creating a new poll with the given parameters.
        """
        self.logger.info(f"Create poll command called by {interaction.user} ({interaction.user.id})")
        
        # Defer the response right away to prevent timeout issues
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception as e:
            self.logger.error(f"Error deferring response: {e}", exc_info=True)
        
        try:
            # Validate input
            if not options:
                await interaction.followup.send(
                    "You must provide options for the poll.",
                    ephemeral=True
                )
                return
            
            # Process options
            option_list = [opt.strip() for opt in options.split(',') if opt.strip()]
            if len(option_list) < 2:
                await interaction.followup.send(
                    "You must provide at least 2 options for the poll.",
                    ephemeral=True
                )
                return

            # Ensure max_selections is at least 1 and at most the number of options
            max_selections = max(1, min(max_selections, len(option_list)))
            
            # Check role permissions
            if not await self._check_admin_permission(interaction):
                await interaction.followup.send(
                    "You don't have permission to create polls of this type.",
                    ephemeral=True
                )
                return
            
            async with self.bot.db() as session:
                poll_service = PollService(session)
                
                # Check if there's already a poll of this type in any state - we need to check the full lifecycle
                latest_poll = await poll_service.get_latest_poll_of_type_any_status(
                    guild_id=interaction.guild_id,
                    poll_type=poll_type
                )
                
                if latest_poll:
                    # Check poll lifecycle state and provide appropriate messaging
                    if latest_poll.is_active and not latest_poll.is_revealed:
                        # Poll is active but not revealed - need to close it first
                        await interaction.followup.send(
                            f"There is an active poll of type **{poll_type}**. "
                            f"You must use `/close_{poll_type}` to close it before creating a new one.",
                            ephemeral=True
                        )
                        return
                    elif not latest_poll.is_active and not latest_poll.is_revealed:
                        # Poll is closed but not revealed - need to reveal it first
                        await interaction.followup.send(
                            f"There is a closed poll of type **{poll_type}** that hasn't been revealed yet. "
                            f"You must use `/reveal_{poll_type}` to reveal its answers before creating a new one.",
                            ephemeral=True
                        )
                        return
                    # Otherwise, the poll is complete (closed and revealed), so we can create a new one
                
                # Create the poll
                poll = await poll_service.create_poll(
                    poll_type=poll_type,
                    question=question,
                    description=description,
                    options=option_list,
                    max_selections=max_selections,
                    creator_id=str(interaction.user.id),
                    guild_id=interaction.guild_id,
                    duration=duration,
                    show_votes_while_active=show_votes_while_active
                )
                
                # Save to database
                await session.commit()
                
                # Format the poll announcement
                content = f"**Poll: {poll.question}**\n\n"
                if description:
                    content += f"{description}\n\n"
                
                if max_selections > 1:
                    content += f"Select up to {max_selections} option(s).\n\n"
                
                # Add options with emoji letters (A-Z)
                emoji_letters = [chr(ord('🇦') + i) for i in range(min(26, len(option_list)))]
                for i, option in enumerate(option_list):
                    if i < len(emoji_letters):
                        content += f"{emoji_letters[i]} {option}\n"
                    else:
                        content += f"{i+1}. {option}\n"
                
                # Add voting instructions
                content += f"\nUse `/vote_{poll_type}` to cast your vote!"
                
                # Add duration if provided
                if duration:
                    # Format the end time in both relative and absolute format
                    formatted_relative_time = discord.utils.format_dt(poll.end_time, style="R")
                    formatted_absolute_time = discord.utils.format_dt(poll.end_time, style="F")
                    
                    # Display both formats
                    content += f"\nThis poll will end {formatted_relative_time} ({formatted_absolute_time})"
                
                # Send the poll announcement to the channel
                message = await interaction.channel.send(content)
                
                # Save the message ID and channel ID for later reference
                await poll_service.register_poll_message(
                    poll_id=poll.id,
                    channel_id=int(interaction.channel.id),
                    message_id=int(message.id)
                )
                await session.commit()
                
                # Send confirmation to admin
                await interaction.followup.send(f"Poll created successfully!", ephemeral=True)
                
        except Exception as e:
            self.logger.error(f"Error creating poll: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)
            except Exception as follow_up_error:
                self.logger.error(f"Error sending error message: {follow_up_error}", exc_info=True)
                # Try to send a new message if the followup fails
                try:
                    if interaction.channel:
                        await interaction.channel.send(f"Error creating poll: {str(e)}")
                except Exception:
                    pass

    def _close_poll_command(self, poll_type):
        """Create a close poll command with the poll_type properly bound."""
        @app_commands.command(
            name=f"close_{poll_type}",
            description=f"Close the current {poll_type} poll"
        )
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def close_poll(interaction: discord.Interaction):
            # The poll_type is properly bound from the outer function
            await self._close_poll(interaction, poll_type)
            
        return close_poll
        
    def _reveal_poll_command(self, poll_type):
        """Create a reveal answer command with the poll_type properly bound."""
        @app_commands.command(
            name=f"reveal_{poll_type}",
            description=f"Reveal the correct answers for the current {poll_type} poll"
        )
        @app_commands.guild_only()
        @app_commands.default_permissions(administrator=True)
        async def reveal_poll(interaction: discord.Interaction):
            # The poll_type is properly bound from the outer function
            await self._handle_reveal_poll_ui(interaction, poll_type)
            
        return reveal_poll
        
    def _vote_command(self, poll_type):
        """Create a vote command with the poll_type properly bound."""
        @app_commands.command(
            name=f"vote_{poll_type}",
            description=f"Vote on the active {poll_type} poll"
        )
        @app_commands.guild_only()
        async def vote(interaction: discord.Interaction):
            # The poll_type is properly bound from the outer function
            await self._handle_vote(interaction, poll_type)
            
        return vote
        
    async def _handle_vote(self, interaction: discord.Interaction, poll_type: str):
        """
        Vote on an active poll.
        
        Args:
            interaction: The Discord interaction that triggered this command
            poll_type: The type of poll to vote on
        """
        # Only allow in guilds
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
            
        # Defer response to prevent timeout
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        try:
            async with self.bot.db() as session:
                poll_service = PollService(session)
                
                # Get the active poll for this poll type
                poll = await poll_service.get_active_poll(poll_type)
                
                if not poll:
                    await interaction.followup.send(
                        f"No active {poll_type} poll found.",
                        ephemeral=True
                    )
                    return
                
                # Get user's current selections if any
                user_selections = await poll_service.get_user_selections(poll.id, str(interaction.user.id))
                
                # Extract selected indices from the selections field
                selected_indices = []
                if user_selections:
                    for selection in user_selections:
                        if 'selections' in selection and selection['selections']:
                            if isinstance(selection['selections'], list):
                                selected_indices.extend(selection['selections'])
                            else:
                                selected_indices.append(selection['selections'])
                
                # Create a new ephemeral message with fresh components
                await self._send_voting_interface(interaction, poll, selected_indices)
        
        except Exception as e:
            self.logger.error(f"Error in vote command: {e}", exc_info=True)
            await interaction.followup.send(
                f"An error occurred while processing your vote: {str(e)}",
                ephemeral=True
            )

    async def _send_voting_interface(self, interaction, poll, selected_indices=None):
        """Send ephemeral voting interface with selection buttons and a confirm button."""
        if selected_indices is None:
            selected_indices = []
            
        # Create the view with buttons for each option
        view = discord.ui.View(timeout=180)  # 3 minute timeout
        
        # Add option buttons
        options_list = []
        if hasattr(poll, 'options') and poll.options is not None:
            # If options is loaded as a relationship
            for option in poll.options:
                options_list.append(option.text)
        else:
            # If we need to manually get options (fallback)
            async with self.bot.db() as session:
                stmt = select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.index)
                options_result = await session.execute(stmt)
                options = options_result.scalars().all()
                options_list = [option.text for option in options]
        
        max_selections = poll.max_selections
        
        # Calculate how many rows we need for option buttons (max 5 per row)
        option_buttons_per_row = min(5, len(options_list))
        
        # Create a copy of the selected indices that we'll modify
        current_selections = selected_indices.copy()
        
        # Define emoji letters A through Z for options
        emoji_letters = [chr(ord('🇦') + i) for i in range(min(26, len(options_list)))]
        
        for i, option_text in enumerate(options_list):
            row = i // option_buttons_per_row
            
            # Determine the button style based on whether it's selected
            style = discord.ButtonStyle.success if i in current_selections else discord.ButtonStyle.secondary
            
            # Use emoji letter instead of number
            emoji_label = emoji_letters[i] if i < len(emoji_letters) else f"{i+1}"
            
            # Truncate option text to fit on button
            truncated_text = option_text
            if len(option_text) > 10:
                truncated_text = option_text[:8] + "..."
            
            # Create a button for this option
            button = discord.ui.Button(
                style=style,
                label=truncated_text,  # Show truncated option text instead of just emoji
                custom_id=f"vote_{poll.id}_{i}",
                row=row
            )
            
            # Define the callback for this button
            async def option_callback(interaction, btn=button, option_index=i):
                nonlocal current_selections
                # Toggle this option's selection
                if option_index in current_selections:
                    current_selections.remove(option_index)
                else:
                    # Check if we're at the max selections
                    if len(current_selections) >= max_selections:
                        # Remove the oldest selection if we're at max
                        current_selections.pop(0)
                    current_selections.append(option_index)
                
                # Send an updated interface with the new selections
                await interaction.response.defer()
                await self._send_voting_interface(interaction, poll, current_selections)
                
            button.callback = option_callback
            view.add_item(button)
        
        # Add a confirm button at the bottom
        confirm_button = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="Confirm Vote",
            custom_id=f"confirm_{poll.id}",
            row=len(options_list) // option_buttons_per_row + 1  # Put on the next row after options
        )
        
        # Add a cancel button at the bottom
        cancel_button = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Cancel",
            custom_id=f"cancel_{poll.id}",
            row=len(options_list) // option_buttons_per_row + 1  # Put on the same row as confirm
        )
        
        # Define the callback for the confirm button
        async def confirm_callback(interaction):
            nonlocal current_selections
            # Save the user's selections to the database
            async with self.bot.db() as session:
                poll_service = PollService(session)
                
                try:
                    if not current_selections:
                        await interaction.response.send_message(
                            "You must select at least one option before confirming.",
                            ephemeral=True
                        )
                        return
                    
                    # Save the selections
                    await poll_service.register_vote(
                        poll_id=poll.id,
                        user_id=str(interaction.user.id),
                        option_indices=current_selections
                    )
                    await session.commit()
                    
                    # Confirm to the user
                    # Format the selection confirmation message with emoji letters
                    selected_options = []
                    for idx in current_selections:
                        if idx < len(emoji_letters):
                            option_label = emoji_letters[idx]
                        else:
                            option_label = f"{idx+1}"
                        selected_options.append(f"{option_label}. {options_list[idx]}")
                    
                    confirmation = "\n".join(selected_options)
                    await interaction.response.send_message(
                        f"Vote confirmed for:\n{confirmation}",
                        ephemeral=True
                    )
                    
                except Exception as e:
                    self.logger.error(f"Error saving vote: {e}", exc_info=True)
                    await interaction.response.send_message(
                        f"Failed to save your vote: {str(e)}",
                        ephemeral=True
                    )
        
        confirm_button.callback = confirm_callback
        view.add_item(confirm_button)
        
        # Define the callback for the cancel button
        async def cancel_callback(interaction):
            await interaction.response.send_message(
                "Voting cancelled.",
                ephemeral=True
            )
        
        cancel_button.callback = cancel_callback
        view.add_item(cancel_button)
        
        # Build the message content
        content = f"**Poll: {poll.question}**\n\n"
        
        if current_selections:
            # Use emoji letters for selected options
            selected_texts = []
            for idx in current_selections:
                if idx < len(emoji_letters):
                    option_label = emoji_letters[idx]
                else:
                    option_label = f"{idx+1}"
                selected_texts.append(f"{option_label} {options_list[idx]}")
            content += f"Your current selections: {', '.join([emoji_letters[i] if i < len(emoji_letters) else str(i+1) for i in current_selections])}\n\n"
        
        content += f"Select options (max: {max_selections}):\n"
        for i, option_text in enumerate(options_list):
            # Use emoji letters for options list
            label = emoji_letters[i] if i < len(emoji_letters) else f"[{i+1}]"
            content += f"{label} {option_text}\n"
            
        # Send the ephemeral message with the view
        await interaction.followup.send(content, view=view, ephemeral=True)

    async def _close_poll(self, interaction: discord.Interaction, poll_type: str):
        """Close the current poll."""
        self.logger.info(f"Close poll command called by {interaction.user} ({interaction.user.id})")
        self.logger.info(f"Guild ID: {interaction.guild_id}, Poll Type: {poll_type}")
        
        # Defer response since we'll be doing database operations
        await interaction.response.defer(ephemeral=True)
        
        # Get poll configuration for this guild and poll type
        poll_config = None
        if interaction.guild_id in self.bot.poll_configs:
            for config in self.bot.poll_configs[interaction.guild_id]:
                if config.poll_type == poll_type:
                    poll_config = config
                    break
        
        if not poll_config:
            self.logger.warning(f"No poll configuration for guild {interaction.guild_id} and type {poll_type}")
            await interaction.followup.send(
                f"This guild is not configured for {poll_type} polls.",
                ephemeral=True
            )
            return
        
        if not interaction.user.get_role(poll_config.admin_role_id):
            self.logger.warning(f"User {interaction.user.id} lacks admin role")
            await interaction.followup.send(
                Messages.NOT_ADMIN,
                ephemeral=True
            )
            return

        try:
            async with self.bot.db() as session:
                poll_service = PollService(session)
                
                # First, check for an active poll
                active_poll = await poll_service.get_latest_poll_of_type(
                    interaction.guild_id,
                    poll_type,
                    include_closed=False  # Only get active polls
                )
                
                # If no active poll, check for closed but not revealed poll
                if not active_poll:
                    closed_poll = await poll_service.get_latest_poll_of_type(
                        interaction.guild_id,
                        poll_type,
                        include_closed=True  # Include closed polls
                    )
                    
                    if not closed_poll:
                        self.logger.warning(f"No {poll_type} poll found in this guild.")
                        await interaction.followup.send(
                            f"No {poll_type} poll found in this guild.",
                            ephemeral=True
                        )
                        return
                    
                    # Check if already closed but not revealed
                    if not closed_poll.is_active and not closed_poll.is_revealed:
                        self.logger.info(f"Poll {closed_poll.id} is already closed but not revealed")
                        poll = closed_poll
                        await interaction.followup.send(
                            f"The {poll_type} poll is already closed.\n\nYou can now reveal the answers using the `/reveal_{poll_type}` command.",
                            ephemeral=True
                        )
                        return
                    
                    # Poll is already revealed
                    if closed_poll.is_revealed:
                        self.logger.info(f"Poll {closed_poll.id} is already revealed")
                        await interaction.followup.send(
                            f"The {poll_type} poll is already closed and the answers have been revealed.\n\nYou can create a new poll using the `/create_{poll_type}` command.",
                            ephemeral=True
                        )
                        return
                else:
                    # Close the active poll
                    self.logger.info(f"Closing active poll {active_poll.id}")
                    poll = await poll_service.close_poll(active_poll.id)
                    await session.commit()
                    
                    # Send confirmation to admin
                    await interaction.followup.send(
                        f"The {poll_type} poll has been closed successfully.\n\nYou can now reveal the answers using the `/reveal_{poll_type}` command.",
                        ephemeral=True
                    )
                
        except Exception as e:
            self.logger.error(f"Error closing poll: {e}", exc_info=True)
            await interaction.followup.send(
                f"Failed to close poll: {str(e)}",
                ephemeral=True
            )

    async def _handle_reveal_poll_ui(self, interaction: discord.Interaction, poll_type: str):
        """Handle the answer reveal UI with buttons."""
        self.logger.info(f"Reveal command (with UI) called by {interaction.user} ({interaction.user.id})")
        self.logger.info(f"Guild ID: {interaction.guild_id}, Poll Type: {poll_type}")
        
        # Defer response since we'll be doing database operations
        await interaction.response.defer(ephemeral=True)
        
        # Get poll configuration for this guild and poll type
        poll_config = None
        if interaction.guild_id in self.bot.poll_configs:
            for config in self.bot.poll_configs[interaction.guild_id]:
                if config.poll_type == poll_type:
                    poll_config = config
                    break
        
        if not poll_config:
            self.logger.warning(f"No poll configuration for guild {interaction.guild_id} and type {poll_type}")
            await interaction.followup.send(
                f"This guild is not configured for {poll_type} polls.",
                ephemeral=True
            )
            return
        
        # Check admin permissions
        if not interaction.user.get_role(poll_config.admin_role_id):
            self.logger.warning(f"User {interaction.user.id} lacks admin role")
            await interaction.followup.send(
                Messages.NOT_ADMIN,
                ephemeral=True
            )
            return

        try:
            async with self.bot.db() as session:
                poll_service = PollService(session)
                
                # Get the latest poll of this type, including closed polls
                poll = await poll_service.get_latest_poll_of_type(
                    interaction.guild_id,
                    poll_type,
                    include_closed=True
                )
                
                if not poll:
                    self.logger.warning(f"No {poll_type} poll found")
                    await interaction.followup.send(
                        f"No {poll_type} poll found in this guild.",
                        ephemeral=True
                    )
                    return

                # Poll must be closed (not active) to reveal
                if poll.is_active:
                    self.logger.warning(f"Poll {poll.id} is still active")
                    await interaction.followup.send(
                        f"The current {poll_type} poll is still active. Please close it first using the `/close_{poll_type}` command before revealing answers.",
                        ephemeral=True
                    )
                    return
                    
                # Check if answers already revealed
                if poll.is_revealed:
                    self.logger.warning(f"Poll {poll.id} answers already revealed")
                    await interaction.followup.send(
                        f"The answers for the {poll_type} poll have already been revealed.",
                        ephemeral=True
                    )
                    return
                
                # Get options for the poll - explicitly query options to avoid lazy loading issues
                stmt = select(PollOption).where(PollOption.poll_id == poll.id).order_by(PollOption.index)
                options_result = await session.execute(stmt)
                options = options_result.scalars().all()
                options_list = [option.text for option in options]
                
                # Send the interface for selecting correct answers
                await self._send_reveal_interface(interaction, poll, options_list)
                
        except Exception as e:
            self.logger.error(f"Error preparing reveal interface: {e}", exc_info=True)
            await interaction.followup.send(
                f"Failed to prepare the reveal interface: {str(e)}",
                ephemeral=True
            )

    async def _send_reveal_interface(self, interaction, poll, options_list):
        """Send ephemeral interface for selecting correct answers with buttons."""
        # Create the view with buttons for each option
        view = discord.ui.View(timeout=180)  # 3 minute timeout
        
        # Track current selections
        current_selections = []
        
        # Calculate how many rows we need for option buttons (max 5 per row)
        option_buttons_per_row = min(5, len(options_list))
        
        for i, option_text in enumerate(options_list):
            row = i // option_buttons_per_row
            
            # Use the option text in the button but truncate if too long
            option_display = option_text
            if len(option_display) > 30:  # Discord has limits on button label length
                option_display = option_display[:27] + "..."
                
            # Create a button for this option - show only the option text, no emoji prefix
            button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label=f"{option_display}",
                custom_id=f"reveal_{poll.id}_{i}",
                row=row
            )
            
            # Define the callback for this button
            async def option_callback(interaction, btn=button, option_index=i, option_text=option_text):
                nonlocal current_selections
                
                # Toggle selection
                if option_index in current_selections:
                    current_selections.remove(option_index)
                    btn.style = discord.ButtonStyle.secondary
                else:
                    # Check if we're at max selections
                    if len(current_selections) >= poll.max_selections:
                        await interaction.response.send_message(
                            f"You can only select up to {poll.max_selections} correct option(s).",
                            ephemeral=True
                        )
                        return
                    
                    current_selections.append(option_index)
                    btn.style = discord.ButtonStyle.success
                
                # Sort selections for display consistency
                current_selections.sort()
                
                # Update the poll info with current selections
                emoji_letters = [chr(ord('🇦') + i) for i in range(min(26, len(options_list)))]
                poll_info = f"**Poll: {poll.question}**\n\nSelect the correct answer(s):\nMax selections allowed in this poll: {poll.max_selections}"
                
                if current_selections:
                    poll_info += "\n\nYou've selected:"
                    for idx in current_selections:
                        label = emoji_letters[idx] if idx < len(emoji_letters) else f"{idx+1}"
                        poll_info += f"\n{label} {options_list[idx]}"
                
                # Update the message with the new button states and content
                await interaction.response.edit_message(content=poll_info, view=view)
                
            button.callback = option_callback
            view.add_item(button)
        
        # Add confirm and cancel buttons at the bottom
        confirm_button = discord.ui.Button(
            style=discord.ButtonStyle.primary,
            label="Confirm Answers",
            custom_id=f"confirm_reveal_{poll.id}",
            row=len(options_list) // option_buttons_per_row + 1
        )
        
        cancel_button = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label="Cancel",
            custom_id=f"cancel_reveal_{poll.id}",
            row=len(options_list) // option_buttons_per_row + 1
        )
        
        # Define the callback for the confirm button
        async def confirm_callback(interaction):
            nonlocal current_selections
            
            try:
                if not current_selections:
                    await interaction.response.send_message(
                        "You must select at least one option as a correct answer.",
                        ephemeral=True
                    )
                    return
                
                # Store the indices directly as strings
                # This ensures correct_answers uses the same format as user selections
                correct_indices = [str(idx) for idx in current_selections if idx < len(options_list)]
                
                # For display/logging purposes, we'll show the actual option text
                correct_answers_text = [options_list[idx] for idx in current_selections if idx < len(options_list)]
                
                # Send a confirmation message and give time for processing
                await interaction.response.defer(ephemeral=True)
                
                # Log the process
                self.logger.info(f"Revealing answers for poll {poll.id}: indices {correct_indices}, text: {correct_answers_text}")
                
                # Reveal the answers in the database
                async with self.bot.db() as session:
                    poll_service = PollService(session)
                    points_service = PointsService(session)
                    
                    try:
                        # 1. Update the poll to mark correct answers
                        updated_poll = await poll_service.reveal_poll(poll.id, correct_indices)
                        await session.commit()  # Commit the poll update
                        self.logger.info(f"Successfully marked correct answers for poll {poll.id}")
                        
                        # 2. Calculate points based on the revealed answers
                        points_updates = await points_service.calculate_poll_points(poll.id)
                        if points_updates is None:
                            self.logger.warning(f"calculate_poll_points returned None for poll {poll.id}")
                            points_updates = []
                        else:
                            self.logger.info(f"Calculated points for {len(points_updates)} users")
                        
                        # 3. Get the updated leaderboard
                        leaderboard = await points_service.get_poll_type_leaderboard(
                            guild_id=interaction.guild_id,
                            poll_type=poll.poll_type,
                            limit=10
                        )
                        
                        if not leaderboard:
                            self.logger.warning("Leaderboard is empty after updating points, attempting to force refresh")
                            # Force a refresh and try again
                            await points_service.update_poll_type_leaderboard(
                                guild_id=interaction.guild_id,
                                poll_type=poll.poll_type,
                                user_points={}  # Empty dict to trigger a full refresh
                            )
                            await session.commit()
                            
                            # Fetch again after refresh
                            leaderboard = await points_service.get_poll_type_leaderboard(
                                guild_id=interaction.guild_id,
                                poll_type=poll.poll_type,
                                limit=10
                            )
                            
                        if leaderboard:
                            self.logger.info(f"Successfully fetched leaderboard with {len(leaderboard)} entries")
                        else:
                            self.logger.warning("Leaderboard still empty after refresh")
                            leaderboard = []

                        # 4. Load poll options explicitly to avoid lazy loading in _format_results_message_with_dict
                        # Get options for the poll to prevent lazy loading issues
                        stmt = select(PollOption).where(PollOption.poll_id == updated_poll.id).order_by(PollOption.index)
                        options_result = await session.execute(stmt)
                        updated_poll_options = options_result.scalars().all()
                        
                        # Create a dictionary that includes the poll and its options to avoid lazy loading
                        poll_dict = {
                            'id': updated_poll.id,
                            'question': updated_poll.question,
                            'max_selections': updated_poll.max_selections,
                            'correct_answers': updated_poll.correct_answers,
                            'is_revealed': updated_poll.is_revealed,
                            'is_active': updated_poll.is_active,
                            'poll_type': updated_poll.poll_type,
                            'options': {str(opt.index): opt.text for opt in updated_poll_options}
                        }
                        
                        # Format and send the results message
                        message = self._format_results_message_with_dict(poll_dict, points_updates, leaderboard)
                        await interaction.followup.send(message)
                        
                    except Exception as e:
                        self.logger.error(f"Error in reveal answer process: {e}", exc_info=True)
                        await session.rollback()
                        await interaction.followup.send(f"Error revealing answers: {str(e)}", ephemeral=True)
            
            except Exception as e:
                self.logger.error(f"Error revealing answers: {e}", exc_info=True)
                try:
                    await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)
                except:
                    # If followup fails, try to send a direct message
                    try:
                        await interaction.user.send(f"An error occurred when revealing poll answers: {str(e)}")
                    except:
                        # If all else fails, log the error and move on
                        self.logger.error("Failed to notify user of error")
                        pass
        
        # Define the callback for the cancel button
        async def cancel_callback(interaction):
            await interaction.response.send_message(
                "Answer reveal cancelled.",
                ephemeral=True
            )
        
        confirm_button.callback = confirm_callback
        cancel_button.callback = cancel_callback
        
        view.add_item(confirm_button)
        view.add_item(cancel_button)
        
        # Send the initial message with the buttons
        poll_info = f"**Poll: {poll.question}**\n\nSelect the correct answer(s):\nMax selections allowed in this poll: {poll.max_selections}"
        
        # Add options list with their emojis for reference (outside of buttons)
        emoji_letters = [chr(ord('🇦') + i) for i in range(min(26, len(options_list)))]
        poll_info += "\n\nOptions:"
        for i, option_text in enumerate(options_list):
            label = emoji_letters[i] if i < len(emoji_letters) else f"{i+1}"
            poll_info += f"\n{label} {option_text}"
            
        # Use followup.send instead of response.send_message since the interaction was already deferred
        await interaction.followup.send(poll_info, view=view, ephemeral=True)

    def _format_results_message(
        self,
        poll: Poll,
        points_updates: List[dict],
        leaderboard: List
    ) -> str:
        """Format a message showing poll results, point distributions, and leaderboard."""
        # Get the base poll information
        message = "**Poll Results**\n"
        message += f"Correct answers: "
        
        if not poll.correct_answers:
            message += "None specified"
        else:
            # Get poll options
            if hasattr(poll, 'options') and poll.options:
                # Options are already loaded
                options_by_index = {str(opt.index): opt.text for opt in poll.options}
            else:
                # Load options from database (this should rarely happen)
                try:
                    options_by_index = {}
                    async def get_options():
                        async with self.bot.db() as session:
                            stmt = select(PollOption).where(PollOption.poll_id == poll.id)
                            result = await session.execute(stmt)
                            options = result.scalars().all()
                            return {str(opt.index): opt.text for opt in options}
                    
                    # We have to run this synchronously since we're in a sync method
                    options_by_index = asyncio.run_coroutine_threadsafe(
                        get_options(), 
                        self.bot.loop
                    ).result()
                except Exception as e:
                    self.logger.error(f"Error loading options for poll {poll.id}: {e}")
                    options_by_index = {}

            # Format the correct answers with their text
            correct_answers_text = []
            correct_indices = poll.correct_answers

            # Use emoji letters for display
            emoji_letters = [chr(ord('🇦') + i) for i in range(26)]  # A-Z emojis

            for i, index in enumerate(correct_indices):
                index_str = str(index)
                if index_str in options_by_index:
                    # Get the emoji letter (A, B, C...) based on the option index
                    option_idx = int(index_str) if index_str.isdigit() else 0
                    emoji = emoji_letters[option_idx] if option_idx < len(emoji_letters) else f"#{option_idx+1}"
                    correct_answers_text.append(f"{emoji} {options_by_index[index_str]}")
                else:
                    # Fallback if we can't find the option text
                    correct_answers_text.append(f"Option {index}")
            
            message += ", ".join(correct_answers_text) if correct_answers_text else "None"
        
        # Add statistics section
        message += "\n\n**Statistics**\n"
        
        # Count participants
        participants = len(points_updates)
        message += f"Total participants: {participants}\n\n"
        
        # Group participants by points
        points_distribution = {}
        for update in points_updates:
            points = update.get('poll_points', 0)
            if points in points_distribution:
                points_distribution[points] += 1
            else:
                points_distribution[points] = 1
        
        # Format points distribution
        message += "Points distribution:\n"
        for points, count in sorted(points_distribution.items(), reverse=True):
            message += f"• {count} {'person' if count == 1 else 'people'} scored {points} {'point' if points == 1 else 'points'}\n"
        
        # Display the actual leaderboard
        if leaderboard and len(leaderboard) > 0:
            message += "\n**Leaderboard**\n"
            
            # Add formatting for each leaderboard entry
            for i, entry in enumerate(leaderboard):
                # Get the medal emoji for top 3 positions
                if i == 0:
                    rank_display = "🥇"
                elif i == 1:
                    rank_display = "🥈"
                elif i == 2:
                    rank_display = "🥉"
                else:
                    # Support both dictionary and object access for rank
                    if hasattr(entry, 'get') and callable(entry.get):
                        rank_display = f"#{entry.get('rank', i+1)}"
                    else:
                        rank_display = f"#{getattr(entry, 'rank', i+1)}"
                
                # Format username - support both dictionary and object access
                if hasattr(entry, 'get') and callable(entry.get):
                    user_id = entry.get('user_id')
                    username = entry.get('username', f"User {user_id}")
                    total_points = entry.get('points', 0)
                    total_polls = entry.get('total_polls', 0)
                    successful_polls = entry.get('successful_polls', 0)
                else:
                    user_id = getattr(entry, 'user_id', 'Unknown')
                    username = getattr(entry, 'username', f"User {user_id}")
                    total_points = getattr(entry, 'points', 0)
                    total_polls = getattr(entry, 'total_polls', 0)
                    successful_polls = getattr(entry, 'successful_polls', 0)
                
                # Try to fetch username from bot if possible and not already set
                if username.startswith("User ") and hasattr(self, 'bot'):
                    try:
                        # This will be executed asynchronously later
                        user = self.bot.get_user(int(user_id))
                        if user:
                            username = user.display_name
                    except:
                        pass  # Keep using the default if we can't fetch the user
                
                # Add the leaderboard entry
                message += f"{rank_display} **{username}**: {total_points} points"
                
                # Add success rate if available
                if total_polls > 0:
                    message += f" ({successful_polls}/{total_polls} polls)"
                
                message += "\n"
            
        return message

    def _format_results_message_with_dict(
        self,
        poll_dict: Dict,
        points_updates: List[dict],
        leaderboard: List
    ) -> str:
        """Format a message showing poll results, point distributions, and leaderboard.
        This version takes a dictionary with poll data rather than a Poll object to avoid SQLAlchemy lazy loading.
        """
        # Get the base poll information
        message = "**Poll Results**\n"
        message += f"Correct answers: "
        
        correct_indices = poll_dict.get('correct_answers', [])
        options_by_index = poll_dict.get('options', {})
        
        if not correct_indices:
            message += "None specified"
        else:
            # Format the correct answers with their text
            correct_answers_text = []

            # Use emoji letters for display
            emoji_letters = [chr(ord('🇦') + i) for i in range(26)]  # A-Z emojis

            for index in correct_indices:
                index_str = str(index)
                if index_str in options_by_index:
                    # Get the emoji letter (A, B, C...) based on the option index
                    option_idx = int(index_str) if index_str.isdigit() else 0
                    emoji = emoji_letters[option_idx] if option_idx < len(emoji_letters) else f"#{option_idx+1}"
                    correct_answers_text.append(f"{emoji} {options_by_index[index_str]}")
                else:
                    # Fallback if we can't find the option text
                    correct_answers_text.append(f"Option {index}")
            
            message += ", ".join(correct_answers_text) if correct_answers_text else "None"
        
        # Add statistics section
        message += "\n\n**Statistics**\n"
        
        # Count participants
        participants = len(points_updates)
        message += f"Total participants: {participants}\n\n"
        
        # Group participants by points
        points_distribution = {}
        for update in points_updates:
            points = update.get('poll_points', 0)
            if points in points_distribution:
                points_distribution[points] += 1
            else:
                points_distribution[points] = 1
        
        # Format points distribution
        message += "Points distribution:\n"
        for points, count in sorted(points_distribution.items(), reverse=True):
            message += f"• {count} {'person' if count == 1 else 'people'} scored {points} {'point' if points == 1 else 'points'}\n"
        
        # Display the actual leaderboard
        if leaderboard and len(leaderboard) > 0:
            message += "\n**Leaderboard**\n"
            
            # Add formatting for each leaderboard entry
            for i, entry in enumerate(leaderboard):
                # Get the medal emoji for top 3 positions
                if i == 0:
                    rank_display = "🥇"
                elif i == 1:
                    rank_display = "🥈"
                elif i == 2:
                    rank_display = "🥉"
                else:
                    # Support both dictionary and object access for rank
                    if hasattr(entry, 'get') and callable(entry.get):
                        rank_display = f"#{entry.get('rank', i+1)}"
                    else:
                        rank_display = f"#{getattr(entry, 'rank', i+1)}"
                
                # Format username - support both dictionary and object access
                if hasattr(entry, 'get') and callable(entry.get):
                    user_id = entry.get('user_id')
                    username = entry.get('username', f"User {user_id}")
                    total_points = entry.get('points', 0)
                    total_polls = entry.get('total_polls', 0)
                    successful_polls = entry.get('successful_polls', 0)
                else:
                    user_id = getattr(entry, 'user_id', 'Unknown')
                    username = getattr(entry, 'username', f"User {user_id}")
                    total_points = getattr(entry, 'points', 0)
                    total_polls = getattr(entry, 'total_polls', 0)
                    successful_polls = getattr(entry, 'successful_polls', 0)
                
                # Try to fetch username from bot if possible and not already set
                if username.startswith("User ") and hasattr(self, 'bot'):
                    try:
                        # This will be executed asynchronously later
                        user = self.bot.get_user(int(user_id))
                        if user:
                            username = user.display_name
                    except:
                        pass  # Keep using the default if we can't fetch the user
                
                # Add the leaderboard entry
                message += f"{rank_display} **{username}**: {total_points} points"
                
                # Add success rate if available
                if total_polls > 0:
                    message += f" ({successful_polls}/{total_polls} polls)"
                
                message += "\n"
            
        return message

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        self.logger.error(f"Command error: {error}")

    async def cog_command_error(
        self, 
        interaction: discord.Interaction, 
        error: app_commands.AppCommandError
    ):
        self.logger.error(f"App command error: {error}")

    @tasks.loop(minutes=1)
    async def _update_active_polls(self):
        """Background task to update active poll messages with current vote counts and close expired polls."""
        try:
            # First, get all active polls
            active_polls = []
            async with self.bot.db() as session:
                poll_service = PollService(session)
                active_polls = await poll_service.get_all_active_polls()
            
            if not active_polls:
                return
                
            self.logger.debug(f"Found {len(active_polls)} active polls to update")
            
            # Process each active poll
            for poll in active_polls:
                try:
                    # Check if poll has expired
                    now = datetime.now(timezone.utc)
                    
                    # TZDateTime ensures poll.end_time is already timezone-aware
                    if poll.end_time <= now and poll.is_active:
                        self.logger.info(f"Poll {poll.id} has expired, marking as closed")
                        async with self.bot.db() as session:
                            poll_service = PollService(session)
                            try:
                                await poll_service.close_poll(poll.id)
                                await session.commit()
                                self.logger.info(f"Successfully closed expired poll {poll.id}")
                            except Exception as e:
                                self.logger.error(f"Error closing expired poll {poll.id}: {e}", exc_info=True)
                                await session.rollback()
                    
                    # ... rest of the existing code ...
                    
                except Exception as poll_error:
                    self.logger.error(f"Error processing poll {poll.id}: {poll_error}", exc_info=True)
            
        except Exception as e:
            self.logger.error(f"Error in _update_active_polls task: {e}", exc_info=True)

    async def _check_admin_permission(self, interaction: discord.Interaction) -> bool:
        """
        Check if the user has admin permissions for the poll type.
        
        Args:
            interaction: The Discord interaction
            
        Returns:
            True if the user has admin permissions, False otherwise
        """
        if not interaction.guild_id:
            return False
            
        # Get poll configuration for this guild
        configs = self.bot.poll_configs.get(interaction.guild_id, [])
        
        # Check if user has any of the admin roles for this guild
        for config in configs:
            if interaction.user.get_role(config.admin_role_id):
                return True
                
        # If user has administrator permission, they can also manage polls
        if interaction.user.guild_permissions.administrator:
            return True
            
        return False

    @tasks.loop(minutes=1)
    async def _check_expired_polls(self):
        """Background task to check for and close polls that have reached their end time."""
        try:
            self.logger.info("Checking for expired polls...")
            
            # First, get all active polls
            active_polls = []
            try:
                async with self.bot.db() as session:
                    poll_service = PollService(session)
                    active_polls = await poll_service.get_all_active_polls()
                self.logger.info(f"Found {len(active_polls)} active polls to check for expiration")
            except Exception as e:
                self.logger.error(f"Error retrieving active polls: {e}", exc_info=True)
                return
            
            # Get current time with timezone information
            now = datetime.now(timezone.utc)
            self.logger.info(f"Current time (UTC): {now}")
            
            # Process each poll in its own session to isolate transactions
            for poll in active_polls:
                poll_info = f"Poll #{poll.id} (type: {poll.poll_type}, end_time: {poll.end_time}, is_active: {poll.is_active})"
                
                if poll.end_time is None:
                    self.logger.info(f"{poll_info} - No end time set, skipping expiration check")
                    continue
                
                # Make sure the poll end time is timezone aware
                if hasattr(poll.end_time, 'tzinfo') and poll.end_time.tzinfo is None:
                    # If end_time doesn't have timezone info, assume UTC
                    poll.end_time = poll.end_time.replace(tzinfo=timezone.utc)
                
                # Check if poll has expired
                if poll.end_time <= now:
                    self.logger.info(f"Poll {poll.id} has expired, marking as closed")
                    
                    try:
                        async with self.bot.db() as session:
                            poll_service = PollService(session)
                            await poll_service.close_poll(poll.id)
                            await session.commit()
                        self.logger.info(f"Successfully closed expired poll {poll.id}")
                    except Exception as e:
                        self.logger.error(f"Error closing expired poll {poll.id}: {e}", exc_info=True)
                else:
                    remaining = poll.end_time - now
                    self.logger.debug(f"{poll_info} - Not expired yet. Remaining time: {remaining}")
        except Exception as e:
            self.logger.error(f"Error in _check_expired_polls task: {e}", exc_info=True)
            
    @_check_expired_polls.before_loop
    async def before_check_expired_polls(self):
        """Wait until the bot is ready before starting the task."""
        await self.bot.wait_until_ready()

    def _ensure_timezone_aware(self, dt):
        """Ensure a datetime object has timezone information (UTC)."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

async def setup(bot: commands.Bot):
    """Setup function for the poll commands cog."""
    await bot.add_cog(PollCommands(bot))