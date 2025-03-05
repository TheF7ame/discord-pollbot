from typing import Set, Optional, Any, Dict, List
import discord
import logging
from discord import ui
from sqlalchemy import select
from sqlalchemy import and_
from datetime import datetime

from src.database.models import Poll, PollStatus, PollMessage, UIState
from src.services.poll_service import PollService
from src.services.points_service import PointsService
from src.utils.exceptions import PollError
from src.utils.constants import ButtonIds
from src.bot.views.base_view import BasePollView, SafePollButton
from discord.ext import commands

logger = logging.getLogger(__name__)

class ClosePollButton(SafePollButton):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label="Close Poll",
            custom_id=ButtonIds.CLOSE_POLL
        )
        logger.info("Created Close Poll button")

    async def _validate_poll_state(self, poll: Poll) -> bool:
        """Validate poll state for this button."""
        return poll.status == PollStatus.OPEN

    async def _process_interaction(
        self,
        interaction: discord.Interaction,
        poll: Optional[Poll]
    ) -> Any:
        """Process the button interaction."""
        if not poll:
            raise PollError("Poll not found")
            
        logger.info(f"Starting to close poll {poll.id}")
        
        async with interaction.client.db() as session:
            poll_service = PollService(session)
            updated_poll = await poll_service.close_poll(poll.id)
            await session.commit()  # Ensure changes are committed
            logger.info(f"Poll {poll.id} marked as closed")
            return updated_poll

    async def _handle_result(self, interaction: discord.Interaction, result: Poll):
        """Handle the interaction result."""
        try:
            # Create new view with answer selection
            new_view = PollAdminView(result)
            logger.info("Created new admin view with answer selection buttons")
            
            # Update the message with new view and instructions
            await interaction.response.edit_message(
                content="**Admin Controls**\nSelect the correct answer(s) and click Confirm",
                view=new_view
            )
            
            # Send confirmation as follow-up
            await interaction.followup.send(
                "Poll has been closed. Please select the correct answer(s).",
                ephemeral=True
            )
            
        except Exception as e:
            logger.error(f"Error in view interaction: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"Failed to update view after closing poll: {str(e)}",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"Poll has been closed, but there was an error updating the view: {str(e)}",
                    ephemeral=True
                )

class AnswerSelectionButton(SafePollButton):
    def __init__(self, option_text: str, row: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=option_text,
            row=row
        )
        self.option_text = option_text

    async def _validate_poll_state(self, poll: Poll) -> bool:
        """Validate poll state for this button."""
        # Allow modifications if poll is closed but not revealed
        # This enables admins to continue selecting answers after interruption
        return (poll.status == PollStatus.CLOSED and not poll.is_revealed) or (
            poll.status == PollStatus.OPEN and 
            isinstance(self.view, PollAdminView) and 
            not getattr(poll, 'is_revealed', False)
        )

    async def _process_interaction(
        self,
        interaction: discord.Interaction,
        poll: Optional[Poll]
    ) -> Any:
        """Process the button interaction."""
        view: 'PollAdminView' = self.view
        logger.info(f"Processing answer selection for option: {self.option_text}")
        
        # Defer the response immediately
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Toggle selection and update button style
            if self.option_text in view.selected_answers:
                logger.info(f"Deselecting option: {self.option_text}")
                view.selected_answers.remove(self.option_text)
                self.style = discord.ButtonStyle.secondary
            else:
                logger.info(f"Selecting option: {self.option_text}")
                view.selected_answers.add(self.option_text)
                self.style = discord.ButtonStyle.primary
            
            logger.info(f"Current selected answers: {view.selected_answers}")
            
            # Persist the UI state after modification
            await view.persist_ui_state(interaction)
            
            # Update confirm button state
            view.confirm_button.disabled = len(view.selected_answers) == 0
            
            try:
                # Try to edit the original message
                if hasattr(interaction, 'message') and interaction.message is not None:
                    await interaction.message.edit(view=view)
                else:
                    logger.warning("Interaction message not available, sending new message")
                    await interaction.followup.send(
                        content="**Admin Controls**\nSelect the correct answer(s) and click Confirm",
                        view=view,
                        ephemeral=True
                    )
            except discord.NotFound:
                logger.warning("Original message not found, sending new message")
                # Send a new message since we can't edit the original
                await interaction.followup.send(
                    content="**Admin Controls**\nSelect the correct answer(s) and click Confirm",
                    view=view,
                    ephemeral=True
                )
                # Store the new message for future reference
                if hasattr(interaction, 'followup') and hasattr(interaction.followup, '_last_response'):
                    view.message = interaction.followup._last_response
            except Exception as e:
                logger.error(f"Error updating message: {e}", exc_info=True)
                # Send a new message if we can't update the original
                await interaction.followup.send(
                    content="**Admin Controls**\nSelect the correct answer(s) and click Confirm",
                    view=view,
                    ephemeral=True
                )
            
            # Send feedback about the selection
            action = "selected" if self.style == discord.ButtonStyle.primary else "deselected"
            selected_count = len(view.selected_answers)
            feedback_msg = f"✅ {action.capitalize()} '{self.option_text}'. Currently selected: {selected_count} answer(s)"
            logger.info(f"Sending feedback message: {feedback_msg}")
            
            await interaction.followup.send(content=feedback_msg, ephemeral=True)
            logger.info("Feedback message sent successfully")
            
            return {'style_updated': True}
            
        except Exception as e:
            logger.error(f"Error processing answer selection: {e}", exc_info=True)
            await interaction.followup.send(
                f"Failed to process selection: {str(e)}",
                ephemeral=True
            )
            return {'error': str(e)}

    async def _handle_result(self, interaction: discord.Interaction, result: Any):
        """Handle the interaction result."""
        # No need to do anything here since we handled everything in _process_interaction
        pass

class ConfirmAnswersButton(SafePollButton):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Confirm Answers",
            disabled=True,
            row=4
        )

    async def _validate_poll_state(self, poll: Poll) -> bool:
        """Validate poll state for this button."""
        return poll.status == PollStatus.CLOSED and not poll.is_revealed

    async def _process_interaction(
        self,
        interaction: discord.Interaction,
        poll: Optional[Poll]
    ) -> Any:
        """Process the button interaction."""
        view: 'PollAdminView' = self.view
        if not poll:
            raise PollError("Poll not found")
            
        correct_answers = list(view.selected_answers)
        logger.info(f"Starting confirm answers callback with answers: {correct_answers}")
        
        # First, defer the response since we'll be doing database operations
        await interaction.response.defer()
        
        async with interaction.client.db() as session:
            try:
                poll_service = PollService(session)
                points_service = PointsService(session)
                
                # Get a fresh poll object bound to this session
                stmt = select(Poll).where(Poll.id == poll.id)
                result = await session.execute(stmt)
                poll = result.scalar_one_or_none()
                if not poll:
                    raise PollError("Poll not found")
                
                # Reveal answers and calculate points in the same transaction
                poll = await poll_service.reveal_answer(poll.id, correct_answers)
                points_updates = await points_service.calculate_poll_points(poll.id)
                
                # Get leaderboard after points are calculated
                leaderboard = await points_service.get_leaderboard(
                    channel_id=poll.channel_id,
                    limit=10
                )
                
                # Ensure transaction is committed
                await session.commit()
                
                return {
                    'poll': poll,
                    'points_updates': points_updates,
                    'leaderboard': leaderboard
                }
            except Exception as e:
                logger.error(f"Error in confirm answers: {e}", exc_info=True)
                await session.rollback()
                raise PollError(f"Failed to confirm answers: {str(e)}")

    async def _handle_result(self, interaction: discord.Interaction, result: Any):
        """Handle the interaction result."""
        view: 'PollAdminView' = self.view
        
        # Format results message
        message = view._format_results_message(
            result['points_updates'],
            result['leaderboard']
        )
        
        # Disable all buttons
        for item in view.children:
            item.disabled = True
        
        try:
            # Try to edit the original message
            if hasattr(interaction, 'message') and interaction.message is not None:
                await interaction.message.edit(view=view)
            else:
                logger.warning("Interaction message not available when confirming answers")
        except discord.NotFound:
            logger.warning("Original message not found when confirming answers")
            # We'll just continue to send the results
        except Exception as e:
            logger.error(f"Error updating message in confirm answers: {e}", exc_info=True)
        
        try:
            await interaction.followup.send(message)
            logger.info("Successfully sent poll results")
        except Exception as e:
            logger.error(f"Error sending results message: {e}", exc_info=True)
            await interaction.followup.send(
                "Failed to send complete results. Please check the logs.",
                ephemeral=True
            )

class PollAdminView(BasePollView):
    def __init__(self, poll: Poll, bot: Optional[commands.Bot] = None):
        """Initialize the admin view for poll management."""
        super().__init__(poll, bot)
        self.selected_answers = set()
        self.logger = logging.getLogger(__name__)
        self.message = None
        self.confirm_button = None
        
        try:
            # Add appropriate buttons based on poll status
            if poll.status == PollStatus.OPEN:
                self.logger.info("Adding Close Poll button")
                self.add_item(ClosePollButton())
            # For CLOSED polls, we'll defer adding option buttons to the async initialize method
            # to avoid lazy loading poll.options in a synchronous context
            
            self.logger.info(f"PollAdminView base initialization complete")
        except Exception as e:
            self.logger.error(f"Error initializing PollAdminView: {e}", exc_info=True)
            raise

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the user has permission to use this view."""
        if not self.bot:
            self.logger.error("Bot instance not available for permission check")
            return False

        try:
            # Get poll configuration for this guild and poll type
            poll_config = self.bot.settings.get_poll_config_by_type(
                interaction.guild_id,
                self.poll.poll_type
            )
            if not poll_config:
                self.logger.warning(
                    f"No poll configuration for guild {interaction.guild_id} "
                    f"and type {self.poll.poll_type}"
                )
                await interaction.response.send_message(
                    "You don't have permission to manage this poll.",
                    ephemeral=True
                )
                return False

            # Check if user has the admin role
            if not interaction.user.get_role(poll_config.admin_role_id):
                self.logger.warning(
                    f"User {interaction.user.id} lacks admin role "
                    f"{poll_config.admin_role_id}"
                )
                await interaction.response.send_message(
                    "You don't have permission to manage this poll.",
                    ephemeral=True
                )
                return False

            return True
        except Exception as e:
            self.logger.error(f"Error checking permissions: {e}", exc_info=True)
            await interaction.response.send_message(
                "An error occurred while checking permissions.",
                ephemeral=True
            )
            return False

    async def persist_message(self, message: discord.Message) -> None:
        """Persist the admin view message."""
        try:
            self.message = message
            async with self.bot.db() as session:
                ui_state = UIState(
                    message_id=message.id,
                    channel_id=message.channel.id,
                    guild_id=message.guild.id,
                    poll_id=self.poll.id,
                    view_type="admin",
                    selected_answers=list(self.selected_answers)
                )
                session.add(ui_state)
                await session.commit()
        except Exception as e:
            self.logger.error(f"Error persisting admin view: {e}", exc_info=True)
            raise PollError(f"Failed to persist admin view: {str(e)}")

    async def initialize(self) -> bool:
        """Initialize the admin view from persisted state."""
        try:
            if not self.bot:
                self.logger.error("Bot instance not available for initialization")
                return False

            # If poll is closed but not revealed, add answer buttons here
            # This avoids the sync access to poll.options
            if self.poll.status == PollStatus.CLOSED and not self.poll.is_revealed:
                self.logger.info("Adding answer selection buttons")
                # Get a fresh poll with options loaded
                async with self.bot.db() as session:
                    poll_service = PollService(session)
                    poll = await poll_service.get_poll(self.poll.id)
                    
                    if poll:
                        # Add a button for each poll option
                        for i, option in enumerate(poll.options):
                            self.add_item(AnswerSelectionButton(option.text, i // 2))
                        # Add confirm button
                        self.confirm_button = ConfirmAnswersButton()
                        self.add_item(self.confirm_button)
            
            # Continue with recovering saved state
            async with self.bot.db() as session:
                stmt = select(UIState).where(
                    and_(
                        UIState.poll_id == self.poll.id,
                        UIState.view_type == "admin"
                    )
                )
                result = await session.execute(stmt)
                ui_state = result.scalar_one_or_none()

                if ui_state:
                    self.selected_answers = set(ui_state.selected_answers)
                    # Update button states based on selected answers
                    for child in self.children:
                        if isinstance(child, AnswerSelectionButton):
                            child.style = (
                                discord.ButtonStyle.success
                                if child.label in self.selected_answers
                                else discord.ButtonStyle.secondary
                            )
                        elif isinstance(child, ConfirmAnswersButton):
                            child.disabled = not bool(self.selected_answers)

            self.logger.info(f"PollAdminView fully initialized with {len(self.children)} buttons")
            return True
        except Exception as e:
            self.logger.error(f"Error initializing admin view: {e}", exc_info=True)
            return False

    async def recover_message(self, channel_id: int) -> Optional[discord.Message]:
        """Recover admin message from database."""
        try:
            if not self.bot:
                return None

            async with self.bot.db() as session:
                stmt = select(PollMessage).where(
                    and_(
                        PollMessage.poll_id == self.poll_id,
                        PollMessage.channel_id == channel_id,
                        PollMessage.message_type == 'admin',
                        PollMessage.is_active == True
                    )
                )
                result = await session.execute(stmt)
                poll_message = result.scalar_one_or_none()
                
                if poll_message:
                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        try:
                            message = await channel.fetch_message(poll_message.message_id)
                            return message
                        except discord.NotFound:
                            # Message was deleted, mark as inactive
                            poll_message.is_active = False
                            await session.commit()
                return None
        except Exception as e:
            self.logger.error(f"Error recovering admin message: {e}")
            return None

    async def recover_state(self) -> bool:
        """Recover view state after bot restart."""
        try:
            if not self.bot:
                return False

            # First recover poll data
            if not await super().recover_state():
                return False

            # Then recover UI state
            if self.poll:
                ui_state = await self.recover_ui_state(str(self.poll.creator_id))
                if ui_state and 'selected_answers' in ui_state:
                    self.selected_answers = set(ui_state['selected_answers'])
                    
                    # Update button states
                    for child in self.children:
                        if isinstance(child, AnswerSelectionButton):
                            if child.option_text in self.selected_answers:
                                child.style = discord.ButtonStyle.primary
                    
                    # Update confirm button state
                    if hasattr(self, 'confirm_button'):
                        self.confirm_button.disabled = len(self.selected_answers) == 0

                # Try to recover the admin message
                if self.poll.channel_id:
                    message = await self.recover_message(self.poll.channel_id)
                    if message:
                        self.message = message

            return True
        except Exception as e:
            self.logger.error(f"Error recovering admin view state: {e}")
            return False

    async def persist_ui_state(self, interaction: discord.Interaction):
        """Persist current UI state."""
        if not self.bot:
            return

        try:
            async with self.bot.db() as session:
                state_data = {
                    'selected_answers': list(self.selected_answers),
                    'last_updated': datetime.utcnow().isoformat()
                }
                
                # Check if state already exists
                stmt = select(UIState).where(
                    and_(
                        UIState.poll_id == self.poll_id,
                        UIState.user_id == str(interaction.user.id)
                    )
                )
                result = await session.execute(stmt)
                ui_state = result.scalar_one_or_none()
                
                if ui_state:
                    ui_state.state_data = state_data
                else:
                    ui_state = UIState(
                        poll_id=self.poll_id,
                        user_id=str(interaction.user.id),
                        state_data=state_data
                    )
                    session.add(ui_state)
                
                await session.commit()
        except Exception as e:
            self.logger.error(f"Error persisting UI state: {e}")
            # Don't raise the error, just log it

    async def recover_ui_state(self, user_id: str) -> Optional[Dict]:
        """Recover UI state for a user."""
        try:
            if not self.bot:
                return None

            async with self.bot.db() as session:
                stmt = select(UIState).where(
                    and_(
                        UIState.poll_id == self.poll_id,
                        UIState.user_id == user_id
                    )
                )
                result = await session.execute(stmt)
                ui_state = result.scalar_one_or_none()
                return ui_state.state_data if ui_state else None
        except Exception as e:
            self.logger.error(f"Error recovering UI state: {e}")
            return None

    def _format_results_message(self, points_updates: list, leaderboard: list) -> str:
        """Format poll results message with statistics and top scorers."""
        self.logger.info("Starting to format results message")
        self.logger.info(f"Points updates: {points_updates}")
        self.logger.info(f"Leaderboard: {leaderboard}")
        
        # Get statistics
        total_participants = len(points_updates)
        points_distribution = {}
        for update in points_updates:
            points = update['poll_points']
            points_distribution[points] = points_distribution.get(points, 0) + 1

        self.logger.info(f"Points distribution: {points_distribution}")

        # Format message
        correct_answers = self.poll.correct_answers if self.poll else self.metadata.get('correct_answers', [])
        self.logger.info(f"Correct answers: {correct_answers}")
        
        correct_answers_text = ", ".join(str(ans) for ans in (correct_answers or []))
        self.logger.info(f"Formatted correct answers text: {correct_answers_text}")
        
        # Build message sections
        sections = [
            f"📊 **Poll Results**",
            f"✅ Correct answer(s): {correct_answers_text}",
            f"\n📈 **Statistics**",
            f"Total participants: {total_participants}"
        ]
        
        # Points distribution section
        if points_distribution:
            sections.append("\nPoints distribution:")
            for points, count in sorted(points_distribution.items()):
                sections.append(
                    f"• {count} {'person' if count == 1 else 'people'} scored {points} "
                    f"point{'s' if points != 1 else ''}"
                )

        # Leaderboard section
        if leaderboard:
            sections.append("\n🏆 **Top 10 Overall Scores**")
            for i, entry in enumerate(leaderboard, 1):
                prefix = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                sections.append(
                    f"{prefix} <@{entry['user_id']}>: {entry['total_points']} points "
                    f"({entry['total_correct']} correct)"
                )

        final_message = "\n".join(sections)
        self.logger.info(f"Final formatted message: {final_message}")
        return final_message 