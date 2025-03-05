import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

class HelpCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="showhelp")
    async def show_help(self, interaction: discord.Interaction):
        """Show help information about bot commands."""
        embed = discord.Embed(
            title="Poll Bot Help",
            description="Here are all available commands:",
            color=discord.Color.blue()
        )

        # Get the user's poll configs for this guild
        guild_configs = self.bot.poll_configs.get(interaction.guild_id, [])
        
        # Check if user has admin role for any poll type
        is_admin = False
        for config in guild_configs:
            if interaction.user.get_role(config.admin_role_id):
                is_admin = True
                break

        if is_admin:
            # Admin Commands
            embed.add_field(
                name="Admin Commands",
                value=(
                    "`/create_[poll_type] <question> <options> [duration] [max_selections]`\n"
                    "Create a new poll\n"
                    "`/close_[poll_type]`\n"
                    "Close the current poll\n"
                    "`/reveal_[poll_type] <answers>`\n"
                    "Reveal correct answers"
                ),
                inline=False
            )

        # User Commands (visible to everyone)
        embed.add_field(
            name="User Commands",
            value=(
                "`/dashboard_[poll_type]`\n"
                "View your stats and leaderboard\n"
                "`/vote_[poll_type]`\n"
                "Vote in the current poll"
            ),
            inline=False
        )

        # Examples
        if is_admin:
            embed.add_field(
                name="Examples",
                value=(
                    "Create poll: `/create_world_pvp \"Best Class?\" \"Warrior,Mage,Rogue\" --duration 24h --max 2`\n"
                    "Reveal answers: `/reveal_world_pvp Warrior,Mage`"
                ),
                inline=False
            )
        else:
            embed.add_field(
                name="Examples",
                value=(
                    "View stats: `/dashboard_world_pvp`\n"
                    "Vote in poll: `/vote_world_pvp`"
                ),
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    """Setup function for the help commands cog."""
    await bot.add_cog(HelpCommands(bot))