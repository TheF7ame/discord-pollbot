from enum import Enum

class ButtonIds:
    OPTION_PREFIX = "poll_option_"
    CLOSE_POLL = "close_poll"
    REVEAL_ANSWER = "reveal_answer"

class CommandNames(str, Enum):
    """Command names for the bot."""
    CREATE_POLL = "poll create"
    CLOSE_POLL = "poll closepoll"
    REVEAL_ANSWER = "poll revealanswer"
    MY_VOTES = "poll myvotes"
    DASHBOARD = "poll dashboard"
    SHOW_HELP = "poll showhelp"

class Messages:
    """Standard messages used throughout the bot."""
    POLL_CREATED = "📊 New poll created! Vote by clicking the buttons below."
    POLL_CLOSED = "🔒 Poll has been closed. No more votes can be submitted."
    POLL_REVEALED = "🎯 The correct answer(s) have been revealed!"
    MAX_SELECTIONS_REACHED = "⚠️ You've reached the maximum number of selections allowed."
    INVALID_SELECTION = "❌ Invalid selection."
    NOT_ADMIN = "⛔ You don't have permission to use this command."
    WRONG_CHANNEL = "⚠️ This command can only be used in the designated channel."
    POLL_CLOSED_MSG = "This poll is already closed."
    ANSWER_REVEALED = "The correct answer(s) have been revealed!"

class PollType(str, Enum):
    """Types of polls supported by the bot."""
    WORLD_PVP = "world_pvp"
    SHOOTING_EAGLE = "shooting_eagle"
    ANOTHER_VOTE = "another_vote"
    # Add more poll types as needed
