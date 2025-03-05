import discord
from discord.ui import View, Button, Select, Modal, TextInput
import logging

logger = logging.getLogger(__name__)

class DashboardView(View):
    """View with buttons for the dashboard."""
    
    def __init__(self, bot, poll_type, user_id):
        super().__init__(timeout=180)  # 3 minute timeout
        self.bot = bot
        self.poll_type = poll_type
        self.user_id = user_id
        
        # Add buttons for common actions
        self.add_item(Button(
            label="Create Poll",
            style=discord.ButtonStyle.primary,
            custom_id=f"dashboard:create:{poll_type}"
        ))
        
        self.add_item(Button(
            label="View Active Polls",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dashboard:active:{poll_type}"
        ))
        
        self.add_item(Button(
            label="View Closed Polls",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dashboard:closed:{poll_type}"
        ))
        
        self.add_item(Button(
            label="Refresh",
            style=discord.ButtonStyle.success,
            custom_id=f"dashboard:refresh:{poll_type}"
        ))
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the user who triggered the dashboard can use its buttons
        return interaction.user.id == self.user_id
        
    async def on_timeout(self):
        # Clear all components when the view times out
        for item in self.children:
            item.disabled = True 