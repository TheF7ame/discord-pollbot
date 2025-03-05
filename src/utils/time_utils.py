from datetime import timedelta
import re

def parse_duration(duration_str: str) -> timedelta:
    """Parse a duration string into a timedelta object.
    
    Formats supported:
    - Xd: X days
    - Xh: X hours
    - Xm: X minutes
    - Xs: X seconds
    - Combinations like "1d12h30m"
    
    Args:
        duration_str: String representing duration (e.g., "5d", "12h", "30m", "1d12h")
        
    Returns:
        timedelta object representing the duration
        
    Raises:
        ValueError: If the duration string is invalid
    """
    if not duration_str:
        raise ValueError("Duration string cannot be empty")
    
    # Define regex pattern for duration components
    pattern = re.compile(r'(\d+)([dhms])', re.IGNORECASE)
    matches = pattern.findall(duration_str)
    
    if not matches:
        raise ValueError(f"Invalid duration format: {duration_str}")
    
    # Initialize duration components
    days = hours = minutes = seconds = 0
    
    # Process each component
    for value, unit in matches:
        value = int(value)
        unit = unit.lower()
        
        if unit == 'd':
            days = value
        elif unit == 'h':
            hours = value
        elif unit == 'm':
            minutes = value
        elif unit == 's':
            seconds = value
    
    return timedelta(
        days=days,
        hours=hours,
        minutes=minutes,
        seconds=seconds
    ) 