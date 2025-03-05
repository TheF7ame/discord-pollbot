from typing import Optional, List, Dict
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, desc, func, delete
from datetime import datetime

from src.database.models import Guild, PollTypeLeaderboard, UserScore, AdminRole, Poll
from src.utils.exceptions import GuildError

logger = logging.getLogger(__name__)

class GuildService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = logging.getLogger(__name__)

    async def register_guild(self, guild_id: int, guild_name: str) -> Guild:
        """Register a new guild or update existing one."""
        try:
            stmt = select(Guild).where(Guild.guild_id == guild_id)
            result = await self.session.execute(stmt)
            guild = result.scalar_one_or_none()

            if guild:
                guild.name = guild_name
                guild.is_active = True
                guild.last_updated = datetime.utcnow()
            else:
                guild = Guild(
                    guild_id=guild_id,
                    name=guild_name,
                    is_active=True
                )
                self.session.add(guild)

            await self.session.commit()
            return guild

        except Exception as e:
            logger.error(f"Error registering guild: {e}", exc_info=True)
            await self.session.rollback()
            raise GuildError(f"Failed to register guild: {str(e)}")

    async def deactivate_guild(self, guild_id: int) -> None:
        """Deactivate a guild when bot leaves."""
        try:
            stmt = (
                update(Guild)
                .where(Guild.guild_id == guild_id)
                .values(is_active=False, last_updated=datetime.utcnow())
            )
            await self.session.execute(stmt)
            await self.session.commit()
        except Exception as e:
            logger.error(f"Error deactivating guild: {e}", exc_info=True)
            await self.session.rollback()
            raise GuildError(f"Failed to deactivate guild: {str(e)}")

    async def update_guild_leaderboard(self, guild_id: int) -> None:
        """Update the cached leaderboard for a guild."""
        try:
            # Get all user scores for the guild
            stmt = (
                select(UserScore)
                .where(UserScore.guild_id == guild_id)
                .order_by(desc(UserScore.points))
            )
            result = await self.session.execute(stmt)
            scores = result.scalars().all()

            # Group scores by poll_type
            poll_type_scores = {}
            for score in scores:
                poll_type = score.poll_type
                if poll_type not in poll_type_scores:
                    poll_type_scores[poll_type] = []
                poll_type_scores[poll_type].append(score)

            # Update leaderboard for each poll_type
            for poll_type, scores in poll_type_scores.items():
                # Delete existing leaderboard entries for this poll_type in this guild
                await self.session.execute(
                    delete(PollTypeLeaderboard).where(
                        and_(
                            PollTypeLeaderboard.guild_id == guild_id,
                            PollTypeLeaderboard.poll_type == poll_type
                        )
                    )
                )

                # Create new leaderboard entries
                for rank, score in enumerate(scores, 1):
                    leaderboard_entry = PollTypeLeaderboard(
                        guild_id=guild_id,
                        poll_type=poll_type,
                        user_id=score.user_id,
                        points=score.points,
                        total_correct=score.total_correct,
                        rank=rank,
                        last_updated=datetime.utcnow()
                    )
                    self.session.add(leaderboard_entry)

            await self.session.commit()
            logger.info(f"Updated leaderboards for guild {guild_id}")

        except Exception as e:
            logger.error(f"Error updating guild leaderboard: {e}", exc_info=True)
            await self.session.rollback()
            raise GuildError(f"Failed to update guild leaderboard: {str(e)}")

    async def get_guild_leaderboard(self, guild_id: int, poll_type: str, limit: int = 10) -> List[Dict]:
        """Get the cached leaderboard for a guild's poll type."""
        try:
            # Get leaderboard for the specified poll_type in this guild
            stmt = (
                select(PollTypeLeaderboard)
                .where(
                    and_(
                        PollTypeLeaderboard.guild_id == guild_id,
                        PollTypeLeaderboard.poll_type == poll_type
                    )
                )
                .order_by(PollTypeLeaderboard.rank)
                .limit(limit)
            )
            result = await self.session.execute(stmt)
            entries = result.scalars().all()

            return [
                {
                    "user_id": entry.user_id,
                    "total_points": entry.points,
                    "total_correct": entry.total_correct,
                    "rank": entry.rank,
                    "poll_type": entry.poll_type
                }
                for entry in entries
            ]

        except Exception as e:
            logger.error(f"Error getting guild leaderboard: {e}", exc_info=True)
            raise GuildError(f"Failed to get guild leaderboard: {str(e)}")

    async def get_user_rank(self, guild_id: int, poll_type: str, user_id: str) -> Optional[int]:
        """Get a user's rank from the cached leaderboard for a specific poll type."""
        try:
            stmt = select(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type,
                    PollTypeLeaderboard.user_id == user_id
                )
            )
            result = await self.session.execute(stmt)
            entry = result.scalar_one_or_none()
            return entry.rank if entry else None

        except Exception as e:
            logger.error(f"Error getting user rank: {e}", exc_info=True)
            return None

    async def get_or_create_guild(self, guild_id: int, guild_name: str = "Unknown") -> Guild:
        """Get or create a guild record."""
        try:
            stmt = select(Guild).where(Guild.guild_id == guild_id)
            result = await self.session.execute(stmt)
            guild = result.scalar_one_or_none()

            if not guild:
                guild = Guild(
                    guild_id=guild_id,
                    name=guild_name
                )
                self.session.add(guild)
                await self.session.flush()
                self.logger.info(f"Created new guild record for {guild_id}")

            return guild
        except Exception as e:
            self.logger.error(f"Error in get_or_create_guild: {e}", exc_info=True)
            raise GuildError(f"Failed to get or create guild: {str(e)}", guild_id)

    async def set_admin_role(self, guild_id: int, poll_type: str, role_id: int) -> AdminRole:
        """Set or update the admin role for a poll type in a guild."""
        try:
            stmt = select(AdminRole).where(
                and_(
                    AdminRole.guild_id == guild_id,
                    AdminRole.poll_type == poll_type
                )
            )
            result = await self.session.execute(stmt)
            admin_role = result.scalar_one_or_none()

            if admin_role:
                admin_role.role_id = role_id
            else:
                admin_role = AdminRole(
                    guild_id=guild_id,
                    poll_type=poll_type,
                    role_id=role_id
                )
                self.session.add(admin_role)

            await self.session.flush()
            self.logger.info(
                f"Set admin role {role_id} for poll type {poll_type} "
                f"in guild {guild_id}"
            )
            return admin_role

        except Exception as e:
            self.logger.error(f"Error in set_admin_role: {e}", exc_info=True)
            raise GuildError(
                f"Failed to set admin role: {str(e)}",
                guild_id
            )

    async def get_admin_role(
        self,
        guild_id: int,
        poll_type: str
    ) -> Optional[AdminRole]:
        """Get the admin role for a poll type in a guild."""
        try:
            stmt = select(AdminRole).where(
                and_(
                    AdminRole.guild_id == guild_id,
                    AdminRole.poll_type == poll_type
                )
            )
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except Exception as e:
            self.logger.error(f"Error in get_admin_role: {e}", exc_info=True)
            return None

    async def get_guild_admin_roles(self, guild_id: int) -> List[AdminRole]:
        """Get all admin roles for a guild."""
        try:
            stmt = select(AdminRole).where(AdminRole.guild_id == guild_id)
            result = await self.session.execute(stmt)
            return list(result.scalars().all())
        except Exception as e:
            self.logger.error(f"Error in get_guild_admin_roles: {e}", exc_info=True)
            return [] 