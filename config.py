import os
from dotenv import load_dotenv


load_dotenv()

# MySQL Configuration
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "database": os.getenv("MYSQL_DATABASE", "synergy_pro"),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": int(os.getenv("MYSQL_PORT", 3306)),
    "pool_size": int(os.getenv("MYSQL_POOL_SIZE", 5))
}

TOKEN = os.getenv("DISCORD_TOKEN")