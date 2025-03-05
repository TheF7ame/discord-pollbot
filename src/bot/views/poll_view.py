from typing import List, Optional, Set, Dict, Any
import discord
from discord import ui
import logging
from datetime import datetime
import asyncio
from discord.ext import commands
from sqlalchemy import select, and_, desc

from src.database.models import Poll, PollStatus, PollMessage, UIState
from src.services.poll_service import PollService
from src.services.points_service import PointsService
from src.utils.exceptions import PollError
from src.utils.constants import ButtonIds, Messages
from src.bot.views.base_view import BasePollView, SafePollButton
from src.bot.views.poll_admin_view import PollAdminView  # Import PollAdminView from the correct module

logger = logging.getLogger(__name__)

class PollOptionButton(discord.ui.Button):
    def __init__(self, label: str, custom_id: str, row: int):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            custom_id=custom_id,
            row=row
        )
        self.logger = logging.getLogger(__name__)

    async def callback(self, interaction: discord.Interaction):
        """Handle button click with state persistence and recovery."""
        logger.info(f"Button clicked: {self.label} by user {interaction.user.id}")
        try:
            # Defer response immediately with thinking state
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
                logger.info("Response deferred")
            except discord.errors.NotFound:
                logger.warning("Interaction already acknowledged, continuing with followup")
                pass
            
            view: PollView = self.view
            _, poll_id, _, option_id = self.custom_id.split('_')
            poll_id = int(poll_id)
            logger.info(f"Processing poll_id: {poll_id}, option_id: {option_id}")
            
            # Force message recovery at the start
            recovered_message = await view.recover_message(interaction.channel_id)
            if recovered_message:
                view.message = recovered_message
                logger.info(f"Successfully recovered message {recovered_message.id}")
            else:
                logger.error("Failed to recover message reference")
                await interaction.followup.send(
                    "Failed to process your selection. Please try again.",
                    ephemeral=True
                )
                return
            
            async with interaction.client.db() as session:
                poll_service = PollService(session)
                
                # Get fresh poll state with retries
                poll = None
                for attempt in range(3):
                    try:
                        poll = await poll_service.get_poll_with_refresh(poll_id)
                        if poll:
                            # Validate poll state
                            now = datetime.utcnow()
                            if now > poll.end_time:
                                await interaction.followup.send("This poll has ended.", ephemeral=True)
                                return
                            if not poll.is_active:
                                await interaction.followup.send("This poll is no longer active.", ephemeral=True)
                                return
                            break
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        logger.error(f"Attempt {attempt + 1} to get poll failed: {e}")
                        if attempt == 2:
                            await interaction.followup.send(
                                "Failed to process your selection. Please try again.",
                                ephemeral=True
                            )
                            return
                        continue

                if not poll:
                    logger.warning("Poll not found")
                    await interaction.followup.send(
                        "Poll not found or has been deleted.",
                        ephemeral=True
                    )
                    return

                # Try to recover existing selections with retries
                ui_state = None
                current_selections = []
                for attempt in range(3):
                    try:
                        ui_state = await view.recover_ui_state(str(interaction.user.id))
                        if ui_state:
                            current_selections = ui_state.get('selections', [])
                        break
                    except Exception as e:
                        logger.error(f"Attempt {attempt + 1} to recover UI state failed: {e}")
                        if attempt == 2:
                            logger.warning("Failed to recover UI state, proceeding with empty selections")
                        await asyncio.sleep(0.5)
                        continue

                logger.info(f"Current selections: {current_selections}")
                
                # Process selection with retry logic
                for attempt in range(3):
                    try:
                        logger.info(f"Processing selection attempt {attempt + 1}")
                        # Process selection
                        selection = await poll_service.add_selection(
                            poll_id=poll_id,
                            user_id=str(interaction.user.id),
                            selection=self.label
                        )
                        logger.info(f"Selection processed: {selection.selections if selection else None}")
                        
                        # Persist UI state with retries
                        new_state = {
                            'selections': selection.selections,
                            'last_updated': datetime.utcnow().isoformat()
                        }
                        logger.info(f"Persisting new UI state: {new_state}")
                        
                        await view.persist_ui_state(interaction, new_state)
                        logger.info("UI state persisted")
                        
                        # Update message with view
                        try:
                            await view.message.edit(view=view)
                            logger.info("Message updated successfully")
                        except discord.NotFound:
                            logger.warning("Original message not found, creating new response")
                            await interaction.followup.send(
                                content=f"Your selections: {', '.join(selection.selections)}",
                                ephemeral=True
                            )
                        except Exception as e:
                            logger.error(f"Error updating message: {e}")
                            # Continue execution to at least show the confirmation
                        
                        # Send confirmation
                        await interaction.followup.send(
                            f"Your selections: {', '.join(selection.selections)}",
                            ephemeral=True
                        )
                        logger.info("Confirmation sent")
                        break
                    except PollError as pe:
                        # Handle known poll errors gracefully
                        logger.warning(f"Poll error: {pe}")
                        await interaction.followup.send(str(pe), ephemeral=True)
                        return
                    except Exception as e:
                        logger.error(f"Attempt {attempt + 1} failed: {str(e)}", exc_info=True)
                        if attempt == 2:  # Last attempt
                            logger.error(f"Failed to process selection after 3 attempts: {str(e)}")
                            # Try to restore previous state if available
                            if current_selections:
                                logger.info(f"Attempting to restore previous selections: {current_selections}")
                                await poll_service.update_user_selection(
                                    poll_id=poll_id,
                                    user_id=str(interaction.user.id),
                                    selections=current_selections
                                )
                            await interaction.followup.send(
                                "Failed to process your selection. Please try again.",
                                ephemeral=True
                            )
                            return
                        await asyncio.sleep(1)
                        continue
                        
        except discord.errors.InteractionResponded:
            logger.warning("Interaction already responded to")
            # This is fine, just continue
            pass
        except Exception as e:
            logger.error(f"Error in button callback: {str(e)}", exc_info=True)
            try:
                await interaction.followup.send(
                    "An error occurred while processing your selection. Please try again.",
                    ephemeral=True
                )
            except:
                # If we can't send a followup, the interaction might be completely invalid
                logger.error("Failed to send error message")
                pass

class PollView(BasePollView):
    def __init__(self, poll: Poll, bot: Optional[commands.Bot] = None):
        super().__init__(poll, bot)
        self.message = None
        self.logger = logging.getLogger(__name__)
        logger.info(f"Initializing PollView for poll {poll.id if poll else 'None'}")
        logger.info(f"Bot instance provided: {bool(bot)}")
        self._setup_buttons()  # Synchronous setup
        if bot:
            self.start_countdown_task()
            logger.info("Countdown task started")

    def _setup_buttons(self):
        """Setup poll option buttons synchronously."""
        try:
            # If poll options aren't loaded yet, we'll set up buttons later in initialize()
            if not hasattr(self, 'options_loaded') or not self.options_loaded:
                logger.info("Options not loaded yet, deferring button setup to initialize()")
                return

            if not self.poll or not self.poll.options:
                logger.error("No poll options available for button setup")
                return

            logger.info(f"Setting up buttons for poll {self.poll_id}")
            logger.info(f"Number of options: {len(self.poll.options)}")
            
            for i, option in enumerate(self.poll.options):
                button = PollOptionButton(
                    label=option.text,
                    custom_id=f"poll_{self.poll_id}_option_{option.id}",
                    row=i // 2
                )
                self.add_item(button)
                logger.info(f"Added button for option: {option.text} with custom_id: {button.custom_id}")
            
            logger.info("Button setup completed successfully")
        except Exception as e:
            logger.error(f"Error setting up buttons: {str(e)}", exc_info=True)
            raise

    async def cleanup_duplicate_messages(self):
        """Clean up duplicate messages for this poll."""
        try:
            async with self.bot.db() as session:
                # Get all messages for this poll ordered by creation time
                stmt = select(PollMessage).where(
                    and_(
                        PollMessage.poll_id == self.poll.id,
                        PollMessage.message_type == 'poll'
                    )
                ).order_by(desc(PollMessage.created_at))
                
                result = await session.execute(stmt)
                messages = result.scalars().all()
                
                if len(messages) <= 1:
                    return
                    
                # Keep the most recent message, delete the rest
                most_recent = messages[0]
                for msg in messages[1:]:
                    session.delete(msg)
                
                await session.commit()
                logger.info(f"Cleaned up {len(messages) - 1} duplicate messages for poll {self.poll.id}")
                
        except Exception as e:
            logger.error(f"Error cleaning up duplicate messages: {e}", exc_info=True)

    @staticmethod
    async def recover_all_active_polls(bot: commands.Bot):
        """Recover all active polls and their views on bot startup."""
        try:
            logger.info("Starting recovery of all active polls")
            async with bot.db() as session:
                poll_service = PollService(session)
                active_polls = await poll_service.get_active_polls()
                logger.info(f"Found {len(active_polls)} active polls to recover")
                
                for poll in active_polls:
                    try:
                        # Create new view for the poll
                        view = PollView(poll, bot)
                        logger.info(f"Created new view for poll {poll.id}")
                        
                        # Get the most recent message for this poll
                        stmt = select(PollMessage).where(
                            and_(
                                PollMessage.poll_id == poll.id,
                                PollMessage.message_type == 'poll'
                            )
                        ).order_by(desc(PollMessage.created_at))
                        
                        result = await session.execute(stmt)
                        poll_message = result.scalar_one_or_none()
                        
                        if poll_message:
                            try:
                                # Try to get the channel and message
                                channel = bot.get_channel(poll_message.channel_id)
                                if channel:
                                    try:
                                        message = await channel.fetch_message(poll_message.message_id)
                                        if message:
                                            # Update message with new view
                                            view.message = message
                                            await message.edit(view=view)
                                            logger.info(f"Successfully recovered poll {poll.id} message {message.id}")
                                            
                                            # Start the countdown task
                                            view.start_countdown_task()
                                            continue
                                    except discord.NotFound:
                                        logger.warning(f"Message {poll_message.message_id} not found for poll {poll.id}")
                                        session.delete(poll_message)
                                        await session.commit()
                            except Exception as e:
                                logger.error(f"Error recovering message for poll {poll.id}: {e}")
                        
                        logger.warning(f"Failed to recover message for poll {poll.id}")
                        
                    except Exception as e:
                        logger.error(f"Error recovering poll {poll.id}: {e}")
                        continue
                        
            logger.info("Completed recovery of all active polls")
            
        except Exception as e:
            logger.error(f"Error during poll recovery: {e}", exc_info=True)

    async def initialize(self):
        """Initialize async components of the view."""
        try:
            logger.info(f"Starting async initialization for poll {self.poll_id}")
            if self.bot:
                logger.info("Bot instance available, proceeding with initialization")
                # Refresh poll data
                await self.refresh_poll_data()
                logger.info("Poll data refreshed")
                
                if self.poll:
                    # If options weren't loaded in constructor, load them now and set up buttons
                    if not hasattr(self, 'options_loaded') or not self.options_loaded:
                        logger.info("Loading poll options during initialization")
                        # Now we're in an async context, so it's safe to access options
                        if hasattr(self.poll, 'options') and self.poll.options:
                            # Update metadata with options
                            self.metadata['options'] = [(opt.id, opt.text) for opt in self.poll.options]
                            self.options_loaded = True
                            # Set up buttons now that options are loaded
                            self._setup_buttons()
                            logger.info("Options loaded and buttons set up")
                    
                    # Clean up duplicate messages first
                    await self.cleanup_duplicate_messages()
                    
                    # Check if poll should be active based on end time
                    now = datetime.utcnow()
                    logger.info(f"Checking poll status - Current time: {now}, End time: {self.poll.end_time}")
                    
                    if now <= self.poll.end_time:
                        async with self.bot.db() as session:
                            # Force poll to active state if within time limit
                            if not self.poll.is_active:
                                logger.info(f"Reactivating poll {self.poll.id} during initialization")
                                self.poll.is_active = True
                                session.add(self.poll)
                                await session.commit()
                                logger.info("Poll reactivated successfully")
                            
                            # Recover message if needed
                            if not self.message and self.poll.channel_id:
                                logger.info("Attempting to recover poll message")
                                self.message = await self.recover_message(self.poll.channel_id)
                                if self.message:
                                    logger.info(f"Successfully recovered message {self.message.id}")
                                    # Update the message with the current view to ensure buttons work
                                    try:
                                        await self.message.edit(view=self)
                                        logger.info("Message updated with current view")
                                    except Exception as e:
                                        logger.error(f"Failed to update message: {e}")
                    
                    return True
                else:
                    logger.warning("Poll not found during initialization")
                    return False
            else:
                logger.warning("No bot instance available for initialization")
                return False
        except Exception as e:
            logger.error(f"Error during view initialization: {e}", exc_info=True)
            return False

    async def persist_message(self, message: discord.Message) -> None:
        """Persist the message to the database."""
        try:
            async with self.bot.db() as session:
                # Ensure IDs are integers
                message_id = int(message.id)
                channel_id = int(message.channel.id)
                
                # Check if message already exists
                stmt = select(PollMessage).where(
                    and_(
                        PollMessage.poll_id == self.poll.id,
                        PollMessage.message_id == message_id
                    )
                )
                result = await session.execute(stmt)
                existing_message = result.scalar_one_or_none()
                
                if existing_message:
                    logger.info(f"Message {message_id} already exists for poll {self.poll.id}")
                    return
                
                poll_message = PollMessage(
                    poll_id=self.poll.id,
                    message_id=message_id,
                    channel_id=channel_id,
                    message_type='poll'
                )
                session.add(poll_message)
                await session.commit()
                logger.info(f"Message {message_id} persisted for poll {self.poll.id}")
        except Exception as e:
            logger.error(f"Failed to persist message: {e}", exc_info=True)
            raise

    async def persist_ui_state(self, interaction: discord.Interaction, state_data: Dict):
        """Persist UI state for a user."""
        try:
            logger.info(f"Persisting UI state for user {interaction.user.id} in poll {self.poll_id}")
            logger.info(f"State data to persist: {state_data}")
            async with self.bot.db() as session:
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
                    logger.info("Updating existing UI state")
                    ui_state.state_data = state_data
                else:
                    logger.info("Creating new UI state")
                    ui_state = UIState(
                        poll_id=self.poll_id,
                        user_id=str(interaction.user.id),
                        state_data=state_data
                    )
                    session.add(ui_state)
                
                await session.commit()
                logger.info("UI state persisted successfully")
        except Exception as e:
            logger.error(f"Error persisting UI state: {str(e)}", exc_info=True)
            raise

    async def recover_ui_state(self, user_id: str) -> Optional[Dict]:
        """Recover UI state for a user."""
        try:
            logger.info(f"Attempting to recover UI state for user {user_id} in poll {self.poll_id}")
            async with self.bot.db() as session:
                stmt = select(UIState).where(
                    and_(
                        UIState.poll_id == self.poll_id,
                        UIState.user_id == user_id
                    )
                )
                result = await session.execute(stmt)
                ui_state = result.scalar_one_or_none()
                logger.info(f"Recovered UI state: {ui_state.state_data if ui_state else None}")
                return ui_state.state_data if ui_state else None
        except Exception as e:
            logger.error(f"Error recovering UI state: {str(e)}", exc_info=True)
            return None

    async def recover_message(self, channel_id: int) -> Optional[discord.Message]:
        """Recover poll message from database."""
        try:
            if not self.poll:
                logger.warning("No poll object available for message recovery")
                return None

            async with self.bot.db() as session:
                # Get latest message for this poll
                stmt = select(PollMessage).where(
                    and_(
                        PollMessage.poll_id == self.poll.id,
                        PollMessage.channel_id == channel_id,  # channel_id is already an int
                        PollMessage.message_type == 'poll'  # Only get poll messages
                    )
                ).order_by(desc(PollMessage.created_at)).limit(1)  # Get most recent message
                
                result = await session.execute(stmt)
                poll_message = result.scalar_one_or_none()

                if poll_message and poll_message.message_id:
                    try:
                        # Try to fetch the message from Discord
                        channel = self.bot.get_channel(channel_id)
                        if channel:
                            message = await channel.fetch_message(int(poll_message.message_id))
                            if message:
                                logger.info(f"Successfully recovered message {message.id}")
                                return message
                    except discord.NotFound:
                        logger.warning(f"Message {poll_message.message_id} not found in Discord")
                        # Message might be deleted from Discord, remove from database
                        session.delete(poll_message)
                        await session.commit()
                    except Exception as e:
                        logger.warning(f"Failed to fetch message: {e}")
                        # Message might be deleted from Discord, remove from database
                        session.delete(poll_message)
                        await session.commit()

            return None

        except Exception as e:
            logger.error(f"Error recovering message: {e}", exc_info=True)
            return None

    def format_time_remaining(self) -> str:
        """Format the remaining time until poll closes."""
        now = datetime.utcnow()
        end_time = self.poll.end_time if self.poll else self.metadata['end_time']
        if end_time <= now:
            return "Poll has ended"
            
        delta = end_time - now
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
            
        return f"Time remaining: {' '.join(parts)}"

    def start_countdown_task(self):
        """Start the countdown update task."""
        async def update_countdown():
            while True:
                await asyncio.sleep(1)  # Wait for message to be set
                if not self.message:
                    continue
                    
                # Refresh poll data
                await self.refresh_poll_data()
                
                if self.poll and self.poll.status != PollStatus.OPEN:
                    break
                    
                try:
                    await self.message.edit(
                        content=(
                            f"**Poll: {self.poll.question if self.poll else self.metadata['question']}**\n"
                            f"Select up to {self.poll.max_selections if self.poll else self.metadata['max_selections']} "
                            f"option{'s' if (self.poll.max_selections if self.poll else self.metadata['max_selections']) > 1 else ''}\n"
                            f"{self.format_time_remaining()}"
                        ),
                        view=self
                    )
                except discord.NotFound:
                    break
                
                # Calculate dynamic sleep duration
                now = datetime.utcnow()
                end_time = self.poll.end_time if self.poll else self.metadata['end_time']
                remaining = end_time - now
                if remaining.days > 0:
                    await asyncio.sleep(3600)  # 1 hour
                elif remaining.seconds > 3600:
                    await asyncio.sleep(900)   # 15 minutes
                elif remaining.seconds > 300:
                    await asyncio.sleep(300)   # 5 minutes
                else:
                    await asyncio.sleep(60)    # 1 minute
                
        self.bot.loop.create_task(update_countdown())

    async def update_button_states(self, selections: List[str]):
        """Update all button states based on current selections."""
        logger.debug(f"Updating button states for selections: {selections}")
        pass  # No style changes needed

    async def recover_poll_state(self):
        """Recover poll state after bot reconnection."""
        if not self.poll:
            return
            
        try:
            async with self.bot.db.begin() as session:
                poll_service = PollService(session)
                self.poll = await poll_service.get_poll(self.poll.id)
                
                if self.poll and self.poll.status == PollStatus.OPEN:
                    self.start_countdown_task()
        except Exception as e:
            logger.error(f"Error recovering poll state: {e}", exc_info=True)

    def _format_results_message(self, points_updates: list, leaderboard: list) -> str:
        """Format poll results message with statistics and top scorers."""
        logger.info("Starting to format results message")
        logger.info(f"Points updates: {points_updates}")
        logger.info(f"Leaderboard: {leaderboard}")
        
        # Get statistics
        total_participants = len(points_updates)
        points_distribution = {}
        for update in points_updates:
            points = update['poll_points']
            points_distribution[points] = points_distribution.get(points, 0) + 1

        logger.info(f"Points distribution: {points_distribution}")

        # Format message
        correct_answers = self.metadata.get('correct_answers', [])
        logger.info(f"Correct answers from metadata: {correct_answers}")
        
        if not correct_answers and hasattr(self, 'poll') and self.poll:
            correct_answers = self.poll.correct_answers or []
            logger.info(f"Correct answers from poll object: {correct_answers}")
        
        correct_answers_text = ", ".join(str(ans) for ans in (correct_answers or []))
        logger.info(f"Formatted correct answers text: {correct_answers_text}")
        
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
                sections.append(f"{prefix} <@{entry['user_id']}>: {entry['points']} points")

        final_message = "\n".join(sections)
        logger.info(f"Final formatted message: {final_message}")
        return final_message

    async def refresh_poll_data(self):
        """Refresh poll data from database."""
        try:
            logger.info(f"Refreshing poll data for poll {self.poll_id}")
            if not self.bot:
                logger.warning("No bot instance available for refresh")
                return False
            
            async with self.bot.db() as session:
                poll_service = PollService(session)
                refreshed_poll = await poll_service.get_poll_with_refresh(self.poll_id)
                
                if refreshed_poll:
                    logger.info(f"Poll {self.poll_id} refreshed successfully")
                    logger.info(f"Poll status: {refreshed_poll.status}, Active: {refreshed_poll.is_active}")
                    logger.info(f"End time: {refreshed_poll.end_time}")
                    self.poll = refreshed_poll
                    return True
                else:
                    logger.warning(f"Failed to refresh poll {self.poll_id}")
                    return False
        except Exception as e:
            logger.error(f"Error refreshing poll data: {str(e)}", exc_info=True)
            return False

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
            logger.info(f"Poll {poll.id} marked as closed")
            return updated_poll

    async def _handle_result(self, interaction: discord.Interaction, result: Poll):
        """Handle the interaction result."""
        try:
            # Create new view with answer selection
            new_view = PollAdminView(result)
            logger.info("Created new admin view with answer selection buttons")
            
            # Update the original message with new view
            await interaction.response.edit_message(
                content="**Admin Controls**\nSelect the correct answer(s) and click Confirm",
                view=new_view
            )
            
            # Send confirmation as follow-up
            await interaction.followup.send(
                "Poll has been closed. Please select the correct answer(s).",
                ephemeral=True
            )
            
        except discord.errors.InteractionResponded:
            logger.warning("Interaction already responded to, using message.edit")
            try:
                await interaction.message.edit(
                    content="**Admin Controls**\nSelect the correct answer(s) and click Confirm",
                    view=new_view
                )
                await interaction.followup.send(
                    "Poll has been closed. Please select the correct answer(s).",
                    ephemeral=True
                )
            except Exception as e:
                logger.error(f"Error updating message after interaction response: {e}", exc_info=True)
                await interaction.followup.send(
                    "Poll has been closed, but there was an error updating the view. Please refresh.",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error in view interaction: {e}", exc_info=True)
            await interaction.followup.send(
                f"Failed to update view after closing poll: {str(e)}",
                ephemeral=True
            )

class RevealAnswerButton(SafePollButton):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.primary,
            label="Reveal Answer",
            custom_id=ButtonIds.REVEAL_ANSWER
        )
        logger.info("Created Reveal Answer button")

    async def _validate_poll_state(self, poll: Poll) -> bool:
        """Validate poll state for this button."""
        return poll.status == PollStatus.CLOSED and not poll.is_revealed

    async def _process_interaction(
        self,
        interaction: discord.Interaction,
        poll: Optional[Poll]
    ) -> Any:
        """Process the button interaction."""
        if not poll:
            raise PollError("Poll not found")
            
        if not poll.correct_answers:
            logger.warning("No correct answers set for poll")
            raise PollError("Please use the /reveal command to specify the correct answers.")
            
        logger.info(f"Revealing answers for poll {poll.id}: {poll.correct_answers}")
        
        async with interaction.client.db() as session:
            poll_service = PollService(session)
            points_service = PointsService(session)
            
            # Reveal answers and calculate points in a single transaction
            poll = await poll_service.reveal_poll(poll.id)
            points_updates = await points_service.calculate_poll_points(poll.id)
            logger.info(f"Points calculated: {points_updates}")
            
            # Get global leaderboard
            leaderboard = await points_service.get_leaderboard(limit=10)
            logger.info(f"Retrieved leaderboard with {len(leaderboard)} entries")
            
            return {
                'poll': poll,
                'points_updates': points_updates,
                'leaderboard': leaderboard
            }

    async def _handle_result(self, interaction: discord.Interaction, result: Dict[str, Any]):
        """Handle the interaction result."""
        view: PollAdminView = self.view
        
        # Format and send results
        message = self._format_results_message(
            result['points_updates'],
            result['leaderboard']
        )
        logger.info(f"Sending results message: {message}")
        await interaction.followup.send(message)
        
        # Disable all buttons in the view
        for item in view.children:
            item.disabled = True
        await interaction.message.edit(view=view)
