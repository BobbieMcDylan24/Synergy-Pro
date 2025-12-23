import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from datetime import datetime
import random 
import io
from PIL import Image, ImageDraw, ImageFont
import logging
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG

logger = logging.getLogger(__name__)

class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = MySQLHelper(**MYSQL_CONFIG)
        self.cooldowns = {}
        self._ensure_tables()

    def _ensure_tables(self):
        create_levels_table = """
            CREATE TABLE IF NOT EXISTS levels (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                xp INT DEFAULT 0,
                level INT DEFAULT 0,
                total_xp INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_guild (user_id, guild_id),
                INDEX idx_user_id (user_id),
                INDEX idx_guild_id (guild_id),
                INDEX idx_level (level)
            )
        """
        self.db.create_table(create_levels_table)
        
        create_settings_table = """
            CREATE TABLE IF NOT EXISTS level_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT UNIQUE NOT NULL,
                enabled BOOLEAN DEFAULT TRUE,
                level_up_channel_id BIGINT DEFAULT NULL,
                level_up_message TEXT DEFAULT NULL,
                xp_cooldown INT DEFAULT 30,
                min_xp INT DEFAULT 15,
                max_xp INT DEFAULT 25,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """
        self.db.create_table(create_settings_table)
        logger.info("Leveling tables ensured")

    def xp_needed(self, level: int) -> int:
        return 5 * (level ** 2) + 50 * level + 100
    
    def _get_guild_settings(self, guild_id: int) -> dict:
        query = "SELECT * FROM level_settings WHERE guild_id = %s"
        result = self.db.fetch_one_dict(query, (guild_id,))

        if not result:
            self.db.insert("level_settings", {"guild_id": guild_id})
            return {
                "enabled": True,
                "level_up_channel_id": None,
                "level_up_message": None,
                "xp_cooldown": 30,
                "min_xp": 15,
                "max_xp": 25
            }
        return result
    
    def _get_user_data(self, user_id: int, guild_id: int) -> dict:
        query = """
            SELECT user_id, guild_id, xp, level, total_xp
            FROM levels
            WHERE user_id = %s AND guild_id = %s
        """
        result = self.db.fetch_one_dict(query, (user_id, guild_id))

        if not result:
            return {
                "user_id": user_id,
                "guild_id": guild_id,
                "xp": 0,
                "level": 0,
                "total_xp": 0
            }
        
        return result
    
    def _upsert_user_data(self, user_data: dict) -> bool:
        query = """
            INSERT INTO levels (user_id, guild_id, xp, level, total_xp)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                xp = VALUES(xp),
                level = VALUES(level),
                total_xp = VALUES(total_xp)
        """
        return self.db.execute_query(query, (user_data["user_id"], user_data["guild_id"], user_data["xp"], user_data["level"], user_data["total_xp"]))
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return
        
        settings = self._get_guild_settings(message.guild.id)

        if not settings["enabled"]:
            return
        
        user_id = message.author.id
        cooldown_key = f"{user_id}_{message.guild.id}"

        now = datetime.utcnow()
        last_time = self.cooldowns.get(cooldown_key)
        if last_time and (now - last_time).total_seconds() < settings["xp_cooldown"]:
            return
        self.cooldowns[cooldown_key] = now

        user_data = self._get_user_data(user_id, message.guild.id)

        gained_xp = random.randint(settings["min_xp"], settings["max_xp"])
        user_data['xp'] += gained_xp
        user_data['total_xp'] += gained_xp

        leveled_up = False
        levels_gained = 0
        while user_data["xp"] >= self.xp_needed(user_data["level"]):
            user_data["xp"] -= self.xp_needed(user_data["level"])
            user_data["level"] += 1
            levels_gained += 1
            leveled_up = True

        self._upsert_user_data(user_data)

        if leveled_up:
            level_up_message = settings.get("level_up_message") or f"{message.author.mention} leveled up to **Level {user_data['level']}**!"
            level_up_message = level_up_message.replace("{user}", message.author.mention)
            level_up_message = level_up_message.replace("{level}", str(user_data['level']))

            channel = message.channel
            if settings["level_up_channel_id"]:
                level_up_channel = message.guild.get_channel(settings["level_up_channel_id"])
                if level_up_channel:
                    channel = level_up_channel
            
            try:
                await channel.send(level_up_message)
            except discord.Forbidden:
                logger.warning(f"Cannot send level up message in guild {message.guild.id}")


def setup(bot):
    bot.add_cog(Leveling(bot))