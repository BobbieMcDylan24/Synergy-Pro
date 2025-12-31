import discord
from discord.ext import commands, tasks
from discord.commands import SlashCommandGroup, Option
from datetime import datetime, timezone, timedelta
from collections import defaultdict, deque
import re
import json
import logging
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG

logger = logging.getLogger(__name__)

class GiveRolesBackView(discord.ui.View):
    def __init__(self, original_roles, user : discord.Member):
        super().__init__(timeout=None)
        self.original_roles = original_roles
        self.user = user

    @discord.ui.button(label="Give Roles Back", style=discord.ButtonStyle.green)
    async def button_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await self.user.edit(roles=self.original_roles, reason=f"Roles restored by {interaction.user.display_name}")
            await interaction.response.send_message(f"Restored roles to {self.user.mention}")
        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to assign some of the roles.")
        except Exception as e:
            await interaction.response.send_message(f"Error restoring roles: `{e}`")
            logger.error(f"Security: Error restoring roles {e}")


class DisableRaidButton(discord.ui.View):
    def __init__(self, cog, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.cog = cog

    @discord.ui.button(label="Disable Raid Mode", style=discord.ButtonStyle.red)
    async def button_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        self.cog.raid_mode[self.guild_id] = False
        await interaction.response.send_message("Raid Mode disabled.", ephemeral=False)

class UndoPunishment(discord.ui.View):
    def __init__(self, member : discord.Member):
        super().__init__(timeout=None)
        self.member = member

    @discord.ui.button(label="Undo Punishment", style=discord.ButtonStyle.red)
    async def button_callback(self, button: discord.ui.Button, interaction: discord.Interaction):
        try:
            await self.member.timeout(None, reason=f"Punishment removed by {interaction.user.display_name}")
            await interaction.response.send_message(f"Timeout removed for {self.member.mention}")
        except Exception as e:
            interaction.response.send_message(f"Failed to undo: {e}", ephemeral=True)

class Security(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = MySQLHelper(**MYSQL_CONFIG)
        self.joins = {}
        self.raid_mode = {}
        self.thresholds = defaultdict(lambda: {
            'ban': (3, 10),
            "kick": (3, 10),
            "channel_delete": (2, 10),
            "role_delete": (2, 10),
            "emoji_delete": (2, 10)
        })
        self.action_logs = defaultdict(lambda: defaultdict(deque))
        self.user_messages = defaultdict(deque)
        self.join_times = {}
        self.original_permissions = {}
        self.dm_tracker = defaultdict(list)
        self._ensure_tables()

    def _ensure_tables(self):
        create_whitelist_table = """
            CREATE TABLE IF NOT EXISTS security_whitelist (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                guild_id BIGINT NOT NULL,
                added_by BIGINT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_guild (user_id, guild_id),
                INDEX idx_user_id (user_id),
                INDEX idx_guild_id (guild_id)
            )
        """
        self.db.create_table(create_whitelist_table)
        
        create_backup_table = """
            CREATE TABLE IF NOT EXISTS server_backups (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT UNIQUE NOT NULL,
                backup_data LONGTEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id)
            )
        """
        self.db.create_table(create_backup_table)
        
        create_settings_table = """
            CREATE TABLE IF NOT EXISTS security_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT UNIQUE NOT NULL,
                security_log_channel_id BIGINT DEFAULT NULL,
                anti_raid_enabled BOOLEAN DEFAULT TRUE,
                anti_nuke_enabled BOOLEAN DEFAULT TRUE,
                anti_spam_enabled BOOLEAN DEFAULT TRUE,
                anti_alt_enabled BOOLEAN DEFAULT TRUE,
                anti_suspicious_links_enabled BOOLEAN DEFAULT TRUE,
                anti_selfbot_enabled BOOLEAN DEFAULT TRUE,
                min_account_age_days INT DEFAULT 7,
                raid_join_threshold INT DEFAULT 10,
                raid_time_window INT DEFAULT 15,
                spam_message_threshold INT DEFAULT 5,
                spam_time_window INT DEFAULT 5,
                spam_duplicate_threshold INT DEFAULT 3,
                spam_mention_threshold INT DEFAULT 5,
                spam_link_threshold INT DEFAULT 4,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id)
            )
        """
        self.db.create_table(create_settings_table)

        logger.info("Security tables ensured")

    def _get_security_settings(self, guild_id: int) -> dict:
        query = "SELECT * FROM security_settings WHERE guild_id = %s"
        result = self.db.fetch_one_dict(query, (guild_id,))

        if not result:
            self.db.insert("security_settings", {"guild_id": guild_id})
            return {
                "security_log_channel_id": None,
                "anti_raid_enabled": True,
                "anti_nuke_enabled": True,
                "anti_spam_enabled": True,
                "anti_alt_enabled": True,
                "anti_suspicious_links_enabled": True,
                "anti_selfbot_enabled": True,
                "min_account_age_days": 7,
                "raid_join_threshold": 10,
                "raid_time_window": 15,
                "spam_message_threshold": 5,
                "spam_time_window": 5,
                "spam_duplicate_threshold": 3,
                "spam_mention_threshold": 5,
                "spam_link_threshold": 4 
            }
        
        return result
    
    def _get_security_log_channel(self, guild_id: int) -> int:
        settings = self._get_security_settings(guild_id)
        return settings.get("security_log_channel_id")

    def _is_whitelisted(self, user_id: int, guild_id: int) -> bool:
        query = "SELECT id FROM security_whitelist WHERE user_id %s AND guild_id = %s"
        result = self.db.fetch_one(query, (user_id, guild_id))
        return result is not None
    
    def _add_to_whitelist(self, user_id: int, guild_id: int, added_by: int = None) -> bool:
        return self.db.insert("security_whitelist", {
            "user_id": user_id,
            "guild_id": guild_id,
            "added_by": added_by
        }) is not None
    
    def _remove_from_whitelist(self, user_id: int, guild_id: int) -> bool:
        return self.db.delete("security_whitelist", "user_id = %s AND guild_id = %s", (user_id, guild_id))
    
    def _save_backup(self, guild_id: int, backup_data: dict) -> bool:
        backup_json = json.dumps(backup_data)

        existing = self.db.fetch_one("SELECT id FROM server_backups WHERE guild_id = %s", (guild_id,))

        if existing:
            return self.db.update("server_backups", {"backup_data": backup_json}, "guild_id = %s", (guild_id,))
        else:
            return self.db.insert("server_backups", {"guild_id": guild_id, "backup_data": backup_json}) is not None
    
    def _get_backup(self, guild_id: int) -> dict:
        query = "SELECT backup_data FROM server_backups WHERE guild_id = %s"
        result = self.db.fetch_one(query, (guild_id))

        if result and result[0]:
            return json.loads(result[0])
        return None
    
    async def _send_security_log(self, guild_id: int, embed: discord.Embed, view: discord.ui.View = None):
        channel_id = self._get_security_log_channel(guild_id)

        if not channel_id:
            logger.info(f"No security log channel configured for guild {guild_id}")
            return False
        
        channel = self.bot.get_channel(channel_id)

        if not channel:
            logger.warning(f"Security log channel {channel_id} not found for guild {guild_id}")
            return False
        
        try:
            if view:
                await channel.send(embed=embed, view=view)
            else:
                await channel.send(embed=embed)
            return True
        except discord.Forbidden:
            logger.error(f"Missing permissions to send to security log in guild {guild_id}")
            return False
        except Exception as e:
            logger.error(f"Error sending security log: {e}")
            return False
    
    @tasks.loop(seconds=5)
    async def check_raid_loop(self):
        now = datetime.utcnow()

        for guild_id, timestamps in list(self.joins.items()):
            settings = self._get_security_settings(guild_id)

            if not settings["anti_raid_enabled"]:
                continue

            raid_threshold = settings["raid_join_threshold"]
            time_window = settings["raid_time_windoww"]

            self.joins[guild_id] = deque([t for t in timestamps if (now - t).total_seconds() <= time_window])

            if len(self.joins[guild_id]) >= raid_threshold and not self.raid_mode.get(guild_id, False):
                self.raid_mode[guild_id] = True

                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue

                view = DisableRaidButton(self, guild_id)
                embed = discord.Embed(title="Security Alert", description="Potential raid detected! ", color=discord.Color.red())
                embed.add_field(name="Alert: ", value="Raid Detected", inline=False)
                embed.add_field(name="Details: ", value=f"{len(self.joins[guild_id])} members joined in {time_window} seconds", inline=False)
                embed.add_field(name="Action: ", value="Kicking new joining members until disabled by administrator", inline=False)
                await self._send_security_log(guild_id, embed, view)
    
    @tasks.loop(seconds=5)
    async def watch_audit_log(self):
        now = datetime.now(timezone.utc)

        for guild in self.bot.guilds:
            settings = self._get_security_settings(guild.id)

            if not settings["anti_nuke_enabled"]:
                continue

            for action_type, discord_action in {
                'ban': discord.AuditLogAction.ban,
                'kick': discord.AuditLogAction.kick,
                "channel_delete": discord.AuditLogAction.channel_delete,
                "role_delete": discord.AuditLogAction.role_delete,
                "emoji_delete": discord.AuditLogAction.emoji_delete
            }.items():
                try:
                    async for entry in guild.audit_logs(limit=5, action=discord_action):
                        if (now - entry.created_at).total_seconds() > 10:
                            continue

                        user_id = entry.user.id

                        if self._is_whitelisted(user_id, guild.id):
                            continue

                        self.action_logs[guild.id][user_id].append((action_type, now))

                        count, seconds = self.thresholds[guild.id][action_type]
                        recent = [t for a, t in self.action_logs[guild.id][user_id] if a == action_type and (now - t).total_seconds() <= seconds]
                        
                        if len(recent) >= count:
                            await self.take_nuke_action(guild, entry.user, action_type, len(recent))
                            self.action_logs[guild.id][user_id].clear()
                except Exception as e:
                    logger.error(f"Error reading audit logs for guild {guild.id}: {e}")
    
    async def take_nuke_action(self, guild: discord.Guild, user: discord.Member, action_type: str, count: int):
        try:
            original_roles = [role for role in user.roles if role.name != "@everyone"]
            await user.edit(roles=[], reason=f"Anti-Nuke: {count} {action_type}s in short time.")

            embed = discord.Embed(title="Anti-Nuke Alert", description="Suspicious bulk actions detected!", colour=discord.Colour.red())
            embed.add_field(name="Member: ", value=f"{user.mention} (`{user.id}`)", inline=False)
            embed.add_field(name="Alert: ", value=f"{count} `{action_type}` actions in short time", inline=False)
            embed.add_field(name="Action: ", value="All roles removed", inline=False)
            embed.set_footer(text=f"User: {user.name}")

            view = GiveRolesBackView(original_roles, user)
            await self._send_security_log(guild.id, embed, view)
        except discord.Forbidden:
            await logger.warning(f"Tried to strip roles from {user.mention} but lacked permissions")

    @commands.Cog.listener()
    async def on_message(self, message : discord.Message):
        if message.author.bot:
            return
        
        if isinstance(message.channel, discord.DMChannel):
            now = datetime.now(timezone.utc)
            self.dm_tracker[message.author.id].append(now)
            self.dm_tracker[message.author.id] = [t for t in self.dm_tracker[message.author.id] if (now - t).seconds < 15]

            if len(self.dm_tracker[message.author.id]) > 5:
                try:
                    await message.author.send("You are sending messages too quickly. This may be considered spam!")
                except Exception as e:
                    logger.error(f"DM spam error: {e}")
            return

        if not message.guild:
            return
        
        settings = self._get_security_settings(message.guild.id)

        if settings["anti_spam_enabled"]:
            await self._check_spam(message, settings)

        if settings["anti_suspicious_links_enabled"]:
            await self._check_suspicious_links(message, settings)

        if settings["anti_selfbot_enabled"]:
            await self._check_selfbot(message, settings)
    
    async def _check_spam(self, message: discord.Message, settings: dict):
        author = message.author
        now = datetime.utcnow()
        msg_log = self.user_messages[author.id]
        msg_log.append((message, now))

        interval = settings["spam_time_window"]
        with msg_log and (now - msg_log[0][1]).total_seconds() > interval:
            msg_log.popleft()

        if len(msg_log) >= settings["spam_message_threshold"]:
            await self.take_spam_action(author, msg_log, message)
            return
        
        content = message.content
        same_count = sum(1 for msg, _ in msg_log if msg.content == content)
        if same_count >= settings["spam_duplicate_threshold"]:
            await self.take_spam_action(author, msg_log, message)
            return
        
        if len(message.mentions) >= settings["spam_mention_threshold"]:
            await self.take_spam_action(author, msg_log, message)
            return
        
        link_count = len(re.findall(r'https?://', message.content))
        if link_count >= settings['spam_link_threshold']:
            await self.take_spam_action(author, msg_log, message)
            return
    
    async def _check_suspicious_links(self, message: discord.Message, settings: dict):
        suspicous_links = ["discord.gift", "free-nitro", "steam-giveaway", "airdrop", "login.discord", "discord-app"]

        if any(link in message.content.lower() for link in suspicous_links):
            try:
                await message.delete()
                await message.author.timeout(datetime.now(timezone).utc + timedelta(minutes=30), reason="Suspicous links detected")

                embed = discord.Embed(title="Suspicous Link Detected", description="Potental scam link removed", color=discord.Color.orange())
                embed.add_field(name="User: ", value=f"{message.author.mention}", inline=False)
                embed.add_field(name="Action: ", value="Messge deleted, user timed out for 30 minutes", inline=False)
                
                await self._send_security_log(message.guild.id, embed)
            except Exception as e:
                logger.error(f"Error handling suspcious link: {e}")
    
    async def _check_selfbot(self, message: discord.Message, settings: dict):
        if not message.content:
            return
        
        rapid_fire_patterns = ["@everyone", "http", ":", '.com', "discord.gg"]
        caps_ratio = sum(1 for c in message.content if c.isupper()) / max(len(message.content), 1)

        if(any(p in message.content.lower() for p in rapid_fire_patterns) and caps_ratio > 0.5) or len(message.embeds) > 0:
            try:
                await message.delete()
                await message.author.timeout(datetime.now(timezone.utc) + timedelta(minutes=30), reason="Possible self-bot activitiy")
                embed = discord.Embed(title="Self-Bot Detection", description="Automated bot-like behavior detected", color=discord.Color.red())
                embed.add_field(name="User: ", value=f"{message.author.mention}", inline=False)
                embed.add_field(name="Action: ", value="Message deleted, user timed out for 30 minutes", inline=False)

                await self._send_security_log(message.guild.id, embed)
            except Exception as e:
                logger.error(f"Error dectecting self-bot: {e}")
    
    async def take_spam_action(self, member : discord.Member, msg_log, trigger_message):
        try:
            until = datetime.utcnow() + timedelta(minutes=10)
            await member.timeout(until, reason="Spam detected by Anti=Spam")

            for msg, _ in list(msg_log):
                try:
                    await msg.delete()
                except:
                    pass
            
            view = UndoPunishment(member)
            embed = discord.Embed(title="Anti-Spam Alert", description="Spam behaviour detected and punished", color=discord.Color.red())
            embed.add_field(name="Member: ", value=f"{member.mention} (`{member.id}`)", inline=False)
            embed.add_field(name="Alert: ", value="Spamming messages", inline=False)
            embed.add_field(name="Action: ", value="Timed out for 10 minutes, messages deleted", inline=False)
            embed.set_footer(text=f"User: {member.name}")

            await self._send_security_log(member.guild.id, embed, view)
        except Exception as e:
            logger.error(f"Anti-Spam: Error punishing {member.name}: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member : discord.Member):
        guild_id = member.guild.id
        settings = self._get_security_settings(guild_id)
        now = datetime.utcnow()

        if guild_id not in self.joins:
            self.joins[guild_id] = deque()
        
        self.joins[guild_id].append(now)

        if settings["anti_raid_enabled"] and self.raid_mode.get(guild_id, False) and not member.bot:
            try:
                await member.kick(reason="Anti-Raid mode active")

                embed = discord.Embed(title="Raid Protection", description="Member kicked due to active raid mode", color=discord.Color.orange())
                embed.add_field(name="User: ", value=f"{member.mention} (`{member.id}`)", inline=False)

                await self._send_security_log(guild_id, embed)
            except discord.Forbidden:
                logger.warning(f"Missing permissions to kick {member}")
            except Exception as e:
                logger.error(f"Error kicking {member}: {e}")
        
        if settings["anti_alt_enabled"] and not member.bot:
            account_age = (datetime.now(timezone.utc) - member.created_at).days
            if account_age < settings["min_account_age_days"]:
                try:
                    await member.timeout(until=datetime.now(timezone.utc) + timedelta(minutes=10), reason=f"Alt account detected (account age: {account_age} days)")
                    embed = discord.Embed(title="Alt Account Detection", description="Suspiciously new account detected", color=discord.Color.yellow())
                    embed.add_field(name="User: ", value=f"{member.mention} (`{member.id}`)", inline=False)
                    embed.add_field(name="Account Age: ", value=f"{account_age} days", inline=False)
                    embed.add_field(name="Action: ", value="Timed out for 10 minutes for review", inline=False)

                    await self._send_security_log(guild_id, embed)
                except Exception as e:
                    logger.error(f"Failed to timeout alt: {e}")
        
        if member.bot:
            guild = member.guild
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.bot_add):
                if entry.target.id == member.id:
                    inviter = entry.user

                    if self._is_whitelisted(inviter.id, guild.id):
                        return
                    
                    original_roles = [role for role in inviter.roles if role.name != "@everyone"]

                    try:
                        await inviter.edit(roles=[], reason="Invited a discord bot without authorization")
                        await member.kick(reason="Unauthorized bot")

                        view = GiveRolesBackView(original_roles=original_roles, user=inviter)
                        embed = discord.Embed(title="Unauthorized Bot Addition", description="Bot added without proper authorization", color=discord.Color.red())
                        embed.add_field(name="Inviter: ", value=f"{inviter.mention} (`{inviter.id}`)", inline=False)
                        embed.add_field(name="Bot: ", value=f"{member.mention} (`{member.id}`)", inline=False)
                        embed.add_field(name="Action: ", value="Inviter roles stripped, bot kicked", inline=False)
                        embed.set_footer(text=f"Inviter: {inviter.name}")
                        
                        await self._send_security_log(guild_id, embed, view)
                    except discord.Forbidden:
                        logger.warning(f"Missing permissions to handle unauthorized bot")
                    break
    
    @commands.Cog.listener()
    async def on_webhooks_update(self, channel: discord.TextChannel):
        guild = channel.guild

        try:
            async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create):
                if(datetime.now(timezone.utc) - entry.created_at).seconds < 10:
                    if not entry.user.guild_permissions.administrator:
                        if not self._is_whitelisted(entry.user.id, guild.id):
                            await entry.user.kick(reason="Unauthorized webhook creation")

                            embed = discord.Embed(title="Unauthorized WWebhook", description="Webhook created without authorization", color=discord.Color.red())
                            embed.add_field(name="User: ", value=f"{entry.user.mention}", inline=False)
                            embed.add_field(name="Channel: ", value=channel.mention, inline=False)
                            embed.add_field(name="Action:", value="User Kicked", inline=False)

                            await self._send_security_log(guild.id, embed)
        except Exception as e:
            logger.error(f"Webhook protection error: {e}")
    
    async def trigger_panic_mode(self, guild: discord.Guild, reason: str):
        try:
            self.original_permissions[guild.id] = {}
            for channel in guild.text_channels:
                overwrite = channel.overwrites_for(guild.default_role)
                self.original_permissions[guild.id][channel.id] = overwrite.send_messages
                overwrite.send_messages = False
                await channel.set_permissions(guild.default_role, overwrite=overwrite)
            
            embed = discord.Embed(title="Panic Mode Activated", description=f"Server locked down: {reason}", color=discord.Color.dark_red())
            await self._send_security_log(guild.id, embed)
        except Exception as e:
            logger.error(f"Error triggering panic mode: {e}")

    async def unpanic_mode(self, guild: discord.Guild):
        try:
            if guild.id not in self.original_permissions:
                return

            for channel in guild.text_channels:
                if channel.id in self.original_permissions[guild.id]:
                    original_perm = self.original_permissions[guild.id][channel.id]
                    overwrite = channel.overwrites_for(guild.default_role)
                    overwrite.send_messages = original_perm
                    await channel.set_permissions(guild.default_role, overwrite=overwrite)

            del self.original_permissions[guild.id]

            embed = discord.Embed(title="Panic Mode Deactivated", description="Server unlocked", color=discord.Color.green())
            await self._send_security_log(guild.id, embed)
        except Exception as e:
            logger.error(f"Error reversing panic mode: {e}")
    
    security = SlashCommandGroup("security", "Security system commands")

    @security.command(name="whitelist", description="Add a user to the security whitelist")
    @commands.has_guild_permissions(administrator=True)
    async def whitelist(self, ctx : discord.ApplicationContext, member: Option(discord.Member, description="Member to whitelist", required=True)): # type: ignore
        await ctx.defer()

        if self._is_whitelisted(member.id, ctx.guild.id):
            return await ctx.respond(f"{member.mention} is already whitelisted", ephemeral=True)

        success = self._add_to_whitelist(member.id, ctx.guild.id, ctx.author.id)

        if success:
            embed = discord.Embed(title="User Whitelisted", description=f"{member.mention} has been added to the security whitelist", color=discord.Color.green())
            embed.add_field(name="Exempt From: ", value="Anti_Nuke\n Bot Addition Checks\n Webhook Restrictions", inline=False)
            await ctx.respond(embed=embed)
        else:
            await ctx.respond("Failed to whitelist user", ephemeral=True)
    
    @security.command(name="unwhitelist", description="Remove a user from the security whitelist")
    @commands.has_guild_permissions(administrator=True)
    async def unwhitelist(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="Member to remove from whitelist", required=True)): # type: ignore
        await ctx.defer()

        if not self._is_whitelisted(member.id, ctx.guild.id):
            return await ctx.respond(f"{member.mention} is not whitelisted", ephemeral=True)

        success = self._remove_from_whitelist(member.id, ctx.guild.id)

        if success:
            await ctx.respond(f"{member.mention} has been removed from the security whitelist.")
        else:
            await ctx.respond("Failed to remove from whitelist.", ephemeral=True)

    @security.command(name="viewwhitelist", description="View all whitelisted users")
    @commands.has_guild_permissions(administrator=True)
    async def viewwhitelist(self, ctx: discord.ApplicationContext):
        await ctx.defer()

        query = "SELECT user_id, added_by, created_at FROM security_whitelist WHERE guild_id = %s"
        results = self.db.fetch_all_dict(query, (ctx.guild.id,))

        if not results:
            await ctx.respond("No whitelisted users found.")
            return
        
        embed = discord.Embed(title="Security Whitelist", description=f"Whitelisted users for {ctx.guild.name}", color=discord.Color.blue())
        for idx, entry in enumerate(results[:10], 1):
            user = ctx.guild.get_member(entry["user_id"])
            user_text = user.mention if user else f"Unknown ({entry['user_id']})"

            added_by = ctx.guild.get_member(entry["added_by"]) if entry["added_by"] else None
            added_text = added_by.mention if added_by else "Unknown"

            embed.add_field(name=f"{idx}. {user_text}", value=f"Added By: {added_text}", inline=False)
        
        if len(results) > 10:
            embed.set_footer(text=f"Showing 10 of {len(results)} whitelisted users")
        
        await ctx.respond(embed=embed)
    
    @security.command(name="panicmode", description="Lock down all channels")
    @commands.has_guild_permissions(administrator=True)
    async def panicmode(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        await self.trigger_panic_mode(ctx.guild, reason=f"Manual panic mode activated by {ctx.author.display_name}")
        await ctx.respond("**Panic mode activated!** All channels have been locked.")

    @security.command(name="unpanic", description="Unlock all channels")
    @commands.has_guild_permissions(administrator=True)
    async def unpanic_command(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        await self.unpanic_mode(ctx.guild)
        await ctx.respond("**Panic mode deactivated!** Channels have been unlocked.")
    
    @security.command(name="backupserver", description="Backup server configuration")
    @commands.has_guild_permissions(administrator=True)
    async def backup_server(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        guild = ctx.guild

        backup_data = {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "timestamp": datetime.utcnow().isoformat(),
            "channels": [],
            "roles": [],
            "categories": []
        }

        for category in guild.categories:
            backup_data["categories"].append({
                "name": category.name,
                "position": category.position
            })

        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                continue
            overwrite_data = {}
            for target, overwrite in channel.overwrites.items():
                if isinstance(target, discord.Role):
                    overwrite_data[str(target.id)] = {
                        "send_messages": overwrite.send_messages,
                        "view_channel": overwrite.view_channel
                    }
            backup_data["channels"].append({
                "name": channel.name,
                "type": str(channel.type),
                "position": channel.position,
                "category": channel.category.name if channel.category else None,
                "overwrites": overwrite_data
            })

        
        for role in guild.roles:
            if role.is_default():
                continue
            backup_data["roles"].append({
                "name": role.name,
                "permissions": role.permissions.value,
                "colour": role.colour.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "position": role.position
            })

        success = self._save_backup(guild.id, backup_data)

        if success:
            embed = discord.Embed(title="Server Backed Up", description="Server configuration has been saved", color=discord.Color.green())
            embed.add_field(name="Roles: ", value=str(len(backup_data["roles"])), inline=True)
            embed.add_field(name="Channels: ", value=str(len(backup_data["channels"])), inline=True)
            embed.add_field(name="Cateogies: ", value=str(len(backup_data["categories"])), inline=True)
            embed.set_footer(text=f"Backup created at {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC")
            await ctx.respond(embed=embed)
        else:
            await ctx.respond("Failed to backup server.", ephemeral=True)
    
    @security.command(name="restoreserver", description="Restore server from backup")
    @commands.has_guild_permissions(administrator=True)
    async def restoreserver(self, ctx: discord.ApplicationContext):
        await ctx.defer()
        guild = ctx.guild

        backup_data = self._get_backup(guild.id)
        if not backup_data:
            await ctx.respond("No backup found for this server. Use `/security backupserver` first.")
            return
        
        await ctx.respond("**Restoring server...** This may take several minutes.")

        category_mapping = {}
        restored_roles = 0
        restored_categories = 0
        restored_channels = 0

        for role_data in sorted(backup_data["roles"], key=lambda r: r["position"], reverse=True):
            try:
                await guild.create_role(name=role_data["name"], permissions=discord.Permissions(role_data["permissions"]), colour=discord.Colour(role_data["colour"]), hoist=role_data["hoist"], mentionable=role_data["mentionable"])
                restored_roles += 1
            except Exception as e:
                logger.error(f"Error restoring role {role_data['name']}: {e}")
        
        for cat_data in sorted(backup_data["categories"], key=lambda c: c["position"]):
            try:
                new_category = await guild.create_category(name=cat_data["name"], position=cat_data["position"])
                category_mapping[cat_data["name"]] = new_category
                restored_categories += 1
            except Exception as e:
                logger.error(f"Error restoring category {cat_data['name']}: {e}")
        
        for ch in backup_data["channels"]:
            try:
                overwrites = {}
                for role_id, perms in ch["overwrites"].items():
                    role = guild.get_role(int(role_id))
                    if role:
                        overwrites[role] = discord.PermissionOverwrite(send_messages=perms["send_messages"], view_channel=perms["view_channel"])
                category = category_mapping.get(ch["category"])

                if ch["type"] == "text":
                    await guild.create_text_channel(name=ch["name"], overwrites=overwrites, position=ch["position"], category=category)
                    restored_channels += 1
                elif ch["type"] == "voice":
                    await guild.create_voice_channel(name=ch["name"], overwrites=overwrites, position=ch['position'], category=category)
                    restored_channels += 1
            except Exception as e:
                logger.error(f"Error restoring channel {ch['name']}: {e}")

        embed = discord.Embed(title="Server Restored", description="Server configuration has been restored from backup", color=discord.Color.green())
        embed.add_field(name="Roles Restored: ", value=str(restored_roles), inline=True)
        embed.add_field(name="Channels Restored: ", value=str(restored_channels), inline=True)
        embed.add_field(name="Categories Restored: ", value=str(restored_categories), inline=True)

        await ctx.respond(embed=embed)

def setup(bot):
    bot.add_cog(Security(bot))