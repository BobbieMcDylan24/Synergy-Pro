import discord
from discord.ext import commands, tasks
from discord.commands import SlashCommandGroup, Option
from datetime import datetime, timedelta
import logging
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG

logger = logging.getLogger(__name__)


class RoleManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = MySQLHelper(**MYSQL_CONFIG)
        self._ensure_tables()
        self.check_temp_Roles.start()

    def _ensure_tables(self):
        create_temp_roles_table = """
            CREATE TABLE IF NOT EXISTS temp_roles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                role_id BIGINT NOT NULL,
                added_by BIGINT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                reason TEXT DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id),
                INDEX idx_user_id (user_id),
                INDEX idx_expires_at (expires_at),
                INDEX idx_guild_user (guild_id, user_id)
            )
        """
        self. db.create_table(create_temp_roles_table)
        
        create_assignments_table = """
            CREATE TABLE IF NOT EXISTS role_assignments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                role_id BIGINT NOT NULL,
                moderator_id BIGINT NOT NULL,
                action_type ENUM('ADD', 'REMOVE') NOT NULL,
                reason TEXT DEFAULT NULL,
                is_temporary BOOLEAN DEFAULT FALSE,
                duration VARCHAR(50) DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_guild_id (guild_id),
                INDEX idx_user_id (user_id),
                INDEX idx_role_id (role_id)
            )
        """
        self.db.create_table(create_assignments_table)
        
        logger.info("Role management tables ensured")
    
    def _parse_duration(self, duration: int, unit: str) -> timedelta:
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

    def _add_temp_role(self, guild_id: int, user_id: int, role_id: int, added_by: int, expires_at: datetime, reason: str = None) -> bool:
        data = {
            "guild_id": guild_id,
            "user_id": user_id,
            "role_id": role_id,
            "added_by": added_by,
            "expires_at": expires_at,
            "reason": reason
        }
        return self.db.insert("temp_roles", data) is not None
    
    def _remove_temp_role(self, guild_id: int, user_id: int, role_id: int) -> bool:
        return self.db.delete("temp_roles", "guild_id = %s AND user_id = %s AND role_id = %s", (guild_id, user_id, role_id))

    def _log_role_assignment(self, guild_id: int, user_id: int, role_id: int, moderator_id: int, action_type: str, reason: str = None, is_temporary: bool = False, duration: str = None) -> bool:
        data = {
            "guild_id": guild_id,
            "user_id": user_id,
            "role_id": role_id,
            "moderator_id": moderator_id,
            "action_type": action_type,
            "reason": reason,
            "is_temporary": is_temporary,
            "duration": duration
        }
        return self.db.insert("role_assignments", data) is not None
    
    def _get_user_temp_roles(self, guild_id: int, user_id: int) -> list:
        query = """
            SELECT role_id, expires_at, reason, added_by
            FROM temp_roles
            WHERE guild_id = %s AND user_id = %s
            ORDER BY expires_at ASC
        """
        return self.db.fetch_all_dict(query, (guild_id, user_id))

    def _get_expired_temp_roles(self) -> list:
        query = """
            SELECT id, guild_id, user_id, role_id
            FROM temp_roles
            WHERE expires_at <= NOW()
        """
        return self.db.fetch_all_dict(query)
    
    @tasks.loop(seconds=30)
    async def check_temp_roles(self):
        expired_roles = self._get_expired_temp_roles()

        for entry in expired_roles:
            guild = self.bot.get_guild(entry["guild_id"])
            if not guild:
                self.db.delete("temp_rolees", "id = %s", (entry["id"]))
                continue

            member = guild.get_member(entry["user_id"])
            if not member:
                self.db.delete("temp_roles", "id = %s", (entry["id"]))
                continue

            role = guild.get_role(entry["role_id"])
            if not role:
                self.db.delete("temp_roles", "id = %s", (entry["id"]))
                continue

            try:
                await member.remove_roles(role, reason="Temporary role expired")
                self.db.delete("temp_roles", "id = %s", (entry["id"]))
                logger.info(f"Removed expired temp role {role.name} from {member} in {guild.name}")
            except discord.Forbidden:
                logger.warning(f"Missing permissions to remove role {role.name} from {member} in {guild.name}")
            except Exception as e:
                logger.error(f"Error removing expired temp role: {e}")
    
    @check_temp_roles.before_loop
    async def before_check_temp_roles(self):
        await self.bot.wait_until_ready()
    
    role = SlashCommandGroup("role", "Role management commands")

    @role.command(name="add", description="Add a role to a member")
    @commands.has_guild_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def add_role(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to give the role to", required=True), role: Option(discord.Role, description="The role to give", required=True), reason: Option(str, description="Reason for adding the role", required=False, default="No reason provided")): #type: ignore
        await ctx.defer()
        if role in member.roles:
            await ctx.respond(f"{member.mention} already has the {role.mention} role!", ephemeral=True)
            return
        
        if role >= ctx.guild.me.top_role:
            await ctx.respond(f"I cannot assign {role.mention} because it's higher than or equal to my highest role!", ephemeral=True)
            return
        
        if ctx.author.id != ctx.guild.owner_id:
            if role >= ctx.author.top_role:
                await ctx.respond(f"You cannot assign {role.mention} because it's higher than or equal to your highest role!", ephemeral=True)
                return
        
        if role.is_default():
            await ctx.respond("You cannot assign the `@everyone` role!", ephemeral=True)
            return
        
        try:
            await member.add_roles(role, reason=f"{reason} | Added by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond("I don't have permission to assign this role!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to add role: {e}", ephemeral=True)
            logger.error(f"Failed to add role {role.id} to {member.id}: {e}")
            return
        
        self._log_role_assignment(guild_id=ctx.guild.id, user_id=member.id, role_id=role.id, moderator_id=ctx.author.id, action_type="ADD", reason=reason, is_temporary=False)

        embed = discord.Embed(title="Role Added", color=discord.Color.green(), timestamp=datetime.utcnow())
        embed.add_field(name="Member", value=f"{member.mention}", inline=True)
        embed.add_field(name="Role", value=f"{role.mention}", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)

        await ctx.respond(embed=embed)
        logger.info(f"Role {role.name} added to {member} by {ctx.author.name} in {ctx.guild.name}")
    
    @role.command(name="remove", description="Remove a role from a member")
    @commands.has_guild_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def remove_role(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to remove the role from", required=True), role: Option(discord.Role, description="The role to remove", required=True), reason: Option(str, description="Reason for removing the role", required=False, default="No reason provided")): #type: ignore
        await ctx.defer()

        if role not in member.roles:
            await ctx.respond(f"{member.mention} doesn't have the {role.mention} role!", ephemeral=True)
            return
        
        if role >= ctx.guild.me.top_role:
            await ctx.respond(f"I cannot remove {role.mention} because it's higher than or equal to my highest role!", ephemeral=True)
            return
        
        if ctx.author.id != ctx.guild.owner_id:
            if role >= ctx.author.top_role:
                await ctx.respond(f"You cannot remove {role.mention} because it's higher than or equal to your highest role!", ephemeral=True)
                return
        
        if role.is_default():
            await ctx.respond("You cannot remove the `@everyone` role!", ephemeral=True)
            return
        
        try:
            await member.remove_roles(role, reason=f"{reason} | Removed by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond("I don't have the permission to remove this role!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to remove role: {e}", ephemeral=True)
            logger.error(f"Failed to remove role {role.id} from {member.id}: {e}")
            return
        
        self._remove_temp_role(ctx.guild.id, member.id, role.id)

        self._log_role_assignment(guild_id=ctx.guild.id, user_id=member.id, role_id=role.id, moderator_id=ctx.author.id, action_type="REMOVE", reason=reason, is_temporary=False)

        embed = discord.Embed(title="Role Removed", color=discord.Color.green(), timestamp=datetime.utcnow())
        embed.add_field(name="Member", value=f"{member.mention}", inline=True)
        embed.add_field(name="Role", value=f"{role.mention}", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)

        await ctx.respond(embed=embed)
        logger.info(f"Role {role.name} removed from {member} by {ctx.author} in {ctx.guild.name}")
    
    @role.command(name="temp", description="Give a role to a member temporarily")
    @commands.has_guild_permissions(manage_roles=True)
    @commands.bot_has_guild_permissions(manage_roles=True)
    async def temp_role(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to give the role to", required=True), role: Option(discord.Role, description="The role to give", required=True), duration: Option(int, description="Duration value", required=True, min_value=1, max_value=365), unit: Option(str, description="Time unit", required=True, choices=["minutes", "hours", "days", "weeks"]), reason: Option(str, description="Reason for adding the temproary role", required=False, default="No reason provided")): #type: ignore
        await ctx.defer()

        if role in member.roles:
            await ctx.respond(f"{member.mention} already has the {role.mention} role!", ephemeral=True)
            return
        
        if role >= ctx.guild.me.top_role:
            await ctx.respond(f"I cannot assign {role.mention} because it's higher than or equal to my highest role!", ephemeral=True)
            return
        
        if ctx.author.id != ctx.guild.owner_id:
            if role >= ctx.author.top_role:
                await ctx.respond(f"You cannot assign {role.mention} because it's higher than or equal to your highest role!", ephemeral=True)
                return
            
        if role.is_default():
            await ctx.respond("You cannot assign the `@everyone` role!", ephemeral=True)
            return
        
        duration_delta = self._parse_duration(duration, unit)
        if not duration_delta:
            await ctx.respond("Invalid duration unit!", ephemeral=True)
            return
        
        expires_at = datetime.utcnow() + duration_delta
        duration_str = self._format_duration(duration, unit)

        try:
            await member.add_roles(role, reason=f"Temproary role for {duration_str}: {reason} | Added by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond("I don't have permission to assign this role!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to add role: {e}", ephemeral=True)
            logger.error(f"Failed to add temp role {role.id} to {member.id}: {e}")
            return
        
        success = self._add_temp_role(guild_id=ctx.guild.id, user_id=member.id, role_id=role.id, added_by=ctx.author.id, expires_at=expires_at, reason=reason)

        if not success:
            logger.error(f"Failed to log temp role to database for {member.id}")
        
        self._log_role_assignment(guild_id=ctx.guild.id, user_id=member.id, role_id=role.id, moderator_id=ctx.author.id, action_type="ADD", reason=reason, is_temporary=True, duration=duration_str)

        embed = discord.Embed(title="Temproary Role Added", color = discord.Color.blue(), timestamp=datetime.utcnow())
        embed.add_field(name="Member", value=f"{member.mention}", inline=True)
        embed.add_field(name="Role", value=f"{role.mention}", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx.author.mention}", inline=True)
        embed.add_field(name="Duration", value=duration_str, inline=True)
        embed.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:F> (<t:{int(expires_at.timestamp())}:R>)", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)

        await ctx.respond(embed=embed)
        logger.info(f"Temproary role {role.name} added to {member.name} for {duration_str} by {ctx.author.name} in {ctx.guild.name}")
        
    @role.command(name="list", description="List all roles a member has")
    @commands.has_guild_permissions(manage_roles=True)
    async def list_roles(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to list roles for", required=False)): #type: ignore
        await ctx.defer()

        member = member or ctx.author

        roles = [role for role in member.roles if not role.is_default()]

        if not roles:
            await ctx.respond(f"{member.mention} has no roles.")
            return
        
        temp_roles = self._get_user_temp_roles(ctx.guild.id, member.id)
        temp_role_ids = [tr["role_id"] for tr in temp_roles]

        embed = discord.Embed(title=f"Roles for {member.display_name}", color=member.color, timestamp=datetime.utcnow())
        
        roles.sort(key=lambda r: r.position, reverse=True)

        role_list = []
        for role in roles:
            if role.id in temp_role_ids:
                temp_data = next((tr for tr in temp_roles if tr["role_id"] == role.id), None)
                if temp_data:
                    expires_str = f" (expires <t:{int(temp_data['expires_at'].timestamp())}:R>)"
                    role_list.append(f"{role.mention} {expires_str}")
                else:
                    role_list.append(f"{role.mention}")
            else:
                role_list.append(role.mention)
        
        role_text = ", ".join(role_list)
        if len(role_text) > 1024:
            role_text = role_text[:1021] + "..."
        
        embed.add_field(name=f"Roles ({len(roles)})", value=role_text, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)

        await ctx.respond(embed=embed)

    @role.command(name="templist", description="List all temporary roles for a member")
    @commands.has_permissions(manage_roles=True)
    async def temp_list(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to list temporary roles for", required=False)): # type: ignore
        await ctx.defer()
        
        member = member or ctx.author
        
        temp_roles = self._get_user_temp_roles(ctx.guild.id, member.id)
        
        if not temp_roles: 
            await ctx.respond(f"{member.mention} has no temporary roles.")
            return
        
        embed = discord.Embed(
            title=f"Temporary Roles for {member.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        for idx, temp_data in enumerate(temp_roles, 1):
            role = ctx.guild.get_role(temp_data["role_id"])
            if not role:
                continue
            
            added_by = ctx.guild.get_member(temp_data["added_by"])
            added_by_text = added_by.mention if added_by else "Unknown"
            
            expires_timestamp = int(temp_data["expires_at"].timestamp())
            reason = temp_data["reason"] or "No reason provided"
            
            embed.add_field(
                name=f"{idx}. {role.name}",
                value=(
                    f"**Expires:** <t:{expires_timestamp}:F> (<t:{expires_timestamp}: R>)\n"
                    f"**Added by:** {added_by_text}\n"
                    f"**Reason:** {reason}"
                ),
                inline=False
            )
        
        embed.set_thumbnail(url=member.display_avatar.url)
        await ctx.respond(embed=embed)

    @role.command(name="history", description="View role assignment history for a member")
    @commands.has_permissions(manage_roles=True)
    async def role_history(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to view history for", required=True), limit: Option(int, description="Number of entries to show", required=False, default=10, min_value=1, max_value=25)): # type: ignore
        await ctx.defer()
        
        query = """
            SELECT role_id, moderator_id, action_type, reason, is_temporary, duration, created_at
            FROM role_assignments
            WHERE guild_id = %s AND user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """
        
        history = self.db.fetch_all_dict(query, (ctx.guild. id, member.id, limit))
        
        if not history: 
            await ctx.respond(f"No role history found for {member.mention}.")
            return
        
        embed = discord.Embed(
            title=f"Role History for {member.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        for idx, entry in enumerate(history, 1):
            role = ctx.guild. get_role(entry["role_id"])
            role_text = role.mention if role else f"Deleted Role ({entry['role_id']})"
            
            moderator = ctx.guild.get_member(entry["moderator_id"])
            moderator_text = moderator.mention if moderator else "Unknown"
            
            action_emoji = "➕" if entry["action_type"] == "ADD" else "➖"
            temp_text = f"({entry['duration']})" if entry["is_temporary"] else ""
            
            timestamp = int(entry["created_at"].timestamp())
            reason = entry["reason"] or "No reason provided"
            
            embed.add_field(
                name=f"{action_emoji} {role_text}{temp_text}",
                value=(
                    f"**Moderator:** {moderator_text}\n"
                    f"**Date:** <t:{timestamp}:F>\n"
                    f"**Reason:** {reason}"
                ),
                inline=False
            )
        
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Showing {len(history)} of all entries")
        
        await ctx. respond(embed=embed)

    @role.command(name="removeall", description="Remove all roles from a member")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def remove_all_roles(self, ctx: discord.ApplicationContext, member: Option(discord.Member, description="The member to remove all roles from", required=True), reason: Option(str, description="Reason for removing all roles", required=False, default="No reason provided")): # type: ignore
        await ctx.defer()
        
        roles_to_remove = [role for role in member.roles if not role. is_default()]
        
        if not roles_to_remove: 
            await ctx.respond(f"{member.mention} has no roles to remove!", ephemeral=True)
            return
        
        can_remove = []
        cannot_remove = []
        
        for role in roles_to_remove:
            if role >= ctx.guild.me.top_role:
                cannot_remove. append(role)
            elif ctx.author.id != ctx.guild.owner_id and role >= ctx.author.top_role:
                cannot_remove. append(role)
            else:
                can_remove. append(role)
        
        if not can_remove:
            await ctx.respond("No roles can be removed due to role hierarchy!", ephemeral=True)
            return
        
        try:
            await member.remove_roles(*can_remove, reason=f"{reason} | Removed by {ctx.author}")
        except discord.Forbidden:
            await ctx.respond(f"I don't have permission to remove roles!", ephemeral=True)
            return
        except discord.HTTPException as e:
            await ctx.respond(f"Failed to remove roles:  {e}", ephemeral=True)
            return
        
        for role in can_remove:
            self._remove_temp_role(ctx. guild.id, member.id, role.id)
        
        for role in can_remove:
            self._log_role_assignment(
                guild_id=ctx.guild.id,
                user_id=member.id,
                role_id=role.id,
                moderator_id=ctx.author.id,
                action_type="REMOVE",
                reason=reason
            )
        
        embed = discord. Embed(
            title="Roles Removed",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(name="Member", value=f"{member.mention}", inline=True)
        embed.add_field(name="Moderator", value=f"{ctx. author.mention}", inline=True)
        embed.add_field(name="Roles Removed", value=str(len(can_remove)), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        
        if cannot_remove:
            cannot_remove_text = ", ".join([r.mention for r in cannot_remove[: 5]])
            if len(cannot_remove) > 5:
                cannot_remove_text += f" and {len(cannot_remove) - 5} more..."
            embed.add_field(name="Could Not Remove", value=cannot_remove_text, inline=False)
        
        await ctx. respond(embed=embed)
        logger.info(f"Removed {len(can_remove)} roles from {member} by {ctx. author} in {ctx.guild. name}")

def setup(bot):
    bot.add_cog(RoleManagement(bot))