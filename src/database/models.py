from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import Column, BigInteger, String, DateTime, Boolean, ForeignKey, JSON, Enum as SQLEnum, Integer, UniqueConstraint, TypeDecorator
from sqlalchemy.orm import relationship
import enum

from .database import Base

# Custom DateTime type that automatically strips timezone info
class TZDateTime(TypeDecorator):
    impl = DateTime
    
    def process_bind_param(self, value, dialect):
        """Convert timezone-aware datetime to naive UTC before saving to DB."""
        if value is not None and value.tzinfo is not None:
            # Convert to UTC then remove tzinfo
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
        
    def process_result_value(self, value, dialect):
        """Add UTC timezone info to naive datetimes retrieved from DB."""
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

class PollStatus(enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    REVEALED = "revealed"

class AdminRole(Base):
    __tablename__ = "polls_admin_roles"
    
    guild_id = Column(BigInteger, ForeignKey("polls_guilds.guild_id", ondelete="CASCADE"), primary_key=True)
    poll_type = Column(String, primary_key=True)
    role_id = Column(BigInteger, nullable=False)
    
    # Relationships
    guild = relationship("Guild", back_populates="admin_roles")

class Guild(Base):
    __tablename__ = "polls_guilds"

    guild_id = Column(BigInteger, primary_key=True)
    name = Column(String, nullable=False)
    joined_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    
    # Relationships
    polls = relationship("Poll", back_populates="guild")
    user_scores = relationship("UserScore", back_populates="guild")
    admin_roles = relationship("AdminRole", back_populates="guild")
    poll_type_leaderboards = relationship("PollTypeLeaderboard", back_populates="guild")

class Poll(Base):
    __tablename__ = "polls_polls"

    id = Column(BigInteger, primary_key=True)
    poll_type = Column(String, nullable=False)  # Corresponds to the poll config type
    question = Column(String, nullable=False)
    creator_id = Column(BigInteger, nullable=False)
    guild_id = Column(BigInteger, ForeignKey("polls_guilds.guild_id", ondelete="CASCADE"), nullable=False)
    max_selections = Column(BigInteger, default=1)
    end_time = Column(TZDateTime, nullable=False)  # Using TZDateTime instead of DateTime
    created_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)
    is_revealed = Column(Boolean, default=False)
    correct_answers = Column(JSON, nullable=True)
    # channel_id is now optional - it's kept for backward compatibility
    channel_id = Column(BigInteger, nullable=True)
    description = Column(String, nullable=True)
    show_votes_while_active = Column(Boolean, default=False, nullable=False)

    # Relationships
    options = relationship("PollOption", back_populates="poll", cascade="all, delete-orphan")
    selections = relationship("UserPollSelection", back_populates="poll", cascade="all, delete-orphan")
    guild = relationship("Guild", back_populates="polls")
    messages = relationship("PollMessage", back_populates="poll", cascade="all, delete-orphan")
    ui_states = relationship("UIState", back_populates="poll", cascade="all, delete-orphan")
    votes = relationship("Vote", back_populates="poll", cascade="all, delete-orphan")

    # Note: The unique constraint is handled by a partial index in the database
    # CREATE UNIQUE INDEX uq_active_poll_per_guild_type ON polls_polls (guild_id, poll_type) WHERE is_active = true;

    @property
    def status(self) -> PollStatus:
        """Get the status of the poll."""
        if self.is_revealed:
            return PollStatus.REVEALED
        elif not self.is_active:
            return PollStatus.CLOSED
        else:
            return PollStatus.OPEN

class PollOption(Base):
    __tablename__ = "polls_poll_options"

    id = Column(BigInteger, primary_key=True)
    poll_id = Column(BigInteger, ForeignKey("polls_polls.id", ondelete="CASCADE"), nullable=False)
    text = Column(String, nullable=False)
    index = Column(Integer, nullable=False, default=0)
    
    # Relationships
    poll = relationship("Poll", back_populates="options")

class UserPollSelection(Base):
    __tablename__ = "polls_user_poll_selections"

    id = Column(BigInteger, primary_key=True)
    poll_id = Column(BigInteger, ForeignKey("polls_polls.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, nullable=False)
    # option_index column is removed since it doesn't exist in the database
    selections = Column(JSON, nullable=False)  # List of selected options
    created_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    poll = relationship("Poll", back_populates="selections")

class UserScore(Base):
    __tablename__ = "polls_user_scores"

    user_id = Column(String, primary_key=True)
    guild_id = Column(BigInteger, ForeignKey("polls_guilds.guild_id", ondelete="CASCADE"), primary_key=True)
    poll_type = Column(String, primary_key=True)
    points = Column(Integer, default=0)
    total_correct = Column(BigInteger, default=0)
    created_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    guild = relationship("Guild", back_populates="user_scores")

class PollTypeLeaderboard(Base):
    __tablename__ = "polls_poll_type_leaderboards"

    id = Column(BigInteger, primary_key=True)
    guild_id = Column(BigInteger, ForeignKey("polls_guilds.guild_id", ondelete="CASCADE"), nullable=False)
    poll_type = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    points = Column(Integer, default=0)
    total_correct = Column(Integer, default=0)
    rank = Column(Integer, nullable=False)
    last_updated = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    guild = relationship("Guild", back_populates="poll_type_leaderboards")

    __table_args__ = (
        UniqueConstraint('guild_id', 'poll_type', 'user_id', name='unique_guild_poll_type_user_leaderboard'),
    )

class PollMessage(Base):
    __tablename__ = "polls_poll_messages"

    id = Column(BigInteger, primary_key=True)
    poll_id = Column(BigInteger, ForeignKey("polls_polls.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    message_type = Column(String, nullable=False, default='poll')
    created_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    poll = relationship("Poll", back_populates="messages")

    @property
    def is_valid(self) -> bool:
        """Check if the message is still valid."""
        return bool(self.message_id and self.channel_id)

class UIState(Base):
    """Tracks UI state for polls."""
    __tablename__ = "polls_ui_states"

    id = Column(BigInteger, primary_key=True)
    poll_id = Column(BigInteger, ForeignKey("polls_polls.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, nullable=False)
    state_data = Column(JSON, nullable=False)  # Stores button states, selections, etc.
    last_interaction = Column(TZDateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    created_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    poll = relationship("Poll", back_populates="ui_states")
class Vote(Base):
    """Represents a user's vote on a poll with their selected options."""
    __tablename__ = "polls_votes"

    id = Column(BigInteger, primary_key=True)
    poll_id = Column(BigInteger, ForeignKey("polls_polls.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, nullable=False)
    option_ids = Column(JSON, nullable=False)  # List of selected option IDs
    created_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(TZDateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    poll = relationship("Poll", back_populates="votes")

    __table_args__ = (
        UniqueConstraint('poll_id', 'user_id', name='unique_user_poll_vote'),
    )

