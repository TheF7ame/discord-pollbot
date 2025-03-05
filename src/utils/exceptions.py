from sqlalchemy import exc as sa_exc

class PollError(Exception):
    """Base exception for poll-related errors."""
    def __init__(self, message: str, error_code: str = None):
        super().__init__(message)
        self.error_code = error_code
        self.message = message

class StateError(PollError):
    """Error for invalid state transitions."""
    def __init__(self, current_state: str, attempted_state: str):
        message = f"Invalid state transition from {current_state} to {attempted_state}"
        super().__init__(message, error_code="INVALID_STATE_TRANSITION")

class ValidationError(PollError):
    """Error for validation failures."""
    def __init__(self, message: str, field: str = None):
        error_code = f"VALIDATION_ERROR_{field.upper()}" if field else "VALIDATION_ERROR"
        super().__init__(message, error_code=error_code)

class DatabaseError(PollError):
    """Error for database operations."""
    def __init__(self, message: str, operation: str = None):
        error_code = f"DB_ERROR_{operation.upper()}" if operation else "DB_ERROR"
        super().__init__(message, error_code=error_code)

class SessionError(PollError):
    """Error for session-related issues."""
    def __init__(self, message: str):
        super().__init__(message, error_code="SESSION_ERROR")

class PointsError(PollError):
    """Error for points calculation issues."""
    def __init__(self, message: str):
        super().__init__(message, error_code="POINTS_ERROR")

class ConfigError(Exception):
    """Error for configuration-related issues."""
    def __init__(self, message: str, config_key: str = None):
        error_code = f"CONFIG_ERROR_{config_key.upper()}" if config_key else "CONFIG_ERROR"
        super().__init__(message)
        self.error_code = error_code
        self.message = message

class GuildError(Exception):
    """Error for guild-related issues."""
    def __init__(self, message: str, guild_id: int = None):
        error_code = f"GUILD_ERROR_{guild_id}" if guild_id else "GUILD_ERROR"
        super().__init__(message)
        self.error_code = error_code
        self.message = message

def handle_poll_error(error: Exception) -> str:
    """Convert errors to user-friendly messages."""
    if isinstance(error, PollError):
        return f"{error.message} (Error code: {error.error_code})"
    elif isinstance(error, sa_exc.IntegrityError):
        return "A database constraint was violated."
    elif isinstance(error, sa_exc.TimeoutError):
        return "The database operation timed out. Please try again."
    else:
        return "An unexpected error occurred. Please try again later."
