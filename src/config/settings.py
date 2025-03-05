import os
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from dotenv import load_dotenv

@dataclass
class PollConfig:
    """Configuration for a specific poll type."""
    poll_type: str
    guild_id: int
    admin_role_id: int
    dashboard_command: str

class Settings:
    """Configuration settings loaded from environment variables and poll configs."""
    
    def __init__(self, poll_config_paths: List[str] = None):
        # Load environment variables from .env file
        load_dotenv(override=True)
        
        # Core Discord Configuration
        self.DISCORD_TOKEN = self._get_required_env("DISCORD_TOKEN")
        self.DISCORD_APPLICATION_ID = self._get_required_env("DISCORD_APPLICATION_ID")
        
        # Database Configuration
        self.DATABASE_URL = self._get_required_env("DATABASE_URL")
        
        # Load poll configurations
        self.poll_configs: Dict[str, PollConfig] = {}
        if poll_config_paths:
            for config_path in poll_config_paths:
                self._load_poll_config(config_path)
    
    def _load_poll_config(self, config_path: str):
        """Load a poll configuration from a JSON file."""
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            
            # Use poll_type from config file instead of filename
            poll_type = config['poll_type']
            self.poll_configs[poll_type] = PollConfig(
                poll_type=poll_type,
                guild_id=int(config['discord_guild_id']),
                admin_role_id=int(config['discord_admin_role_id']),
                dashboard_command=config['dashboard_command']
            )
        except Exception as e:
            raise ConfigError(f"Failed to load poll config from {config_path}: {str(e)}")
    
    def get_poll_config_by_type(self, guild_id: int, poll_type: str) -> Optional[PollConfig]:
        """Get poll configuration for a specific type in a guild."""
        config = self.poll_configs.get(poll_type)
        if config and config.guild_id == guild_id:
            return config
        return None
    
    def get_poll_configs_for_guild(self, guild_id: int) -> List[PollConfig]:
        """Get all poll configurations for a specific guild."""
        return [
            config for config in self.poll_configs.values()
            if config.guild_id == guild_id
        ]
    
    def _get_required_env(self, key: str) -> str:
        """Get a required environment variable."""
        value = os.getenv(key)
        if value is None:
            raise ConfigError(
                f"Missing required environment variable: {key}\n"
                f"Please check your .env file and ensure it contains all required variables."
            )
        return value

class ConfigError(Exception):
    """Raised when there is an error in configuration."""
    pass

# Create a default settings instance
settings = Settings()
