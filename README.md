# Discord Poll Bot

A multi-server, multi-poll Discord bot for creating and managing various types of polls across different Discord servers.

## Overview

This Discord bot allows you to create and manage multiple poll configurations across different Discord servers simultaneously. Each poll configuration can have its own unique settings, including:

- Poll type (identifier for different kinds of polls)
- Server (Guild) ID
- Admin role ID
- Dashboard command name

The bot uses JSON configuration files to define these poll settings, making it easy to add or modify poll types without changing the core code.

## Features

- Support for multiple Discord servers simultaneously
- Custom poll types with separate configurations
- Button-based voting interface
- Multiple selection support
- Points system for correct answers
- User statistics and leaderboard
- Admin controls for poll management
- Customizable dashboard commands for each poll type
- Automatic poll expiration
- Database storage for poll results and user statistics

## Requirements

- Python 3.9+
- PostgreSQL database
- Discord Bot Token and Application ID

## Installation

1. Clone the repository:
```
git clone https://github.com/yourusername/most-recent-discord-poll-bot.git
cd most-recent-discord-poll-bot
```

2. Create and activate a virtual environment:
```
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```
pip install -r requirements.txt
```

4. Set up PostgreSQL database:

Start PostgreSQL service (if not already running)
On macOS:
```
brew services start postgresql
```
On Ubuntu/Debian:
```
sudo service postgresql start
```
On Windows:

Start PostgreSQL through Services app
Create database and user (in psql console)
```
psql postgres
CREATE USER <username> WITH PASSWORD '<password>';
CREATE DATABASE <dbname>;
GRANT ALL PRIVILEGES ON DATABASE <dbname> TO <username>;
\c <dbname>
GRANT ALL PRIVILEGES ON SCHEMA public TO <username>;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO <username>;
\q
```
Or alternatively with separate commands:
```
psql postgres -c "CREATE USER <username> WITH PASSWORD '<password>';"
psql postgres -c "CREATE DATABASE <dbname>;"
psql postgres -c "GRANT ALL PRIVILEGES ON DATABASE <dbname> TO <username>;"
psql <dbname> -c "GRANT ALL PRIVILEGES ON SCHEMA public TO <username>;"
psql <dbname> -c "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL PRIVILEGES ON TABLES TO <username>;"
```

5. Create a `.env` file in the root directory with the following content:
```
# Discord Configuration
DISCORD_TOKEN=your_bot_token
DISCORD_APPLICATION_ID=your_application_id

# Database Configuration
DATABASE_URL=postgresql+asyncpg://username:password@localhost:5432/dbname
```

6. Initialize the database:
```
alembic upgrade head
```

7. Create poll configuration files in the `scripts` directory (see examples below)

## Poll Configuration

Each poll type requires a JSON configuration file in the `scripts` directory. Example:

```json
{
    "poll_type": "your_poll_type",
    "discord_guild_id": "your_guild_id",
    "discord_admin_role_id": "your_admin_role_id",
    "dashboard_command": "dashboard_your_poll_type"
}
```

Parameters:
- `poll_type`: Unique identifier for this poll type
- `discord_guild_id`: ID of the Discord server where this poll will be active
- `discord_admin_role_id`: ID of the admin role that can manage this poll
- `dashboard_command`: Command name for accessing the dashboard for this poll type

## Usage

Start the bot with one or more poll configurations:

```
python main.py --config scripts/poll1.json,scripts/poll2.json,scripts/poll3.json
```

You can also specify the number of shards if your bot is in many servers:

```
python main.py --config scripts/poll1.json,scripts/poll2.json --shards 2
```

## Bot Invite URL

Invite the bot to your server using this URL (replace `YOUR_APPLICATION_ID` with your actual application ID):

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_APPLICATION_ID&permissions=2147483648&scope=bot%20applications.commands
```

## Commands

### Admin Commands
- `/create_[poll_type]` - Create a new poll for the specified poll type
  - `question` - The poll question (required)
  - `options` - Options separated by commas (e.g., 'Red,Blue,Green') (required)
  - `description` - Optional description for the poll
  - `max_selections` - Maximum number of options a user can select (optional, default: 1)
  - `duration` - Duration format: '5d' (days), '24h' (hours), '30m' (minutes) (optional, default: 5 days)
  - `show_votes_while_active` - Whether to show vote counts while the poll is active (optional, default: False)

- `/close_[poll_type]` - Close the current active poll for the specified poll type

- `/reveal_[poll_type]` - Reveal the correct answers for the closed poll of the specified poll type

### User Commands
- `/vote_[poll_type]` - Vote on the active poll for the specified poll type

- `/dashboard_[poll_type]` - View your personal statistics and leaderboard for the specified poll type
  - Shows your total points, correct answers, and current rank
  - Displays the global leaderboard with medals for top ranks

- `/showhelp` - Display help information about available commands

Note: Replace `[poll_type]` with the actual poll type defined in your configuration files (e.g., `world_pvp`, `shooting_eagle`, `another_vote`)

## Development

- The bot uses Discord.py library for interaction with Discord's API
- SQLAlchemy for database interactions
- Alembic for database migrations
- Follow PEP 8 style guide
- Add type hints to all functions
- Write tests for new features

## Project Structure

- `main.py`: Main entry point for the bot
- `scripts/`: Directory containing poll configuration files
- `src/`: Source code
  - `config/`: Configuration and settings
  - `database/`: Database models and connection logic
  - `services/`: Business logic services
  - `bot/`: Discord bot functionality
  - `utils/`: Utility functions
- `alembic/`: Database migration scripts

## Note
Make sure to replace:
- `<username>`, `<password>`, `<dbname>` with your desired database credentials
- `your_bot_token` with your Discord bot token
- `your_application_id` with your Discord application ID
- Other Discord IDs with your server's specific IDs

## License

MIT License
