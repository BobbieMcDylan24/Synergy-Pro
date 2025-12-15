import discord
from discord.ext import commands
from utils.mysql_helper import MySQLHelper
from config import MYSQL_CONFIG, TOKEN
import os 

bot = discord.Bot(intents=discord.Intents.all())

db = MySQLHelper(**MYSQL_CONFIG)

@bot.event
async def on_ready():
    print(f"{bot.user} has connected to Discord!")


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