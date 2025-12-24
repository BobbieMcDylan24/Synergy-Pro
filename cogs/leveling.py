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


    level = SlashCommandGroup("level", "Leveling system commands")

    @level.command(name="rank", description="View your or another member's rank card")
    async def rank(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to check", required=False)): # type: ignore
        await ctx.defer()
        member = member or ctx.author

        user_data = self._get_user_data(member.id, ctx.guild.id)

        if user_data["level"] == 0 and user_data["xp"] == 0:
            await ctx.respond("This user has no level data yet. Send a message to gain XP!")
            return
        
        try:
            img = await self.generate_level_card(member, user_data, ctx.guild.id)
            file = discord.File(fp=img, filename="rank.png")
            await ctx.respond(file=file)
        except Exception as e:
            logger.error(f"Error generating rank card: {e}")
            await ctx.respond("Failed to generate rank card. Please try again later.", ephemeral=True)
    
    @level.command(name="leaderboard", description="View the server leaderboard")
    async def leaderboard(self, ctx: discord.ApplicationContext, page: Option(int, description="Page number", required=False, default=1, min_value=1)): # type: ignore
        await ctx.defer()

        per_page = 10
        offset = (page - 1) * per_page

        query = """
            SELECT user_id, leve, total_xp
            FROM levels
            WHERE guild_id = %s
            ORDER BY level DESC, total_xp DESC
            LIMIT %s OFFSET %s
        """

        results = self.db.fetch_all_dict(query, (ctx.guild.id, per_page, offset))

        if not results:
            await ctx.respond("No leaderboard data available yet!")
            return
        
        embed = discord.Embed(title=f"{ctx.guild.name} Leaderboard", description=f"Top members by level and XP (Page {page})", color=discord.Color.gold())
        for idx, user_data in enumerate(results, start=offset + 1):
            user = ctx.guild.get_member(user_data["user_id"])
            if user:
                embed.add_field(name=f"{idx}. {user.display_name}", value=f"Level: {user_data['level']} | Total XP: {user_data['total_xp']: ,}", inline=False)
        embed.set_footer(text=f"Page {page}")
        await ctx.respond(embed=embed)

    async def generate_level_card(self,  member : discord.Member, user_data: dict, guild_id: int) -> io.BytesIO:
        width, height = 600, 180
        img = Image.new("RGB", (width, height), color=(54, 57, 63))
        draw = ImageDraw.Draw(img)

        try:
            font_bold = ImageFont.truetype("assets/fonts/arialbd.ttf", 30)
            font_regular = ImageFont.truetype("assets/fonts/arial.ttf", 22)
            font_small = ImageFont.truetype("assets/fonts/arial.ttf", 18)
        except:
            font_bold = ImageFont.load_default()
            font_regular = ImageFont.load_default()
            font_small = ImageFont.load_default()

        rank_query = """
            SELECT COUNT(*) + 1 as rank
            FROM levels
            WHERE guild_id = %s
            AND (level > %s OR (level = %s AND total_xp > %s))
        """
        rank_result = self.db.fetch_one(rank_query, (guild_id, user_data['level'], user_data['level'], user_data['total_xp']))
        rank = rank_result[0] if rank_result else 1

        username = f"{member.name}"
        if len(username) > 20:
            username = username[:17] + "..."
        draw.text((150, 30), username, font=font_bold, fill='white')

        draw.text((150, 70), f"Rank: #{rank}", font=font_regular, fill=(200, 200, 200))

        draw.text((320, 70), f"Level: {user_data['level']}", font=font_regular, fill='white')

        xp_needed = self.xp_needed(user_data['level'])
        draw.text((150, 100), f"XP: {user_data['xp']:,} / {xp_needed:,}", font=font_small, fill=(180, 180, 180))

        bar_x = 150
        bar_y = 140
        bar_width = 400
        bar_height = 20

        draw.rectangle([bar_x, bar_y, bar_x + bar_width, bar_y + bar_height], fill=(100, 100, 100))
        if xp_needed > 0:
            filled_width = int((user_data['xp'] / xp_needed) * bar_width)
            draw.rectangle([bar_x, bar_y, bar_x + filled_width, bar_y + bar_height], fill=(114, 137, 218))

        try:
            avatar_asset = member.display_avatar.with_size(128)
            avatar_bytes = io.BytesIO()
            await avatar_asset.save(avatar_bytes)
            avatar_bytes.seek(0)
            avatar_img = Image.open(avatar_bytes).resize((128, 128))

            mask = Image.new('L', (128, 128), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 128, 128), fill=255)

            img.paste(avatar_img, (10, 26), mask)
        except Exception as e:
            logger.error(f"Error loading avatar: {e}")

        output = io.BytesIO()
        img.save(output, 'PNG')
        output.seek(0)
        return output


def setup(bot):
    bot.add_cog(Leveling(bot))