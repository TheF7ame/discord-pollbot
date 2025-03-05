from typing import Dict, Any, Optional, Union
import discord
from discord.ext import commands
import logging
from datetime import datetime, timedelta
from sqlalchemy import select, and_

from src.database.models import Poll, PollStatus, UIState
from src.database.session import SessionManager
from src.services.poll_service import PollService
from src.utils.exceptions import PollError

logger = logging.getLogger(__name__)

class BasePollView(discord.ui.View):
    """Base class for poll-related views."""
    def __init__(self, poll_data: Union[Poll, Dict[str, Any]], bot: Optional[commands.Bot] = None):
        super().__init__(timeout=None)
        
        # Handle both Poll object and dictionary input
        if isinstance(poll_data, Poll):
            self.poll = poll_data
            self.poll_id = poll_data.id
            self.channel_id = poll_data.channel_id
            self.metadata = {
                'question': poll_data.question,
                'max_selections': poll_data.max_selections,
                'end_time': poll_data.end_time,
                'status': poll_data.status,
                'is_revealed': poll_data.is_revealed,
                'correct_answers': poll_data.correct_answers if hasattr(poll_data, 'correct_answers') else None
            }
            # Don't access poll_data.options here - defer to async initialize method
            self.options_loaded = False
        else:
            self.poll = None
            self.poll_id = poll_data['id']
            self.channel_id = poll_data['channel_id']
            self.metadata = {
                'question': poll_data['question'],
                'max_selections': poll_data['max_selections'],
                'end_time': poll_data['end_time'],
                'options': poll_data['options'],
                'status': poll_data.get('status'),
                'is_revealed': poll_data.get('is_revealed', False),
                'correct_answers': poll_data.get('correct_answers')
            }
            self.options_loaded = True
        
        self.bot = bot
        self._last_refresh = datetime.utcnow()
        self.refresh_interval = timedelta(minutes=1)
        self.logger = logging.getLogger(__name__)

    async def refresh_poll_data(self) -> bool:
        """Refresh poll data from database."""
        try:
            if not self.bot:
                return False

            now = datetime.utcnow()
            if now - self._last_refresh < self.refresh_interval:
                return True

            async with self.bot.db() as session:
                poll_service = PollService(session)
                poll = await poll_service.get_poll_with_refresh(self.poll_id)
                
                if not poll:
                    return False

                self.poll = poll
                self.metadata.update({
                    'status': poll.status,
                    'is_revealed': poll.is_revealed,
                    'correct_answers': poll.correct_answers
                })
                self._last_refresh = now
                return True
        except Exception as e:
            self.logger.error(f"Error refreshing poll data: {e}")
            return False

    async def recover_state(self) -> bool:
        """Recover view state after bot restart."""
        try:
            if not self.bot:
                return False

            async with self.bot.db() as session:
                poll_service = PollService(session)
                poll = await poll_service.get_poll_with_refresh(self.poll_id)
                
                if not poll:
                    return False

                self.poll = poll
                self.metadata.update({
                    'status': poll.status,
                    'is_revealed': poll.is_revealed,
                    'correct_answers': poll.correct_answers
                })
                return True
        except Exception as e:
            self.logger.error(f"Error recovering view state: {e}")
            return False

    async def refresh_state(self, interaction: discord.Interaction) -> Optional[Poll]:
        """Refresh the view's state if needed."""
        try:
            now = datetime.utcnow()
            if now - self._last_refresh < self.refresh_interval:
                return None

            async with interaction.client.db() as session:
                poll_service = PollService(session)
                poll = await poll_service.get_poll_with_refresh(self.poll_id)
                
                if poll:
                    self.metadata.update({
                        'status': poll.status,
                        'is_revealed': poll.is_revealed,
                        'end_time': poll.end_time
                    })
                self._last_refresh = now
                return poll
        except Exception as e:
            self.logger.error(f"Error refreshing state: {e}", exc_info=True)
            return None

    async def handle_interaction_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        message: str = "An error occurred"
    ):
        """Handle interaction errors consistently."""
        self.logger.error(f"Error in view interaction: {error}", exc_info=True)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"{message}: {str(error)}",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"{message}: {str(error)}",
                    ephemeral=True
                )
        except Exception as e:
            self.logger.error(f"Failed to send error message: {e}", exc_info=True)

    async def persist_ui_state(self, interaction: discord.Interaction, state_data: dict = None) -> None:
        """Persist UI state to database."""
        try:
            if not self.poll:
                logger.warning("No poll object available for UI state persistence")
                return

            async with interaction.client.db() as session:
                # Get existing state first
                stmt = select(UIState).where(
                    and_(
                        UIState.poll_id == self.poll.id,
                        UIState.user_id == str(interaction.user.id)
                    )
                )
                result = await session.execute(stmt)
                ui_state = result.scalar_one_or_none()

                # Validate state data
                if state_data is None:
                    state_data = {}
                if not isinstance(state_data, dict):
                    logger.error(f"Invalid state data type: {type(state_data)}")
                    raise ValueError("State data must be a dictionary")

                # Add metadata
                state_data['last_updated'] = datetime.utcnow().isoformat()
                state_data['interaction_id'] = str(interaction.id)

                if ui_state:
                    # Update existing state
                    ui_state.state_data = state_data
                    ui_state.last_interaction = datetime.utcnow()
                else:
                    # Create new state
                    ui_state = UIState(
                        poll_id=self.poll.id,
                        user_id=str(interaction.user.id),
                        state_data=state_data,
                        last_interaction=datetime.utcnow()
                    )
                    session.add(ui_state)

                try:
                    await session.commit()
                    await session.refresh(ui_state)
                    logger.info(f"UI state persisted successfully for user {interaction.user.id}")
                except Exception as e:
                    logger.error(f"Failed to commit UI state: {e}")
                    await session.rollback()
                    raise

        except Exception as e:
            logger.error(f"Error persisting UI state: {e}", exc_info=True)
            raise

    async def recover_ui_state(self, user_id: str) -> Optional[dict]:
        """Recover UI state from database."""
        try:
            if not self.poll:
                logger.warning("No poll object available for UI state recovery")
                return None

            async with self.bot.db() as session:
                stmt = select(UIState).where(
                    and_(
                        UIState.poll_id == self.poll.id,
                        UIState.user_id == user_id
                    )
                )
                result = await session.execute(stmt)
                ui_state = result.scalar_one_or_none()

                if ui_state:
                    # Validate recovered state
                    if not isinstance(ui_state.state_data, dict):
                        logger.error(f"Invalid state data type in database: {type(ui_state.state_data)}")
                        return None

                    # Check state freshness
                    last_updated = datetime.fromisoformat(ui_state.state_data.get('last_updated', '2000-01-01'))
                    if datetime.utcnow() - last_updated > timedelta(hours=24):
                        logger.warning(f"Recovered state is stale: {last_updated}")
                        return None

                    logger.info(f"Successfully recovered UI state for user {user_id}")
                    return ui_state.state_data

                logger.info(f"No UI state found for user {user_id}")
                return None

        except Exception as e:
            logger.error(f"Error recovering UI state: {e}", exc_info=True)
            return None

class SafePollButton(discord.ui.Button):
    """Base class for poll buttons with safe interaction handling."""
    async def callback(self, interaction: discord.Interaction):
        view: BasePollView = self.view
        try:
            # Create a new session for this interaction
            async with interaction.client.db() as session:
                # Get fresh poll state
                poll_service = PollService(session)
                poll = await poll_service.get_poll_with_refresh(view.poll_id)
                
                if not poll:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "Poll not found or has been deleted.",
                            ephemeral=True
                        )
                    return
                
                if not await self._validate_poll_state(poll):
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            "This action is no longer valid for the current poll state.",
                            ephemeral=True
                        )
                    return

                # Process the interaction with the fresh poll object
                result = await self._process_interaction(interaction, poll)
                
                # Handle the result
                await self._handle_result(interaction, result)
                
        except Exception as e:
            logger.error(f"Error in button callback: {e}", exc_info=True)
            error_message = f"Failed to process button interaction: {str(e)}"
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_message, ephemeral=True)
                else:
                    await interaction.followup.send(error_message, ephemeral=True)
            except Exception as e2:
                logger.error(f"Failed to send error message: {e2}", exc_info=True)

    async def _validate_poll_state(self, poll: Poll) -> bool:
        """Validate poll state for this button."""
        raise NotImplementedError

    async def _process_interaction(
        self,
        interaction: discord.Interaction,
        poll: Optional[Poll]
    ) -> Any:
        """Process the button interaction."""
        raise NotImplementedError

    async def _handle_result(self, interaction: discord.Interaction, result: Any):
        """Handle the interaction result."""
        raise NotImplementedError 