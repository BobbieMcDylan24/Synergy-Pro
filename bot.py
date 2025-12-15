import discord
from discord.ext import commands
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG, TOKEN
import os 

bot = discord.Bot(intents=discord.Intents.all())

db = MySQLHelper(**MYSQL_CONFIG)

@bot.event
async def on_ready():
    create_guilds_table = """
        CREATE TABLE IF NOT EXISTS guilds (
            id INT AUTO_INCREMENT PRIMARY KEY,
            guild_id BIGINT UNIQUE NOT NULL,
            mod_log_channel_id BIGINT DEFAULT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """
    db.create_table(create_guilds_table)
    print(f"{bot.user} has connected to Discord!")

@bot.event
async def on_guild_join(guild: discord.Guild):
    db.insert("guilds", {"guild_id": guild.id})


@bot.event
async def on_close():
    db.close_pool()
    print("Database connections closed.")

if __name__ == "__main__":
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py'):
            bot.load_extension(f"cogs.{filename[:-3]}")
            print(f"Loaded Extension: {filename[:-3]}")
        else:
            continue
    bot.run(TOKEN)