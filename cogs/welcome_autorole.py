import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from datetime import datetime, timedelta
import asyncio
import re
import logging
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG

logger = logging.getLogger(__name__)

class WelcomeAutoRole(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = MySQLHelper(**MYSQL_CONFIG)
        self._ensure_tables()

    def _ensure_tables(self):
        create_welcome_settings = """
            CREATE TABLE IF NOT EXISTS welcome_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT UNIQUE NOT NULL,
                enabled BOOLEAN DEFAULT FALSE,
                channel_id BIGINT DEFAULT NULL,
                message_type ENUM('text', 'embed', 'card') DEFAULT 'embed',
                message_content TEXT DEFAULT NULL,
                embed_title TEXT DEFAULT NULL,
                embed_description TEXT DEFAULT NULL,
                embed_color VARCHAR(7) DEFAULT '#5865F2',
                embed_thumbnail BOOLEAN DEFAULT TRUE,
                embed_image_url TEXT DEFAULT NULL,
                embed_footer TEXT DEFAULT NULL,
                dm_enabled BOOLEAN DEFAULT FALSE,
                dm_message TEXT DEFAULT NULL,
                test_mode BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id)
            )
        """
        self.db.create_table(create_welcome_settings)
        
        create_goodbye_settings = """
            CREATE TABLE IF NOT EXISTS goodbye_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT UNIQUE NOT NULL,
                enabled BOOLEAN DEFAULT FALSE,
                channel_id BIGINT DEFAULT NULL,
                message_type ENUM('text', 'embed') DEFAULT 'embed',
                message_content TEXT DEFAULT NULL,
                embed_title TEXT DEFAULT NULL,
                embed_description TEXT DEFAULT NULL,
                embed_color VARCHAR(7) DEFAULT '#ED4245',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id)
            )
        """
        self.db.create_table(create_goodbye_settings)
        
        create_auto_roles = """
            CREATE TABLE IF NOT EXISTS auto_roles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                role_id BIGINT NOT NULL,
                bot_role BOOLEAN DEFAULT FALSE,
                delay_seconds INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_guild_role (guild_id, role_id, bot_role),
                INDEX idx_guild_id (guild_id)
            )
        """
        self.db.create_table(create_auto_roles)
        
        create_welcome_stats = """
            CREATE TABLE IF NOT EXISTS welcome_stats (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                welcome_sent BOOLEAN DEFAULT FALSE,
                dm_sent BOOLEAN DEFAULT FALSE,
                roles_assigned INT DEFAULT 0,
                INDEX idx_guild_id (guild_id),
                INDEX idx_user_id (user_id),
                INDEX idx_join_date (join_date)
            )
        """
        self.db.create_table(create_welcome_stats)
        
        create_member_tracking = """
            CREATE TABLE IF NOT EXISTS member_tracking (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                action_type ENUM('JOIN', 'LEAVE') NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id),
                INDEX idx_timestamp (timestamp)
            )
        """
        self.db.create_table(create_member_tracking)
        
        logger.info("Welcome & Auto-Role tables ensured")

    def _get_welcome_settings(self, guild_id: int) -> dict:
        query = "SELECT * FROM welcome_settings WHERE guild_id = %s"
        result = self.db.fetch_one_dict(query, (guild_id))

        if not result:
            self.db.insert("welcome_settings", {"guild_id": guild_id})
            return {
                "enabled": False,
                "channel_id": None,
                "message_type": "embed",
                "message_content": None,
                "embed_title": "Welcome to {server}!",
                "embed_description": "Welcome {mention}! You are member #{member_count}.",
                "embed_color": "#5865F2",
                "embed_thumbnail": True,
                "embed_image_url": None,
                "embed_footer": None,
                "dm_enabled": False,
                "dm_message": None,
                "test_mode": False
            }
        
        return result
    
    def _get_goodbye_settings(self, guild_id: int) -> dict:
        query = "SELECT * FROM goodbye_settings WHERE guild_id = %s"
        result = self.db.fetch_one_dict(query, (guild_id))

        if not result:
            self.db.insert("goodbye_settings", {"guild_id": guild_id})
            return {
                "enabled": False,
                "channel_id": None,
                "message_type": "embed",
                "message_content": None,
                "embed_title": "Goodbye!",
                "embed_description": "{user} has left the server.",
                "embed_color": "#ED4245"
            }
        
        return result
    
    def _get_auto_roles(self, guild_id: int, for_bots: bool = False) -> list:
        query = "SELECT role_id, delay_seconds FROM auto_roles WHERE guild_id = %s AND bot_role = %s"
        return self.db.fetch_all_dict(query, (guild_id, for_bots))

    def _format_message(self, text: str, member: discord.Member, guild: discord.Guild) -> str:
        if not text:
            return None
        
        replacements = {
            "{user}": member.name,
            "{mention}": member.mention,
            "{server}": guild.name,
            "{member_count}": str(guild.member_count),
            "{username}": member.name,
            "{discriminator}": member.discriminator,
            "{id}": str(member.id),
            "{guild}": guild.name
        }

        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)

        return text
    
    def _parse_color(self, color_str: str) -> discord.Color:
        try:
            if color_str.startswith('#'):
                color_str = color_str[1:]
            return discord.Color(int(color_str, 16))
        except:
            return discord.Color.blue()
    
    async def _send_welcome_message(self, member: discord.Member, settings: dict):
        if not settings["enabled"] or not settings["channel_id"]:
            return False
        
        channel = member.guild.get_channel(settings["channel_id"])
        if not channel:
            logger.warning(f"Welcome channel {settings['channel_id']} not found in guild {member.guild.id}")
            return False
        
        try:
            if settings["message_type"] == "text":
                message = self._format_message(settings["message_content"], member, member.guild)
                if message:
                    await channel.send(message)
            elif settings["message_type"] == "embed":
                embed = discord.Embed(title=self._format_message(settings["embed_title"], member, member.guild), description=self._format_message(settings["embed_description"], member, member.guild), color=self._parse_color(settings["embed_color"]), timestamp=datetime.utcnow())

                if settings["embed_thumbnail"]:
                    embed.set_thumbnail(url=member.display_avatar.url)
                
                if settings["embed_image_url"]:
                    embed.set_image(url=settings["embed_image_url"])
                
                if settings["embed_footer"]:
                    footer_text = self._format_message(settings["embed_footer"], member, member.guild)
                    embed.set_footer(text=footer_text)
                
                await channel.send(embed=embed)
            return True
        except discord.Forbidden:
            logger.error(f"Missing permissions to send welcome message in guild {member.guild.id}")
            return False
        except Exception as e:
            logger.error(f"Error sending welcome message: {e}")
            return False
    
    async def _send_dm_message(self, member: discord.Member, settings: dict):
        if not settings["dm_enabled"] or not settings["dm_message"]:
            return False
        
        try:
            dm_text = self._format_message(settings["dm_message"], member, member.guild)
            await member.send(dm_text)
            return True
        except discord.Forbidden:
            logger.info(f"Could not DM welcome message to {member}")
            return False
        except Exception as e:
            logger.error(f"Error sending welcome DM: {e}")
            return False
        
    async def _send_goodbye_message(self, member: discord.Member, settings: dict):
        if not settings["enabled"] or not settings["channel_id"]:
            return False
        
        channel = member.guild.get_channel(settings["channel_id"])
        if not channel:
            logger.warning(f"Goodbye channel {settings['channel_id']} not found in guild {member.guild.id}")
            return False
        
        try:
            if settings["message_type"] == "text":
                message = self._format_message(settings["message_content"], member, member.guild)
                if message:
                    await channel.send(message)
            
            elif settings["message_type"] == "embed":
                embed = discord.Embed(title=self._format_message(settings["embed_title"], member, member.guild), description=self._format_message(settings["embed_description"], member, member.guild), color=self._parse_color(settings["embed_color"]), timestamp=datetime.utcnow())

                embed.set_thumbnail(url=member.display_avatar.url)

                await channel.send(embed=embed)

            return True
        except discord.Forbidden:
            logger.error(f"Missing permissions to send goodbye message in guild {member.guild.id}")
            return False
        except Exception as e:
            logger.error(f"Error sending goodbye message: {e}")
            return False
    
    async def _assign_auto_roles(self, member: discord.Member):
        auto_roles = self._get_auto_roles(member.guild.id, for_bots=member.bot)

        if not auto_roles:
            return 0
        
        roles_assigned = 0

        for role_data in auto_roles:
            role = member.guild.get_role(role_data["role_id"])
            if not role:
                continue

            if role_data["delay_seconds"] > 0:
                await asyncio.sleep(role_data["delay_seconds"])

            try:
                await member.add_roles(role, reason="Auto-role on join")
                roles_assigned += 1
                logger.info(f"Auto-assigned role {role.name} to {member} in {member.guild.name}")
            except discord.Forbidden:
                logger.warning(f"Missing permissions to assign auto-role {role.name} in guild {member.guild.id}")
            except Exception as e:
                logger.error(f"Error assigning auto-role: {e}")
        
        return roles_assigned

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        self.db.insert("member_tracking", {
            "guild_id": member.guild.id,
            "user_id": member.id,
            "action_type": "JOIN"
        })

        welcome_settings = self._get_welcome_settings(member.guild.id)

        welcome_sent = await self._send_welcome_message(member, welcome_settings)

        dm_sent = await self._send_dm_message(member, welcome_settings)

        roles_assigned = await self._assign_auto_roles(member)

        self.db.insert("welcome_stats", {
            "guild_id": member.guild.id,
            "user_id": member.id,
            "welcome_sent": welcome_sent,
            "dm_sent": dm_sent,
            "roles_assigned": roles_assigned
        })
    
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        self.db.insert("member_tracking", {
            "guild_id": member.guild.id,
            "user_id": member.id,
            "action_type": "LEAVE"
        })

        goodbye_settings = self._get_goodbye_settings(member.guild.id)

        await self._send_goodbye_message(member, goodbye_settings)

    welcome = SlashCommandGroup(name="welcome", description="All welcome and auto role management.")

    @welcome.command(name="stats", description="View welcome system statistics")
    @commands.has_guild_permissions(administrator=True)
    async def welcome_stats(self, ctx: discord.ApplicationContext, days: Option(int, description="Number of days to analyze", required=False, default=7, min_value=1, max_value=90)): #type: ignore
        await ctx.defer()

        cutoff_date = datetime.utcnow() - timedelta(days=days)

        join_query = """
            SELECT COUNT(*) as count
            FROM member_tracking
            WHERE guild_id = %s AND action_type = 'JOIN' AND timestamp >= %s
        """
        joins = self.db.fetch_one(join_query, (ctx.guild.id, cutoff_date))
        join_count = joins[0] if joins else 0

        leave_query = """
            SELECT COUNT(*) as count
            FROM member_tracking
            WHERE guild_id = %s AND action_type = 'LEAVE' AND timestamp >= %s
        """
        leaves = self.db.fetch_one(leave_query, (ctx.guild.id, cutoff_date))
        leave_count = leaves[0] if leaves else 0

        welcome_query = """
            SELECT
                SUM(welcome_sent) as welcomes,
                SUM(dm_sent) as dms,
                SUM(roles_assigned) as roles
            FROM welcome_stats
            WHERE guild_id = %s AND join_date >= %s
        """
        stats = self.db.fetch_one(welcome_query, (ctx.guild.id, cutoff_date))

        welcomes_sent = stats[0] if stats and stats[0] else 0
        dms_sent = stats[1] if stats and stats[1] else 0
        roles_assigned = stats[2] if stats and stats[2] else 0

        net_growth = join_count - leave_count

        embed = discord.Embed(title="Welcome System Statistics", description=f"Statistics for the last {days} days", color=discord.Color.blue(), timestamp=datetime.utcnow())
        embed.add_field(name="Memebers Joined", value=f"{join_count:,}", inline=True)
        embed.add_field(name="Members Left", value=f"{leave_count:,}", inline=True)
        embed.add_field(name="Net Grwoth", value=f"{net_growth:+,}", inline=True)

        embed.add_field(name="Welcomes Sent", value=f"{welcomes_sent:,}", inline=True)
        embed.add_field(name="DMs Sent", value=f"{dms_sent:,}", inline=True)
        embed.add_field(name="Roles Assigned", value=f"{roles_assigned:,}", inline=True)

        embed.set_footer(text=f"Current member count: {ctx.guild.member_count:,}")

        await ctx.respond(embed=embed)
def setup(bot):
    bot.add_cog(WelcomeAutoRole(bot))