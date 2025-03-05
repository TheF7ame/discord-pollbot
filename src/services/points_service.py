from typing import List, Dict, Optional
from datetime import datetime, timezone
import logging
from sqlalchemy import select, and_, func, desc, delete, case, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects import postgresql

from src.database.models import Poll, UserScore, PollTypeLeaderboard, PollStatus, UserPollSelection, Vote
from src.utils.exceptions import PollError

logger = logging.getLogger(__name__)

class PointsService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = logging.getLogger(__name__)

    async def calculate_poll_points(self, poll_id: int):
        """Calculate points for users who participated in a poll."""
        self.logger.info(f"Calculating points for poll {poll_id}")
        
        # Get poll with correct answers
        try:
            poll_stmt = select(Poll).where(Poll.id == poll_id)
            result = await self.session.execute(poll_stmt)
            poll = result.scalar_one_or_none()
        except Exception as e:
            self.logger.error(f"Database error getting poll: {e}", exc_info=True)
            await self.session.rollback()
            return []
        
        if not poll:
            self.logger.error(f"Poll {poll_id} not found")
            return []
            
        if not poll.is_revealed:
            self.logger.error(f"Poll {poll_id} is not revealed yet")
            return []
            
        # Ensure poll.correct_answers is not None
        if poll.correct_answers is None:
            self.logger.warning(f"Poll {poll_id} has no correct answers defined")
            poll.correct_answers = []
            
        # Log correct answers for debugging
        self.logger.info(f"Poll {poll_id} correct_answers: {poll.correct_answers} (type: {type(poll.correct_answers)})")

        points_updates = []
        # Track user points to update leaderboard directly
        user_points = {}
        
        # Try to get votes from both tables to ensure we capture all votes
        try:
            # First try the Vote table
            stmt = select(Vote).where(Vote.poll_id == poll_id)
            result = await self.session.execute(stmt)
            votes = result.scalars().all()
            self.logger.info(f"Found {len(votes)} votes in Vote table for poll {poll_id}")
            
            # If we have votes in the Vote table, process them
            if votes:
                # Convert correct answers to a set of strings for consistent comparison
                correct_set = set(str(x) for x in poll.correct_answers)
                
                for vote in votes:
                    try:
                        user_id = vote.user_id
                        # Convert vote options to strings for consistent comparison
                        user_set = set(str(x) for x in vote.option_ids)
                        
                        # Calculate points - 1 point for each correct answer
                        user_correct_answers = user_set & correct_set
                        
                        # Log the comparison results
                        self.logger.info(f"User {user_id} - correct_set: {correct_set}, user_set: {user_set}, intersection: {user_correct_answers}")
                        
                        points = len(user_correct_answers)  # Points = number of correct selections
                        is_successful = len(user_correct_answers) > 0  # Successful if at least one correct answer
                        
                        self.logger.info(
                            f"User {user_id} selected {vote.option_ids}, "
                            f"correct answers {poll.correct_answers}, got {points} points, "
                            f"successful: {is_successful}"
                        )
                        
                        # Store points for leaderboard update
                        if user_id not in user_points:
                            user_points[user_id] = {
                                "points": 0,
                                "total_correct": 0
                            }
                        
                        user_points[user_id]["points"] += points
                        if is_successful:
                            user_points[user_id]["total_correct"] += 1
                        
                        points_updates.append({
                            "user_id": user_id,
                            "poll_points": points,
                            "is_successful": is_successful
                        })
                    except Exception as e:
                        # Log error but continue processing other votes
                        self.logger.error(f"Error processing vote for user {vote.user_id}: {e}")
                        # Still include the user in points_updates but flag that there was an issue
                        points_updates.append({
                            "user_id": vote.user_id,
                            "poll_points": 0,
                            "is_successful": False,
                            "error": True
                        })
            else:
                # If no votes were found in the Vote table, try the UserPollSelection table
                self.logger.info(f"No votes found in Vote table for poll {poll_id}, trying UserPollSelection table")
                stmt = select(UserPollSelection).where(UserPollSelection.poll_id == poll_id)
                result = await self.session.execute(stmt)
                selections = result.scalars().all()
                self.logger.info(f"Found {len(selections)} selections in UserPollSelection table for poll {poll_id}")
                
                if selections:
                    # Convert correct answers to a set of strings for consistent comparison
                    correct_set = set(str(x) for x in poll.correct_answers)
                    
                    for selection in selections:
                        try:
                            user_id = selection.user_id
                            # Parse selections from the UserPollSelection
                            if selection.selections:
                                # Convert selection options to strings for consistent comparison
                                user_set = set(str(x) for x in selection.selections)
                                
                                # Calculate points - 1 point for each correct answer
                                user_correct_answers = user_set & correct_set
                                
                                # Log the comparison results
                                self.logger.info(f"User {user_id} - correct_set: {correct_set}, user_set: {user_set}, intersection: {user_correct_answers}")
                                
                                points = len(user_correct_answers)  # Points = number of correct selections
                                is_successful = len(user_correct_answers) > 0  # Successful if at least one correct answer
                                
                                self.logger.info(
                                    f"User {user_id} selected {selection.selections}, "
                                    f"correct answers {poll.correct_answers}, got {points} points, "
                                    f"successful: {is_successful}"
                                )
                                
                                # Store points for leaderboard update
                                if user_id not in user_points:
                                    user_points[user_id] = {
                                        "points": 0,
                                        "total_correct": 0
                                    }
                                
                                user_points[user_id]["points"] += points
                                if is_successful:
                                    user_points[user_id]["total_correct"] += 1
                                
                                points_updates.append({
                                    "user_id": user_id,
                                    "poll_points": points,
                                    "is_successful": is_successful
                                })
                        except Exception as e:
                            # Log error but continue processing other selections
                            self.logger.error(f"Error processing selection for user {selection.user_id}: {e}")
                            # Still include the user in points_updates but flag that there was an issue
                            points_updates.append({
                                "user_id": selection.user_id,
                                "poll_points": 0,
                                "is_successful": False,
                                "error": True
                            })
                else:
                    self.logger.warning(f"No votes or selections found for poll {poll_id}")
        except Exception as e:
            self.logger.error(f"Database error getting votes: {e}", exc_info=True)
            await self.session.rollback()
            return []
            
        try:
            # Update leaderboard directly
            if user_points:
                self.logger.info(f"Updating leaderboard with points from {len(user_points)} users for poll {poll_id}")
                await self.update_poll_type_leaderboard(
                    guild_id=poll.guild_id,
                    poll_type=poll.poll_type,
                    user_points=user_points
                )
            else:
                self.logger.warning(f"No points to update for poll {poll_id}")
                
            # Commit all changes
            await self.session.commit()
            self.logger.info(f"Successfully committed points updates for poll {poll_id}")
        except Exception as e:
            self.logger.error(f"Error updating leaderboard: {e}", exc_info=True)
            # Roll back any pending changes
            try:
                await self.session.rollback()
            except Exception as rollback_error:
                self.logger.error(f"Error rolling back transaction: {rollback_error}", exc_info=True)
        
        return points_updates

    async def update_poll_type_leaderboard(self, guild_id: int, poll_type: str, user_points: Dict[str, Dict]) -> None:
        """Update leaderboard entries for users who participated in a poll."""
        self.logger.info(f"Updating poll type leaderboard for guild {guild_id}, poll type {poll_type}")
        
        try:
            # Get current leaderboard entries for these users
            user_ids = list(user_points.keys())
            if not user_ids:
                self.logger.info(f"No users to update in leaderboard")
                return
                
            self.logger.info(f"Incrementally updating leaderboard for {len(user_ids)} users")
            
            # First, get existing entries for these users
            existing_entries_stmt = select(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type,
                    PollTypeLeaderboard.user_id.in_(user_ids)
                )
            )
            
            result = await self.session.execute(existing_entries_stmt)
            existing_entries = {entry.user_id: entry for entry in result.scalars().all()}
            
            # Get all existing leaderboard entries to recalculate ranks
            all_entries_stmt = select(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type
                )
            )
            
            result = await self.session.execute(all_entries_stmt)
            all_entries = {entry.user_id: entry for entry in result.scalars().all()}
            
            # Update existing entries and add new entries
            for user_id, points_data in user_points.items():
                if user_id in existing_entries:
                    # Update existing entry
                    entry = existing_entries[user_id]
                    entry.points += points_data["points"]
                    entry.total_correct += points_data["total_correct"]
                    entry.last_updated = datetime.now(timezone.utc)
                    self.logger.info(f"Updated existing entry for user {user_id}: points={entry.points}, total_correct={entry.total_correct}")
                else:
                    # Create new entry
                    new_entry = PollTypeLeaderboard(
                        guild_id=guild_id,
                        poll_type=poll_type,
                        user_id=user_id,
                        points=points_data["points"],
                        total_correct=points_data["total_correct"],
                        rank=0,  # Will be updated below
                        last_updated=datetime.now(timezone.utc)
                    )
                    self.session.add(new_entry)
                    all_entries[user_id] = new_entry
                    self.logger.info(f"Created new entry for user {user_id}: points={points_data['points']}, total_correct={points_data['total_correct']}")
            
            # Now recalculate ranks based on updated points
            # Convert to list for sorting
            entries_list = list(all_entries.values())
            entries_list.sort(key=lambda x: (-x.points, -x.total_correct))
            
            # Update ranks
            current_rank = 1
            prev_points = None
            prev_correct = None
            
            for i, entry in enumerate(entries_list):
                # Handle ties
                if prev_points is not None and prev_correct is not None:
                    if entry.points != prev_points or entry.total_correct != prev_correct:
                        current_rank = i + 1
                
                entry.rank = current_rank
                prev_points = entry.points
                prev_correct = entry.total_correct
                
                self.logger.info(f"Updated rank for user {entry.user_id}: points={entry.points}, rank={entry.rank}")
            
            # Flush changes to the database
            await self.session.flush()
            
            # Verify the updates
            self.logger.info(f"Verification: {len(entries_list)} leaderboard entries updated for guild {guild_id}, poll type {poll_type}")
            
            # Mark success
            self.logger.info(f"Successfully updated poll type leaderboard for guild {guild_id}, poll type {poll_type}")
        except Exception as e:
            self.logger.error(f"Error updating poll type leaderboard: {e}", exc_info=True)
            raise Exception(f"Failed to update leaderboard: {str(e)}")

    async def _update_user_score(
        self,
        user_id: str,
        guild_id: int,
        poll_type: str,
        points: int,
        is_successful: bool
    ) -> None:
        """Update a user's score."""
        try:
            self.logger.info(f"Updating score for user {user_id} in guild {guild_id}, poll type {poll_type}")
            self.logger.info(f"Points to add: {points}, Is successful: {is_successful}")
            
            # Check if user score exists
            stmt = select(UserScore).where(
                and_(
                    UserScore.user_id == user_id,
                    UserScore.guild_id == guild_id,
                    UserScore.poll_type == poll_type
                )
            )
            result = await self.session.execute(stmt)
            user_score = result.scalar_one_or_none()
            
            if user_score:
                # Update existing score
                self.logger.info(f"Updating existing score for user {user_id}: current points={user_score.points}, adding {points}")
                user_score.points += points
                user_score.polls_participated += 1
                if is_successful:
                    user_score.total_correct += 1
                # Update last successful timestamp if this poll was successful
                if is_successful:
                    user_score.last_successful = datetime.utcnow()
            else:
                # Create new score
                self.logger.info(f"Creating new score for user {user_id} with {points} points")
                user_score = UserScore(
                    user_id=user_id,
                    guild_id=guild_id,
                    poll_type=poll_type,
                    points=points,
                    polls_participated=1,
                    total_correct=1 if is_successful else 0,
                    last_successful=datetime.utcnow() if is_successful else None
                )
                self.session.add(user_score)
            
            # Flush changes to get updated values
            await self.session.flush()
            self.logger.info(f"User {user_id} score updated successfully: points={user_score.points}, total_correct={user_score.total_correct}")
            
            # No need to commit here - will be committed by the calling method
            
        except Exception as e:
            self.logger.error(f"Error updating user score: {e}", exc_info=True)
            # Don't raise to allow the process to continue
            # The transaction will be rolled back by the caller if needed

    async def get_user_stats(
        self,
        user_id: int,
        guild_id: int,
        poll_type: str
    ) -> Dict:
        """Get stats for a user."""
        try:
            # Get user score
            stmt = select(UserScore).where(
                and_(
                    UserScore.user_id == str(user_id),
                    UserScore.guild_id == guild_id,
                    UserScore.poll_type == poll_type
                )
            )
            result = await self.session.execute(stmt)
            user_score = result.scalar_one_or_none()
            
            if not user_score:
                return {
                    "user_id": user_id,
                    "total_points": 0,
                    "total_correct": 0,
                    "rank": None
                }
            
            # Get user rank
            stmt = select(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.user_id == str(user_id),
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type
                )
            )
            result = await self.session.execute(stmt)
            leaderboard_entry = result.scalar_one_or_none()
            
            return {
                "user_id": user_id,
                "total_points": user_score.points,
                "total_correct": user_score.total_correct,
                "rank": leaderboard_entry.rank if leaderboard_entry else None
            }
            
        except Exception as e:
            self.logger.error(f"Error getting user stats: {e}", exc_info=True)
            raise PollError(f"Failed to get user stats: {str(e)}")

    async def get_leaderboard(
        self,
        guild_id: int,
        poll_type: str,
        limit: int = 10
    ) -> List[Dict]:
        """Get leaderboard for a guild's poll type."""
        try:
            # Get top users by points
            stmt = (
                select(UserScore)
                .where(
                    and_(
                        UserScore.guild_id == guild_id,
                        UserScore.poll_type == poll_type
                    )
                )
                .order_by(
                    desc(UserScore.points),
                    desc(UserScore.total_correct)
                )
                .limit(limit)
            )
            
            try:
                result = await self.session.execute(stmt)
                scores = result.scalars().all()
            except Exception as db_error:
                # Handle transaction errors by rolling back
                self.logger.error(f"Database error getting leaderboard: {db_error}", exc_info=True)
                await self.session.rollback()
                # Return empty results rather than failing
                return []
            
            return [
                {
                    "user_id": score.user_id,
                    "total_points": score.points,
                    "total_correct": score.total_correct,
                    "rank": i + 1
                }
                for i, score in enumerate(scores)
            ]
        except Exception as e:
            # Log the error but continue without breaking the reveal process
            self.logger.error(f"Error getting leaderboard: {e}", exc_info=True)
            # Try to rollback the transaction to prevent cascading errors
            try:
                await self.session.rollback()
            except Exception as rollback_error:
                self.logger.error(f"Error rolling back transaction: {rollback_error}", exc_info=True)
            # Return an empty leaderboard instead of raising an error
            return []

    async def update_guild_leaderboard(self, guild_id: int, poll_type: str) -> None:
        """Update rankings for a guild's poll type."""
        try:
            self.logger.info(f"Updating leaderboard for guild {guild_id}, poll type {poll_type}")
            
            # Instead of querying user_scores which has schema issues,
            # we'll calculate scores directly from the votes and polls data
            
            # First, get all revealed polls of this type for this guild
            polls_stmt = select(Poll).where(
                and_(
                    Poll.guild_id == guild_id,
                    Poll.poll_type == poll_type,
                    Poll.is_revealed == True
                )
            )
            
            try:
                result = await self.session.execute(polls_stmt)
                polls = result.scalars().all()
                self.logger.info(f"Found {len(polls)} revealed polls for guild {guild_id}, poll type {poll_type}")
                
                # Log poll details for debugging
                for poll in polls:
                    self.logger.info(f"Poll {poll.id}: question='{poll.question}', correct_answers={poll.correct_answers}")
            except Exception as e:
                self.logger.error(f"Error querying polls: {e}", exc_info=True)
                await self.session.rollback()
                raise
                
            # Build user scores from votes for these polls
            user_scores = {}
            
            for poll in polls:
                # Get votes for this poll - use both Vote and UserPollSelection to ensure all votes are captured
                self.logger.info(f"Querying votes for poll {poll.id}")
                
                # First get votes from the Vote table
                votes_stmt = select(Vote).where(Vote.poll_id == poll.id)
                result = await self.session.execute(votes_stmt)
                votes = result.scalars().all()
                self.logger.info(f"Found {len(votes)} votes from Vote table for poll {poll.id}")
                
                # Also try to get votes from UserPollSelection for legacy or alternative vote storage
                ups_stmt = select(UserPollSelection).where(UserPollSelection.poll_id == poll.id)
                result = await self.session.execute(ups_stmt)
                user_selections = result.scalars().all()
                self.logger.info(f"Found {len(user_selections)} user selections from UserPollSelection table for poll {poll.id}")
                
                # Process votes from Vote table
                if votes:
                    self.logger.info(f"Processing {len(votes)} votes from Vote table for poll {poll.id}")
                    self._process_votes_for_leaderboard(poll, votes, user_scores)
                    
                # Process votes from UserPollSelection table if needed
                if user_selections and not votes:
                    self.logger.info(f"Processing {len(user_selections)} selections from UserPollSelection table for poll {poll.id}")
                    self._process_selections_for_leaderboard(poll, user_selections, user_scores)
            
            # Convert dict to sorted list of user scores
            scores_list = [
                {"user_id": user_id, **score_data}
                for user_id, score_data in user_scores.items()
            ]
            
            # Sort by points (descending), then total_correct (descending)
            scores_list.sort(key=lambda x: (-x["points"], -x["total_correct"]))
            
            self.logger.info(f"Calculated scores for {len(scores_list)} users")
            for idx, score in enumerate(scores_list):
                self.logger.info(f"User {score['user_id']}: points={score['points']}, total_correct={score['total_correct']}")
            
            # Delete existing leaderboard entries for this guild and poll type
            delete_stmt = delete(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type
                )
            )
            delete_result = await self.session.execute(delete_stmt)
            self.logger.info(f"Deleted {delete_result.rowcount} existing leaderboard entries")
            
            # Create new leaderboard entries
            current_rank = 1
            prev_points = None
            prev_correct = None
            
            for i, score_data in enumerate(scores_list):
                # Handle ties
                if prev_points is not None and prev_correct is not None:
                    if score_data["points"] != prev_points or score_data["total_correct"] != prev_correct:
                        current_rank = i + 1
                
                user_id = score_data["user_id"]
                points = score_data["points"]
                total_correct = score_data["total_correct"]
                
                # Log the leaderboard entry we're creating
                self.logger.info(f"Creating leaderboard entry - User: {user_id}, Guild: {guild_id}, " +
                                f"Poll Type: {poll_type}, Points: {points}, Rank: {current_rank}")
                
                leaderboard_entry = PollTypeLeaderboard(
                    guild_id=guild_id,
                    poll_type=poll_type,
                    user_id=user_id,
                    points=points,
                    total_correct=total_correct,
                    rank=current_rank,
                    last_updated=datetime.now(timezone.utc)
                )
                self.session.add(leaderboard_entry)
                self.logger.debug(f"Added leaderboard entry for user {user_id}, rank {current_rank}, points {points}")
                
                prev_points = points
                prev_correct = total_correct
            
            # Make sure to flush and explicitly commit the changes
            await self.session.flush()
            try:
                await self.session.commit()
                self.logger.info(f"Successfully committed leaderboard updates for guild {guild_id}, poll type {poll_type}")
            except Exception as commit_error:
                self.logger.error(f"Error committing leaderboard updates: {commit_error}", exc_info=True)
                await self.session.rollback()
                raise  # Re-raise to signal that the update failed
            
            # Verify the leaderboard entries were created
            verify_stmt = select(func.count()).select_from(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type
                )
            )
            verify_result = await self.session.execute(verify_stmt)
            entry_count = verify_result.scalar_one()
            self.logger.info(f"Verification: {entry_count} leaderboard entries now exist for guild {guild_id}, poll type {poll_type}")
            
            # Verify specific entries
            if entry_count > 0:
                verify_entries_stmt = select(PollTypeLeaderboard).where(
                    and_(
                        PollTypeLeaderboard.guild_id == guild_id,
                        PollTypeLeaderboard.poll_type == poll_type
                    )
                ).order_by(PollTypeLeaderboard.rank)
                result = await self.session.execute(verify_entries_stmt)
                entries = result.scalars().all()
                self.logger.info(f"Verification entries: {len(entries)}")
                for entry in entries:
                    self.logger.info(f"Verified entry - User: {entry.user_id}, Points: {entry.points}, Rank: {entry.rank}")
            
        except Exception as e:
            self.logger.error(f"Error updating guild leaderboard: {e}", exc_info=True)
            # Roll back any pending changes
            try:
                await self.session.rollback()
            except Exception as rollback_error:
                self.logger.error(f"Error rolling back transaction: {rollback_error}", exc_info=True)
            # Re-raise the exception to let the caller know there was an issue
            raise Exception(f"Failed to update guild leaderboard: {str(e)}")

    def _process_votes_for_leaderboard(self, poll, votes, user_scores):
        """Process votes from Vote model for leaderboard calculation."""
        if poll.correct_answers:
            correct_set = set(str(x) for x in poll.correct_answers)
            self.logger.info(f"Poll {poll.id} correct answers: {correct_set}")
            
            for vote in votes:
                user_id = vote.user_id
                user_set = set(str(x) for x in vote.option_ids)
                
                # Calculate points - 1 point per correct answer
                correct_answers = user_set & correct_set
                points = len(correct_answers)
                is_successful = len(correct_answers) > 0
                
                self.logger.info(f"Vote from user {user_id}: options={user_set}, correct={correct_answers}, points={points}, successful={is_successful}")
                
                # Update the user's score in our dictionary
                if user_id not in user_scores:
                    user_scores[user_id] = {
                        "points": 0, 
                        "total_correct": 0,
                        "polls_participated": 0
                    }
                    
                user_scores[user_id]["points"] += points
                user_scores[user_id]["polls_participated"] += 1
                if is_successful:
                    user_scores[user_id]["total_correct"] += 1
        else:
            self.logger.warning(f"Poll {poll.id} has no correct answers defined, skipping point calculation")

    def _process_selections_for_leaderboard(self, poll, selections, user_scores):
        """Process selections from UserPollSelection model for leaderboard calculation."""
        if poll.correct_answers:
            correct_set = set(str(x) for x in poll.correct_answers)
            self.logger.info(f"Poll {poll.id} correct answers: {correct_set}")
            
            for selection in selections:
                user_id = selection.user_id
                # Parse selections from the JSON
                try:
                    if selection.selections:
                        user_set = set(str(x) for x in selection.selections)
                        
                        # Calculate points - 1 point per correct answer
                        correct_answers = user_set & correct_set
                        points = len(correct_answers)
                        is_successful = len(correct_answers) > 0
                        
                        self.logger.info(f"Selection from user {user_id}: options={user_set}, correct={correct_answers}, points={points}, successful={is_successful}")
                        
                        # Update the user's score in our dictionary
                        if user_id not in user_scores:
                            user_scores[user_id] = {
                                "points": 0, 
                                "total_correct": 0,
                                "polls_participated": 0
                            }
                            
                        user_scores[user_id]["points"] += points
                        user_scores[user_id]["polls_participated"] += 1
                        if is_successful:
                            user_scores[user_id]["total_correct"] += 1
                except Exception as e:
                    self.logger.error(f"Error processing selection for user {user_id}: {e}")
        else:
            self.logger.warning(f"Poll {poll.id} has no correct answers defined, skipping point calculation")

    async def get_poll(self, poll_id: int) -> Optional[Poll]:
        """Get a poll by ID."""
        stmt = select(Poll).where(Poll.id == poll_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_poll_type_points(self, guild_id: int, poll_type: str, user_id: str):
        """Get a user's points and rank for a specific poll type."""
        try:
            # Query the database
            stmt = select(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type,
                    PollTypeLeaderboard.user_id == user_id
                )
            )
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except Exception as e:
            self.logger.error(f"Error getting user poll type points: {e}", exc_info=True)
            raise Exception(f"Failed to get user points: {str(e)}")
    
    async def get_poll_type_leaderboard(self, guild_id: int, poll_type: str, limit: int = 10):
        """Get the leaderboard for a specific poll type."""
        try:
            self.logger.info(f"Fetching poll_type_leaderboard for guild {guild_id}, poll type {poll_type}")
            
            # Query the database
            stmt = select(PollTypeLeaderboard).where(
                and_(
                    PollTypeLeaderboard.guild_id == guild_id,
                    PollTypeLeaderboard.poll_type == poll_type
                )
            ).order_by(PollTypeLeaderboard.rank).limit(limit)
            
            result = await self.session.execute(stmt)
            entries = result.scalars().all()
            
            # Log the results
            self.logger.info(f"Found {len(entries)} leaderboard entries for guild {guild_id}, poll type {poll_type}")
            for entry in entries:
                self.logger.debug(f"Leaderboard entry - User: {entry.user_id}, Points: {entry.points}, Rank: {entry.rank}")
            
            # If no entries are found, try refreshing the leaderboard
            if not entries:
                self.logger.warning(f"No leaderboard entries found, attempting to refresh leaderboard")
                try:
                    # Force a complete refresh of the leaderboard from votes and polls
                    self.logger.info("Forcing complete leaderboard refresh from polls and votes data")
                    await self.update_guild_leaderboard(guild_id, poll_type)
                    
                    # Flush and commit the changes to ensure they're visible
                    await self.session.flush()
                    await self.session.commit()
                    
                    # Query again with fresh data
                    self.logger.info("Re-fetching leaderboard after refresh")
                    # Execute the query again, but with a new transaction
                    result = await self.session.execute(stmt)
                    entries = result.scalars().all()
                    self.logger.info(f"After refresh: Found {len(entries)} leaderboard entries")
                except Exception as refresh_error:
                    self.logger.error(f"Error refreshing leaderboard: {refresh_error}", exc_info=True)
                    # Roll back any failed changes
                    try:
                        await self.session.rollback()
                    except Exception as rollback_error:
                        self.logger.error(f"Error rolling back: {rollback_error}", exc_info=True)
                    # Continue with empty entries
            
            return entries
        except Exception as e:
            self.logger.error(f"Error getting poll type leaderboard: {e}", exc_info=True)
            # Return empty list instead of raising error
            return []
