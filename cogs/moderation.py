import discord
from discord.ext import commands
from discord.commands import SlashCommandGroup, Option
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG
from datetime import datetime, timedelta
import uuid
import logging

logger = logging.getLogger(__name__)

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = MySQLHelper(**MYSQL_CONFIG)
        self._ensure_tables()

    def _ensure_tables(self):
        create_punishment_table = """
            CREATE TABLE IF NOT EXISTS punishment_actions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                punishment_id VARCHAR(36) UNIQUE NOT NULL,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                action_type VARCHAR(50) NOT NULL,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id),
                INDEX idx_user_id (user_id),
                INDEX idx_punishment_id (punishment_id)
                )
            """
        self.db.create_table(create_punishment_table)

        logger.info("Moderation tables ensured")

    def _generate_punishment_id(self) -> str:
        return str(uuid.uuid4())[:8].upper()
    
    def _get_mod_log_channel(self, guild_id: int) -> int:
        query = "SELECT mod_log_channel_id FROM guilds WHERE guild_id = %s"
        result = self.db.fetch_one(query, (guild_id,))

        if result and result[0]:
            return result[0]
        return None
    
    def _log_punishment(self, punishment_id: str, guild_id: int, user_id: int, moderator_id: int, action_type: str, reason: str) -> bool:
        data = {
            "punishment_id": punishment_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "moderator_id": moderator_id,
            "action_type": action_type,
            "reason": reason
        }
        
        result = self.db.insert("punishment_actions", data)
        return result is not None
    
    async def _send_mod_log(self, guild: discord.Guild, punishment_id: str, action_type: str, user: discord.User, moderator: discord.Member, reason: str, duration: str = None) -> None:
        channel_id = self._get_mod_log_channel(guild.id)

        if not channel_id:
            logger.info(f"No mod log channnel configured for guild {guild.id}")
            return
        
        channel = guild.get_channel(channel_id)

        if not channel:
            logger.warning(f"Mod log channel {channel_id} not found in guild {guild.id}")
            return
        
        embed = discord.Embed(title=f"🔨 {action_type.upper()}", color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)\n{user.name}#{user.discriminator}", inline=True)
        embed.add_field(name="Moderator", value=f"{moderator.mention} (`{moderator.id}`)\n{moderator.name}#{moderator.discriminator}", inline=True)
        embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=True)
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)

        if user.avatar:
            embed.set_thumbnail(url=user.avatar.url)
        
        embed.set_footer(text=f"Guild ID: {guild.id}")

        try:
            await channel.send(embed=embed)
            logger.info(f"Mod log sent for punishment {punishment_id} in guild {guild.id}")
        except discord.Forbidden:
            logger.error(f"Missing permissions to send mod log in guild {guild.id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send mod log: {e}")
    
    def _parse_duration(self, duration: str, unit: str) -> timedelta:
        unit_mapping = {
            "minutes": timedelta(minutes=duration),
            "hours": timedelta(hours=duration),
            "days": timedelta(days=duration),
            "weeks": timedelta(weeks=duration)
        }
        return unit_mapping.get(unit)

    def _format_duration(self, duration: int, unit: str) -> str:
        if duration == 1:
            unit = unit.rstrip('s')
        return f"{duration} {unit}"

    mod = SlashCommandGroup("mod", "Moderation Commands")

    @mod.command(name="ban", description="Ban a member from the server")
    @commands.has_permissions(ban_members=True)
    @commands.bot_has_permissions(ban_members=True)
    async def ban(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member you want to ban", required=True), reason: Option(str, description="Reason for the ban", required=False, default="No reason provided"), delete_messages: Option(int, description="Delete messages from the last X days (0-7)", required=False, default=0, min_value=0, max_value=7)): # type: ignore
        await ctx.defer()

        if member.id == ctx.guild.owner_id:
            await ctx.respond("You cannot ban the server owner!", ephemeral=True)
            return
        
        if member.id == ctx.author.id:
            await ctx.respond("You cannot ban yourself!", ephemeral=True)
            return
        
        if member.id == self.bot.user.id:
            await ctx.respond("I cannot ban myself!", ephemeral=True)
            return
        
        if ctx.author.id != ctx.guild.owner_id:
            if member.top_role >= ctx.author.top_role:
                await ctx.respond("You cannot ban someone with a higher or equal role!", ephemeral=True)
                return
            
        if member.top_role >= ctx.guild.me.top_role:
            await ctx.respond("I cannot ban someone with a higher or equal role than me!", ephemeral=True)
            return
        
        punishment_id = self._generate_punishment_id()

        try:
            dm_embed = discord.Embed(title=f"You have been banned from {ctx.guild.name}", color=discord.Color.red(), timestamp=datetime.utcnow())
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            dm_embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=False)
            dm_embed.set_footer(text="If you believe this was a mistake, please contact the server moderators.")

            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False
            logger.info(f"Could not DM user {member.id} about their ban")
        
        try:
            await ctx.guild.ban(member, reason=f"[{punishment_id}] {reason} | Banned by {ctx.author}", delete_message_seconds=delete_messages * 86400)
        except discord.Forbidden:
            await ctx.respond("I don't have permission to ban this user!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to ban user: {e}", ephemeral=True)
            logger.error(f"Failed to ban user {member.id}: {e}")
            return
        
        log_success = self._log_punishment(punishment_id=punishment_id, guild_id=ctx.guild.id, user_id=member.id, moderator_id=ctx.author.id, action_type="BAN", reason=reason)

        if not log_success:
            logger.error(f"Failed to log punishment {punishment_id} to database")
        
        await self._send_mod_log(guild=ctx.guild, punishment_id=punishment_id, action_type="BAN", user=member, moderator=ctx.author, reason=reason)

        guild_check = self.db.fetch_one("SELECT id FROM guilds where guild_id = %s", (ctx.guild.id,))
        if not guild_check:
            self.db.insert("guilds", {"guild_id": ctx.guild.id})
        
        confirm_embed = discord.Embed(title="Member Banned", color=discord.Color.green(), timestamp=datetime.utcnow())
        confirm_embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        confirm_embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=True)
        confirm_embed.add_field(name="Reason", value=reason, inline=False)
        if dm_sent:
            confirm_embed.set_footer(text="User was notified via DM")
        else:
            confirm_embed.set_footer(text="Could not notify user via DM")
        
        await ctx.respond(embed=confirm_embed)
        logger.info(f"User {member.id} banned from guild {ctx.guild.id} by {ctx.author.id} - ID: {punishment_id}")

    @ban.error
    async def ban_error(self, ctx: discord.ApplicationContext, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.respond("You don't have permission to ban members!", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.respond("I don't have permission to ban members!", ephemeral=True)
        else:
            await ctx.respond(f"An error occured: {str(error)}", ephemeral=True)
            logger.error(f"Ban command error: {error}")
    
    @mod.command(name="kick", description="Kick a member from the server")
    @commands.has_permissions(kick_members=True)
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx : discord.ApplicationContext, member: Option(discord.Member, description="The member you want to kick.", required=True), reason: Option(str, description="Reason for the kick.", required=False, default="No reason provided.")): # type: ignore
        await ctx.defer()

        if member.id == ctx.guild.owner_id:
            await ctx.respond("You cannot kick the server owner!", ephemeral=True)
            return
        
        if member.id == ctx.author.id:
            await ctx.respond("You cannot kick yourself!", ephemeral=True)
            return
        
        if member.id == self.bot.user.id:
            await ctx.respond("I cannot kick myself!", ephemeral=True)

        if ctx.author.id != ctx.guild.owner_id:
            if member.top_role >= ctx.author.top_role:
                await ctx.respond("You cannot kick someone with a higher or equal role!", ephemeral=True)
                return
            
        if member.top_role >= ctx.guild.me.top_role:
            await ctx.respond("I cannot kick someone with a higher or equal role than me!", ephemeral=True)
            return
        
        punishment_id = self._generate_punishment_id()

        try:
            dm_embed = discord.Embed(title=f"You have been kicked from {ctx.guild.name}", color=discord.Color.orange(), timestamp=datetime.utcnow())
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            dm_embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=False)
            dm_embed.set_footer(text="If you believe this was a mistake, please contact the server moderators.")
            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False
            logger.info(f"Could not DM user {member.id} about their kick.")

        try:
            await ctx.guild.kick(member, reason=f"[{punishment_id}] {reason} | Kicked by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond("I don't have permission to kick this user!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to kick user: {e}", ephemeral=True)
            logger.error(f"Failed to kick user {member.id}: {e}")
            return
        
        log_success = self._log_punishment(punishment_id=punishment_id, guild_id=ctx.guild.id, user_id=member.id, moderator_id=ctx.author.id, action_type="KICK", reason=reason)

        if not log_success:
            logger.error(f"Failed to log punishment {punishment_id} to database.")

        await self._send_mod_log(guild=ctx.guild, punishment_id=punishment_id, action_type="KICK", user=member, moderator=ctx.author, reason=reason)

        guild_check = self.db.fetch_one("SELECT id FROM guilds WHERE guild_id = %s", (ctx.guild.id,))
        if not guild_check:
            self.db.insert("guilds", {"guild_id": ctx.guild.id})
        
        confirm_embed = discord.Embed(title="Member Kicked", color=discord.Color.green(), timestamp=datetime.utcnow())
        confirm_embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        confirm_embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=True)
        confirm_embed.add_field(name="Reason", value=reason, inline=False)

        if dm_sent:
            confirm_embed.set_footer(text="User was notified via DM")
        else:
            confirm_embed.set_footer(text="Could not notify user via DM")

        await ctx.respond(embed=confirm_embed)
        logger.info(f"User {member. id} kicked from guild {ctx. guild.id} by {ctx. author.id} - ID: {punishment_id}")
    
    @kick.error
    async def kick_error(self, ctx: discord.ApplicationContext, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.respond("You don't have permission to kick members!", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.respond("I don't have permission to kick members!", ephemeral=True)
        else:
            await ctx.respond(f"An error occured: {str(error)}", ephemeral=True)
            logger.error(f"Kick command error: {error}")

    @mod.command(name="timeout", description="Timeout a member")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def timeout(self, ctx : discord.ApplicationContext, member : Option(discord.Member, description="The member you want to timeout.", required=True), duration: Option(int, description="Duration of the timeout", required=True, min_value=1, max_value=40320), unit: Option(str, description="Time unit", required=True, choices=["minutes", "hours", "days", "weeks"]), reason : Option(str, description="Reason for the timeout", required=False, default="No reason provided.")): # type: ignore
        await ctx.defer()

        if member.id == ctx.guild.owner_id:
            await ctx.respond("You cannot timeout the server owner!", ephemeral=True)
            return
        
        if member.id == ctx.author.id:
            await ctx.respond("You cannot timeout yourself!", ephemeral=True)
            return
        
        if member.id == self.bot.user.id:
            await ctx.respond("I cannot timeout myself!", ephemeral=True)
            return
        
        if ctx.author.id != ctx.guild.owner_id:
            if member.top_role >= ctx.author.top_role:
                await ctx.respond("You cannot timeout someone with a higher or equal role!", ephemeral=True)
                return

        if member.top_role >= ctx.guild.me.top_role:
            await ctx.respond("I cannot timeout someone with a higher or equal role than me!", ephemeral=True)
            return
        
        duration_delta = self._parse_duration(duration, unit)

        if not duration_delta:
            await ctx.respond("Invalid duration unit!", ephemeral=True)
            return
        
        if duration_delta > timedelta(days=28):
            await ctx.respond("Timeout duration cannot exceed 28 days!", ephemeral=True)
            return
        
        timeout_until = datetime.utcnow() + duration_delta

        punishment_id = self._generate_punishment_id()

        duration_str = self._format_duration(duration, unit)

        try:
            dm_embed = discord.Embed(title=f"You have been timed out in {ctx.guild.name}", color=discord.Color.yellow(), timestamp=datetime.utcnow())
            dm_embed.add_field(name="Duration", value=duration_str, inline=False)
            dm_embed.add_field(name="Reason", value=reason, inline=False)
            dm_embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=False)
            dm_embed.add_field(name="Timeout Ends", value=f"<t:{int(timeout_until.timestamp())}:F>", inline=False)
            dm_embed.set_footer(text="If you believe this was a mistake. please contact the server moderators.")

            await member.send(embed=dm_embed)
            dm_sent = True
        except (discord.Forbidden, discord.HTTPException):
            dm_sent = False
            logger.info(f"Could not DM user {member.id} about their timeout.")
        
        try:
            await member.timeout_for(duration_delta, reason=f"[{punishment_id}] {reason} | Timed out by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond("I don't have permission to timeout this user!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to timeout user: {e}", ephemeral=True)
            logger.error(f"Failed to timeout user {member.id}: {e}")
            return
        
        log_success = self._log_punishment(punishment_id=punishment_id, guild_id=ctx.guild.id, user_id=member.id, moderator_id=ctx.author.id, action_type="TIMEOUT", reason=reason, duration=duration_str)
        if not log_success:
            logger.error(f"Failed to log punishment {punishment_id} to database.")

        await self._send_mod_log(guild=ctx.guild, punishment_id=punishment_id, action_type="TIMEOUT", user=member, moderator=ctx.author, reason=reason, duration=duration_str)

        guild_check = self.db.fetch_one("SELECT id FROM guilds WHERE guild_id = %s", (ctx.guild.id))
        if not guild_check:
            self.db.insert("guilds", {"guild_id": ctx.guild.id})

        confirm_embed = discord.Embed(title="Member Timed Out", color=discord.Color.green(), timestamp=datetime.utcnow())
        confirm_embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        confirm_embed.add_field(name="Punishment ID", value=f"`{punishment_id}`", inline=True)
        confirm_embed.add_field(name="Duration", value=duration_str, inline=True)
        confirm_embed.add_field(name="Timeout Ends", value=f"<t:{int(timeout_until.timestamp())}:F> (<t:{int(timeout_until.timestamp)}:R>)", inline=False)
        confirm_embed.add_field(name="Reason", value=reason, inline=False)

        if dm_sent:
            confirm_embed.set_footer(text="User was notified via DM")
        else:
            confirm_embed.set_footer(text="Could not notify user via DM")

        await ctx.respond(embed=confirm_embed)
        logger.info(f"User {member.id} timed out in guild {ctx.guild.id} by {ctx.author.id} for {duration_str} - ID: {punishment_id}")

    @timeout.error
    async def timeout_error(self, ctx: discord.ApplicationContext, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.respond("You don't have permission to timeout members!", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.respond("I don't have permission to timeout members!", ephemeral=True)
        else:
            await ctx.respond(f"An error occured: {str(error)}", ephemeral=True)
            logger.error(f"Timeout command error: {error}")
    
    @mod.command(name="untimeout", description="Remove a timeout from a member.")
    @commands.has_permissions(moderate_members=True)
    @commands.bot_has_permissions(moderate_members=True)
    async def untimeout(self, ctx: discord.ApplicationContext, member : Option(discord.Member, description="The member to remove timeout from", required=True), reason: Option(str, description="Reason for removing the timeout", required=False, default="No reason provided.")): # type: ignore
        await ctx.defer()

        if not member.is_timed_out():
            await ctx.respond("This member is not timed out!", ephemeral=True)
            return
        
        try:
            await member.remove_timeout(reason=f"{reason} | Removed by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond("I don't have permission to remove timeouts!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to remove timeout: {e}", ephemeral=True)
            logger.error(f"Failed to remove timeout from user {member.id}: {e}")
            return
        
        confirm_embed = discord.Embed(title="Timeout Removed", color=discord.Color.green(), timestamp=datetime.utcnow())
        confirm_embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        confirm_embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        confirm_embed.add_field(name="Reason", value=reason, inline=False)

        await ctx.respond(embed=confirm_embed)
        logger.info(f"Timeout removed from user {member.id} in guild {ctx.guild.id} by {ctx.author.id}")
    
    @untimeout.error
    async def untimeout_error(self, ctx : discord.ApplicationContext, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.respond("You don't have permission to remove timeouts!", ephemeral=True)
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.respond("I don't have permission to remove timeouts!", ephemeral=True)
        else:
            await ctx.respond(f"An error occurred: {str(error)}", ephemeral=True)
            logger.error(f"Untimeout command error: {error}")
    
    
    

def setup(bot):
    bot.add_cog(Moderation(bot))