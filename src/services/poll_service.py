from datetime import datetime, timedelta, timezone
from typing import List, Optional, Set, Dict, Any, Union, Tuple
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, desc, delete, or_, func, insert, text
from sqlalchemy.orm import selectinload
import asyncio
import json
from sqlalchemy import inspect

from src.database.models import Poll, UserPollSelection, PollStatus, PollOption, UserScore, Guild, PollMessage, Vote
from src.database.session import SessionManager, TransactionManager
from src.utils.exceptions import PollError, StateError, ValidationError, DatabaseError
from src.utils.time_utils import parse_duration
from src.utils.constants import PollType

logger = logging.getLogger(__name__)

class PollService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.transaction_manager = TransactionManager(db_session)
        self.logger = logging.getLogger(__name__)
    
    def _make_db_safe_datetime(self, dt):
        """Convert timezone-aware datetime to naive UTC datetime for database storage."""
        if dt is None:
            return None
        
        # If datetime is already naive, return it as-is
        if dt.tzinfo is None:
            return dt
            
        # Convert to UTC and remove timezone info
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    
    def _ensure_timezone_aware(self, dt):
        """Ensure a datetime object has timezone information (UTC)."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    async def create_poll(
        self,
        question: str,
        options: List[str],
        creator_id: str,
        guild_id: int,
        poll_type: str,
        max_selections: int = 1,
        duration: str = "5d",
        description: str = None,
        show_votes_while_active: bool = False
    ) -> Poll:
        """
        Create a new poll.
        
        Args:
            question: The poll question
            options: List of poll options
            creator_id: ID of the user who created the poll
            guild_id: ID of the guild where the poll is created
            poll_type: Type of the poll
            max_selections: Maximum number of options a user can select
            duration: Duration string like "5d", "24h", "30m"
            description: Optional description
            show_votes_while_active: Whether to show vote counts while the poll is active
            
        Returns:
            The created poll
            
        Raises:
            DatabaseError: If an error occurs while creating the poll
        """
        try:
            # Calculate end time from duration (with timezone awareness)
            end_time = datetime.now(timezone.utc) + self._parse_duration(duration)
            
            # Create the poll - TZDateTime will handle timezone conversion automatically
            poll = Poll(
                question=question,
                creator_id=int(creator_id),
                guild_id=guild_id,
                poll_type=poll_type,
                max_selections=max_selections,
                end_time=end_time,  # TZDateTime handles conversion automatically
                is_active=True,
                is_revealed=False
            )
            self.db.add(poll)
            await self.db.flush()  # Get the poll ID
            
            # Add options
            for i, option_text in enumerate(options):
                option = PollOption(
                    poll_id=poll.id,
                    text=option_text,
                    index=i
                )
                self.db.add(option)
            
            # Commit changes
            await self.db.flush()
            
            # Refresh the poll to get options
            await self.db.refresh(poll)
            
            # TZDateTime should ensure poll.end_time now has timezone info
            return poll
        except Exception as e:
            self.logger.error(f"Error creating poll: {e}", exc_info=True)
            raise DatabaseError(f"Failed to create poll: {str(e)}")

    def _parse_duration(self, duration: str) -> timedelta:
        """Parse duration string into timedelta."""
        try:
            # Handle case where the duration might be empty or None
            if not duration:
                # Default: 5 days
                return timedelta(days=5)
            
            value = int(duration[:-1])
            unit = duration[-1].lower()
            
            if unit == 'm':
                return timedelta(minutes=value)
            elif unit == 'h':
                return timedelta(hours=value)
            elif unit == 'd':
                return timedelta(days=value)
            else:
                raise ValueError(f"Invalid duration unit: {unit}")
        except Exception as e:
            self.logger.error(f"Error parsing duration '{duration}': {e}")
            # Default to 5 days on error rather than raising an exception
            return timedelta(days=5)

    async def add_selection(
        self,
        poll_id: int,
        user_id: str,
        selection: str
    ) -> UserPollSelection:
        """Add or update a user's selection for a poll."""
        try:
            self.logger.debug(f"Adding selection for poll {poll_id}, user {user_id}, selection {selection}")
            
            # Get poll with retry logic
            poll = None
            for attempt in range(3):
                try:
                    poll = await self.get_poll(poll_id)
                    if poll:
                        break
                    await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.debug(f"Attempt {attempt + 1} to get poll failed: {e}")
                    if attempt == 2:
                        raise
                    continue
                    
            if not poll:
                self.logger.warning(f"Poll {poll_id} not found")
                raise PollError("Poll not found")
                
            # Check if poll is open for voting
            now = datetime.now(timezone.utc)
            poll_end_time = self._ensure_timezone_aware(poll.end_time)
            
            if now > poll_end_time:
                self.logger.debug(f"Poll {poll_id} has ended")
                raise PollError("Poll has ended")
                
            if not poll.is_active:
                self.logger.debug(f"Poll {poll_id} is not active")
                raise PollError("Poll is not active")

            # Get user's existing selection with retry logic
            user_selection = None
            for attempt in range(3):
                try:
                    user_selection = await self._get_user_selection(poll_id, user_id)
                    break
                except Exception as e:
                    self.logger.debug(f"Attempt {attempt + 1} to get user selection failed: {e}")
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5)
                    continue
            
            if user_selection:
                self.logger.debug(f"Existing selections: {user_selection.selections}")
                current_selections = set(user_selection.selections)
                if selection in current_selections:
                    self.logger.debug(f"Removing selection {selection}")
                    current_selections.remove(selection)
                else:
                    if len(current_selections) >= poll.max_selections:
                        removed = next(iter(current_selections))
                        self.logger.debug(f"Max selections reached, removing {removed}")
                        current_selections.remove(removed)
                    self.logger.debug(f"Adding selection {selection}")
                    current_selections.add(selection)
                user_selection.selections = list(current_selections)
                self.logger.debug(f"Updated selections: {user_selection.selections}")
            else:
                self.logger.debug("Creating new user selection")
                user_selection = UserPollSelection(
                    poll_id=poll_id,
                    user_id=user_id,
                    selections=[selection]
                )
                self.db.add(user_selection)
                self.logger.debug("Added new selection to session")

            # Commit changes with retry logic
            for attempt in range(3):
                try:
                    await self.db.commit()
                    await self.db.refresh(user_selection)
                    self.logger.debug("Changes committed")
                    break
                except Exception as e:
                    self.logger.debug(f"Attempt {attempt + 1} to commit changes failed: {e}")
                    await self.db.rollback()
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5)
                    continue
            
            return user_selection
            
        except PollError:
            # Re-raise PollError without logging as error
            raise
        except Exception as e:
            self.logger.error(f"Error adding selection: {e}", exc_info=True)
            raise PollError("Failed to add selection")

    async def get_poll_with_refresh(self, poll_id: int) -> Optional[Poll]:
        """Get a poll by ID and refresh its state."""
        try:
            # Get poll with all relationships loaded
            stmt = select(Poll).where(Poll.id == poll_id).options(
                selectinload(Poll.options),
                selectinload(Poll.selections),
                selectinload(Poll.messages),
                selectinload(Poll.ui_states)
            )
            result = await self.db.execute(stmt)
            poll = result.scalar_one_or_none()

            if poll:
                # Refresh poll state
                now = datetime.utcnow()
                
                # DO NOT update active status based on end time
                # This was causing issues with the poll status management
                # poll.is_active = poll.end_time > now and not poll.is_revealed
                
                # Check for orphaned UI states (older than 24 hours)
                for ui_state in poll.ui_states:
                    if now - ui_state.last_interaction > timedelta(hours=24):
                        self.db.delete(ui_state)
                
                # Check for orphaned messages
                for message in poll.messages:
                    try:
                        # Only delete if we can verify the message doesn't exist
                        if not message.is_valid:
                            self.db.delete(message)
                    except Exception as e:
                        logger.warning(f"Error checking message validity: {e}")
                        continue
                
                try:
                    await self.db.commit()
                    await self.db.refresh(poll)
                except Exception as e:
                    logger.error(f"Error committing state refresh: {e}")
                    await self.db.rollback()
                
            return poll
            
        except Exception as e:
            logger.error(f"Error getting poll with refresh: {e}", exc_info=True)
            await self.db.rollback()
            return None

    async def update_poll_state(
        self,
        poll_id: int,
        new_status: PollStatus,
        validate_current_status: Optional[PollStatus] = None
    ) -> Poll:
        """Update the status of a poll."""
        # Define the nested update function to use within transaction
        async def _update():
            # Get the poll with locking
            poll_stmt = select(Poll).where(Poll.id == poll_id)
            result = await self.db.execute(poll_stmt)
            poll = result.scalar_one_or_none()
            
            if not poll:
                raise PollError(f"Poll {poll_id} not found")
                
            # Validate current status if specified
            if validate_current_status is not None and poll.status != validate_current_status:
                raise PollError(f"Poll {poll_id} is not in the expected state {validate_current_status}")
                
            # Update poll status
            if new_status == PollStatus.CLOSED:
                poll.is_active = False
                # Set end_time to current time if closing now
                now = datetime.now(timezone.utc)
                # TZDateTime will handle timezone conversion
                poll.end_time = now
            elif new_status == PollStatus.REVEALED:
                poll.is_active = False
                poll.is_revealed = True
                
            # Flush changes
            await self.db.flush()
            
            return poll
                
        # Execute the update within a transaction
        try:
            result = await _update()
            return result
        except Exception as e:
            self.logger.error(f"Error updating poll state: {e}", exc_info=True)
            raise PollError(f"Failed to update poll state: {str(e)}")

    async def close_poll(self, poll_id: int) -> Poll:
        """
        Close a poll.
        
        Args:
            poll_id: ID of the poll to close
            
        Returns:
            The updated poll
            
        Raises:
            PollError: If the poll is not found or cannot be closed
        """
        try:
            # Get poll
            stmt = select(Poll).where(Poll.id == poll_id)
            result = await self.db.execute(stmt)
            poll = result.scalar_one_or_none()
            
            if not poll:
                raise PollError(f"Poll {poll_id} not found")
                
            if poll.is_revealed:
                raise PollError(f"Poll {poll_id} has already been revealed and cannot be closed")
                
            # Update poll status to closed
            poll.is_active = False
            
            # Set end_time to current time if it hasn't ended yet
            now = datetime.now(timezone.utc)
            if poll.end_time > now:
                # TZDateTime will handle timezone conversion
                poll.end_time = now
            
            # Commit changes
            await self.db.flush()
            
            return poll
        except PollError as e:
            # Re-raise specific poll errors
            raise
        except Exception as e:
            self.logger.error(f"Error closing poll: {e}", exc_info=True)
            raise PollError(f"Failed to close poll: {str(e)}")

    async def reveal_poll(self, poll_id: int, correct_answers: List[str]) -> Poll:
        """
        Reveal a poll's correct answers.
        
        Args:
            poll_id: ID of the poll to reveal
            correct_answers: List of correct answer indices
            
        Returns:
            The updated poll
            
        Raises:
            PollError: If the poll is not found or cannot be revealed
        """
        try:
            # Get poll
            stmt = select(Poll).where(Poll.id == poll_id)
            result = await self.db.execute(stmt)
            poll = result.scalar_one_or_none()
            
            if not poll:
                raise PollError(f"Poll {poll_id} not found")
                
            if poll.is_revealed:
                raise PollError(f"Poll {poll_id} has already been revealed")
                
            # Ensure poll is closed before revealing
            if poll.is_active:
                # Close the poll first
                poll.is_active = False
                
                # Set end_time to current time if it hasn't ended yet
                now = datetime.now(timezone.utc)
                if poll.end_time > now:
                    # TZDateTime will handle timezone conversion
                    poll.end_time = now
            
            # Update poll with correct answers and set to revealed
            poll.correct_answers = correct_answers
            poll.is_revealed = True
            
            # Commit changes
            await self.db.flush()
            
            return poll
        except PollError as e:
            # Re-raise specific poll errors
            raise
        except Exception as e:
            self.logger.error(f"Error revealing poll: {e}", exc_info=True)
            raise PollError(f"Failed to reveal poll: {str(e)}")

    async def get_poll(self, poll_id: int) -> Optional[Poll]:
        """Get a poll by ID with its options loaded."""
        stmt = select(Poll).options(selectinload(Poll.options)).where(Poll.id == poll_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_user_selection(
        self,
        poll_id: int,
        user_id: str
    ) -> Optional[UserPollSelection]:
        """Get a user's selection for a poll."""
        query = select(UserPollSelection).where(
            UserPollSelection.poll_id == poll_id,
            UserPollSelection.user_id == user_id
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def get_expired_polls(self) -> List[Poll]:
        """Get all expired but still open polls."""
        try:
            now = datetime.now(timezone.utc)
            
            # With TZDateTime, we can use the ORM directly again
            query = select(Poll).where(
                and_(
                    Poll.is_active == True,
                    Poll.is_revealed == False,
                    Poll.end_time <= now
                )
            )
            result = await self.db.execute(query)
            polls = result.scalars().all()
            
            return polls
        except Exception as e:
            self.logger.error(f"Error getting expired polls: {e}", exc_info=True)
            raise PollError("Failed to get expired polls")

    async def get_latest_poll(self, include_closed: bool = False) -> Optional[Poll]:
        """Get the most recent poll.
        
        Args:
            include_closed: If True, also include recently closed polls
        """
        try:
            logger.debug(f"Getting latest poll (include_closed={include_closed})")
            
            conditions = []
            if not include_closed:
                conditions.append(Poll.is_active == True)
            
            stmt = (
                select(Poll)
                .options(selectinload(Poll.options))
                .where(*conditions)
                .order_by(desc(Poll.created_at))
                .limit(1)
            )
            logger.debug(f"Query: {stmt}")
            result = await self.db.execute(stmt)
            poll = result.scalar_one_or_none()
            
            if poll:
                logger.debug(f"Found poll ID: {poll.id}, created at: {poll.created_at}, is_active: {poll.is_active}")
            else:
                logger.debug("No poll found")
            return poll
        except Exception as e:
            logger.error(f"Error getting latest poll: {e}", exc_info=True)
            raise
        
    async def update_user_selection(
        self,
        poll_id: int,
        user_id: str,
        selections: List[str]
    ) -> UserPollSelection:
        """Update a user's poll selection."""
        try:
            # Check if user already has a selection
            query = select(UserPollSelection).where(
                and_(
                    UserPollSelection.poll_id == poll_id,
                    UserPollSelection.user_id == user_id
                )
            )
            result = await self.db.execute(query)
            user_selection = result.scalar_one_or_none()

            if user_selection:
                user_selection.selections = selections
            else:
                user_selection = UserPollSelection(
                    poll_id=poll_id,
                    user_id=user_id,
                    selections=selections
                )
                self.db.add(user_selection)

            await self.db.commit()
            await self.db.refresh(user_selection)
            return user_selection

        except Exception as e:
            logger.error(f"Error updating user selection: {e}")
            await self.db.rollback()
            raise PollError("Failed to update user selection")

    async def get_active_poll(self, poll_type: str) -> Optional[Poll]:
        """
        Get the active poll of a specific type.
        
        An active poll is one that is not revealed and is still ongoing.
        
        Args:
            poll_type: The type of poll to get
            
        Returns:
            The active poll or None if no active poll exists
        """
        try:
            # For backward compatibility, check both fields (status and is_active)
            stmt = (
                select(Poll)
                .where(Poll.poll_type == poll_type)
                .where(
                    or_(
                        Poll.status == PollStatus.OPEN,
                        Poll.is_active == True
                    )
                )
                .options(selectinload(Poll.options))
            )
            result = await self.db.execute(stmt)
            poll = result.scalars().first()
            
            if not poll:
                return None
            
            # Get poll message info
            message_info = await self.get_poll_message(poll.id)
            if message_info:
                poll.channel_id, poll.message_id = message_info
            
            return poll
        except Exception as e:
            self.logger.error(f"Error getting active poll: {e}", exc_info=True)
            raise DatabaseError(f"Failed to get active poll: {str(e)}")

    async def close_all_polls_except(self, except_poll_id: Optional[int] = None) -> None:
        """Close all active polls except the specified one."""
        try:
            logger.debug(f"Closing all polls except ID: {except_poll_id}")
            stmt = (
                update(Poll)
                .where(
                    and_(
                        Poll.is_active == True,
                        Poll.id != except_poll_id if except_poll_id else True
                    )
                )
                .values(is_active=False)
            )
            logger.debug(f"Query: {stmt}")
            await self.db.execute(stmt)
            await self.db.commit()
            logger.debug("Successfully closed old polls")
        except Exception as e:
            logger.error(f"Error closing old polls: {e}", exc_info=True)
            await self.db.rollback()
            raise

    async def get_active_polls(self) -> List[Poll]:
        """Get all currently active polls."""
        try:
            stmt = (
                select(Poll)
                .options(selectinload(Poll.options))
                .where(
                    and_(
                        Poll.is_active == True,
                        Poll.end_time > datetime.utcnow()
                    )
                )
            )
            result = await self.db.execute(stmt)
            polls = result.scalars().all()
            logger.debug(f"Found {len(polls)} active polls")
            return polls
        except Exception as e:
            logger.error(f"Error getting active polls: {e}", exc_info=True)
            raise PollError("Failed to get active polls")

    async def get_latest_poll_in_channel(
        self,
        channel_id: int,
        include_closed: bool = False
    ) -> Optional[Poll]:
        """Get the latest poll in a channel."""
        stmt = select(Poll).where(Poll.channel_id == channel_id)
        
        if not include_closed:
            stmt = stmt.where(Poll.status == PollStatus.OPEN)
            
        stmt = stmt.order_by(desc(Poll.created_at))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def close_all_polls_in_channel(self, channel_id: int) -> None:
        """Close all active polls in a channel."""
        logger.debug(f"Closing all polls in channel {channel_id}")
        stmt = select(Poll).where(
            and_(
                Poll.channel_id == channel_id,
                Poll.is_active == True
            )
        )
        result = await self.db.execute(stmt)
        polls = result.scalars().all()
        logger.debug(f"Found {len(polls)} active polls to close")
        
        for poll in polls:
            poll.is_active = False
            poll.end_time = datetime.utcnow()
            logger.debug(f"Closed poll {poll.id}")

    async def record_vote(
        self,
        poll_id: int,
        user_id: int,
        selected_options: List[str]
    ) -> None:
        """Record a user's vote for a poll."""
        poll = await self.get_poll(poll_id)
        if not poll:
            raise PollError("Poll not found")
        
        if poll.status != PollStatus.OPEN:
            raise PollError("Poll is not open")
            
        if len(selected_options) > poll.max_selections:
            raise PollError(f"Maximum {poll.max_selections} selections allowed")
            
        if not all(option in poll.options for option in selected_options):
            raise PollError("Invalid option selected")
        
        # Create or update user score
        stmt = select(UserScore).where(
            and_(
                UserScore.poll_id == poll_id,
                UserScore.user_id == user_id,
                UserScore.guild_id == poll.guild_id
            )
        )
        result = await self.db.execute(stmt)
        user_score = result.scalar_one_or_none()
        
        if user_score:
            user_score.selected_options = selected_options
            user_score.last_updated = datetime.utcnow()
        else:
            user_score = UserScore(
                poll_id=poll_id,
                user_id=user_id,
                guild_id=poll.guild_id,
                selected_options=selected_options
            )
            self.db.add(user_score)

    async def get_poll_results(self, poll_id: int) -> dict:
        """Get the results of a poll."""
        poll = await self.get_poll(poll_id)
        if not poll:
            raise PollError("Poll not found")
        
        stmt = select(UserScore).where(UserScore.poll_id == poll_id)
        result = await self.db.execute(stmt)
        scores = result.scalars().all()
        
        # Count votes for each option
        vote_counts = {option: 0 for option in poll.options}
        for score in scores:
            for option in score.selected_options:
                vote_counts[option] += 1
                
        return {
            "total_votes": len(scores),
            "vote_counts": vote_counts,
            "correct_answers": poll.correct_answers if poll.status == PollStatus.REVEALED else None
        }

    async def close_all_polls_of_type(self, guild_id: int, poll_type: str) -> None:
        """Close all active polls of a specific type in a guild."""
        try:
            stmt = (
                update(Poll)
                .where(
                    and_(
                        Poll.guild_id == guild_id,
                        Poll.poll_type == poll_type,
                        Poll.is_active == True
                    )
                )
                .values(is_active=False)
            )
            await self.db.execute(stmt)
            await self.db.commit()
        except Exception as e:
            self.logger.error(f"Error closing polls: {e}", exc_info=True)
            await self.db.rollback()
            raise PollError(f"Failed to close polls: {str(e)}")

    async def get_latest_poll_of_type(
        self,
        guild_id: int,
        poll_type: str,
        include_closed: bool = False
    ) -> Optional[Poll]:
        """
        Get the latest poll of a specific type in a guild.
        
        Args:
            guild_id: ID of the guild
            poll_type: Type of the poll
            include_closed: Whether to include closed polls
            
        Returns:
            The latest poll of the given type, or None if not found
        """
        try:
            stmt = (
                select(Poll)
                .where(Poll.guild_id == guild_id)
                .where(Poll.poll_type == poll_type)
            )
            
            if not include_closed:
                stmt = stmt.where(Poll.is_active == True)
            
            stmt = stmt.order_by(Poll.created_at.desc()).limit(1)
            
            result = await self.db.execute(stmt)
            poll = result.scalars().first()
            
            # Ensure poll end_time has timezone information
            if poll and poll.end_time and poll.end_time.tzinfo is None:
                poll.end_time = poll.end_time.replace(tzinfo=timezone.utc)
            
            return poll
        except Exception as e:
            self.logger.error(f"Error getting latest poll of type {poll_type}: {e}", exc_info=True)
            return None

    async def get_latest_poll_of_type_any_status(
        self,
        guild_id: int,
        poll_type: str
    ) -> Optional[Poll]:
        """
        Get the latest poll of a specific type in a guild, regardless of its status.
        
        Args:
            guild_id: ID of the guild
            poll_type: Type of the poll
            
        Returns:
            The latest poll of the given type, or None if not found
        """
        try:
            stmt = (
                select(Poll)
                .where(Poll.guild_id == guild_id)
                .where(Poll.poll_type == poll_type)
                .order_by(Poll.created_at.desc())
                .limit(1)
            )
            
            result = await self.db.execute(stmt)
            poll = result.scalars().first()
            
            # Ensure poll end_time has timezone information
            if poll and poll.end_time and poll.end_time.tzinfo is None:
                poll.end_time = poll.end_time.replace(tzinfo=timezone.utc)
            
            return poll
        except Exception as e:
            self.logger.error(f"Error getting latest poll of type {poll_type} (any status): {e}", exc_info=True)
            return None

    async def get_active_polls_by_type(self, guild_id: int, poll_type: str) -> List[Poll]:
        """Get all active polls for a specific poll type in a guild."""
        try:
            # Query the database
            stmt = select(Poll).where(
                and_(
                    Poll.guild_id == guild_id,
                    Poll.poll_type == poll_type,
                    Poll.is_active == True
                )
            )
            result = await self.db.execute(stmt)
            polls = result.scalars().all()
            
            return polls
        except Exception as e:
            self.logger.error(f"Error getting active polls by type: {e}", exc_info=True)
            raise DatabaseError(f"Failed to get active polls: {str(e)}")
            
    async def register_poll_message(self, poll_id: int, channel_id: int, message_id: int) -> None:
        """Register a message as a poll message for updating."""
        try:
            # Check if the message is already registered
            stmt = select(PollMessage).where(
                and_(
                    PollMessage.poll_id == poll_id,
                    PollMessage.channel_id == channel_id,
                    PollMessage.message_id == message_id
                )
            )
            result = await self.db.execute(stmt)
            existing = result.scalar_one_or_none()
            
            if existing:
                return
                
            # Create new poll message record
            poll_message = PollMessage(
                poll_id=poll_id,
                channel_id=channel_id,
                message_id=message_id
            )
            
            self.db.add(poll_message)
            await self.db.flush()
            
        except Exception as e:
            self.logger.error(f"Error registering poll message: {e}", exc_info=True)
            raise DatabaseError(f"Failed to register poll message: {str(e)}")
            
    async def get_poll_messages(self, poll_id: int) -> List:
        """Get all messages associated with a poll."""
        try:
            # Query the database
            stmt = select(PollMessage).where(PollMessage.poll_id == poll_id)
            result = await self.db.execute(stmt)
            messages = result.scalars().all()
            
            return messages
        except Exception as e:
            self.logger.error(f"Error getting poll messages: {e}", exc_info=True)
            raise DatabaseError(f"Failed to get poll messages: {str(e)}")
            
    async def get_all_active_polls(self) -> List[Poll]:
        """Get all active polls from all guilds."""
        try:
            stmt = select(Poll).where(Poll.is_active == True)
            result = await self.db.execute(stmt)
            polls = result.scalars().all()
            
            # Ensure all poll end_times have timezone information
            for poll in polls:
                if poll.end_time and poll.end_time.tzinfo is None:
                    poll.end_time = poll.end_time.replace(tzinfo=timezone.utc)
                
            return polls
        except Exception as e:
            self.logger.error(f"Error getting all active polls: {e}", exc_info=True)
            return []
            
    async def get_votes_per_option(self, poll_id: int) -> Dict[int, int]:
        """
        Get the number of votes for each option in a poll.
        
        Args:
            poll_id: The ID of the poll
            
        Returns:
            A dictionary mapping option IDs to vote counts
            
        Raises:
            DatabaseError: If an error occurs while getting the votes
        """
        try:
            # Get all votes for this poll
            stmt = select(Vote).where(Vote.poll_id == poll_id)
            result = await self.db.execute(stmt)
            votes = result.scalars().all()
            
            # Count votes for each option
            votes_per_option = {}
            for vote in votes:
                for option_id in vote.option_ids:
                    if option_id in votes_per_option:
                        votes_per_option[option_id] += 1
                    else:
                        votes_per_option[option_id] = 1
            
            return votes_per_option
        except Exception as e:
            self.logger.error(f"Error getting votes per option: {e}", exc_info=True)
            raise DatabaseError(f"Failed to get votes per option: {str(e)}")

    async def get_user_selections(self, poll_id: int, user_id: str) -> List:
        """
        Get a user's selections for a poll.
        
        Args:
            poll_id: The ID of the poll
            user_id: The ID of the user
            
        Returns:
            A list of the user's selections
            
        Raises:
            DatabaseError: If an error occurs while getting the selections
        """
        try:
            # Query the database with explicit column selection to avoid missing column errors
            stmt = select(
                UserPollSelection.id,
                UserPollSelection.poll_id,
                UserPollSelection.user_id,
                UserPollSelection.selections,
                UserPollSelection.created_at,
                UserPollSelection.updated_at
            ).where(
                and_(
                    UserPollSelection.poll_id == poll_id,
                    UserPollSelection.user_id == user_id
                )
            )
            result = await self.db.execute(stmt)
            selections = result.all()
            
            # Convert the result to UserPollSelection-like objects
            selection_objects = []
            for row in selections:
                selection_dict = {
                    'id': row.id,
                    'poll_id': row.poll_id,
                    'user_id': row.user_id,
                    'selections': row.selections,
                    'created_at': row.created_at,
                    'updated_at': row.updated_at
                }
                selection_objects.append(selection_dict)
            
            return selection_objects
        except Exception as e:
            self.logger.error(f"Error getting user selections: {e}", exc_info=True)
            raise DatabaseError(f"Failed to get user selections: {str(e)}")
            
    async def register_vote(self, poll_id: int, user_id: str, option_indices: List[int]) -> None:
        """
        Register a user's vote with their selected options.
        
        Args:
            poll_id: The ID of the poll
            user_id: The ID of the user
            option_indices: The indices of the options the user selected
            
        Raises:
            DatabaseError: If an error occurs while registering the vote
        """
        try:
            # First, remove any existing selections
            delete_stmt = delete(UserPollSelection).where(
                and_(
                    UserPollSelection.poll_id == poll_id,
                    UserPollSelection.user_id == user_id
                )
            )
            await self.db.execute(delete_stmt)
            
            # Now add the new selections using direct SQL to avoid any issues with option_index
            # Create the current timestamp
            now = datetime.utcnow()
            
            # Convert the Python list to a JSON string for PostgreSQL
            selections_json = json.dumps(option_indices)
            
            # Insert with only the columns that exist in the database
            query = text("""
            INSERT INTO user_poll_selections 
                (poll_id, user_id, selections, created_at, updated_at) 
            VALUES 
                (:poll_id, :user_id, :selections, :created_at, :updated_at)
            """)
            
            await self.db.execute(
                query, 
                {
                    "poll_id": poll_id,
                    "user_id": user_id,
                    "selections": selections_json,  # Use the JSON string instead of the Python list
                    "created_at": now,
                    "updated_at": now
                }
            )
            
            await self.db.flush()
            
        except Exception as e:
            self.logger.error(f"Error registering vote: {e}", exc_info=True)
            raise DatabaseError(f"Failed to register vote: {str(e)}")

    async def get_poll_message(self, poll_id: int) -> Optional[Tuple[int, int]]:
        """
        Get the channel ID and message ID for a poll.
        
        Args:
            poll_id: The ID of the poll
            
        Returns:
            A tuple of (channel_id, message_id) if the poll has a message,
            or None if it does not.
        """
        try:
            stmt = (
                select(PollMessage)
                .where(PollMessage.poll_id == poll_id)
                .where(PollMessage.message_type == "poll")
            )
            result = await self.db.execute(stmt)
            poll_message = result.scalars().first()
            
            if poll_message:
                return (poll_message.channel_id, poll_message.message_id)
            
            # Fallback for polls before PollMessage table was added
            # Try to get from Poll table
            stmt = (
                select(Poll.channel_id)
                .where(Poll.id == poll_id)
            )
            result = await self.db.execute(stmt)
            poll = result.scalars().first()
            
            if poll and poll.channel_id:
                # We have the channel but not the message ID
                # This is a partial result before the new table was added
                return (int(poll.channel_id), None)
                
            return None
        except Exception as e:
            self.logger.error(f"Error getting poll message: {e}", exc_info=True)
            return None

    # Add new utility method for direct end_time updates
    async def update_poll_end_time(self, poll_id: int, end_time: datetime) -> None:
        """
        Update a poll's end time directly using SQL to avoid timezone issues.
        
        Args:
            poll_id: The ID of the poll
            end_time: The new end time (timezone-aware or naive)
            
        Raises:
            DatabaseError: If an error occurs while updating the end time
        """
        try:
            # Convert to naive UTC datetime for database storage
            db_safe_end_time = self._make_db_safe_datetime(end_time)
            
            # Use direct SQL to update the end_time
            stmt = text(
                "UPDATE polls SET end_time = :end_time WHERE id = :poll_id"
            ).bindparams(
                end_time=db_safe_end_time,
                poll_id=poll_id
            )
            
            await self.db.execute(stmt)
            await self.db.flush()
            
            self.logger.debug(f"Updated poll {poll_id} end_time to {db_safe_end_time}")
        except Exception as e:
            self.logger.error(f"Error updating poll end time: {e}", exc_info=True)
            raise DatabaseError(f"Failed to update poll end time: {str(e)}")