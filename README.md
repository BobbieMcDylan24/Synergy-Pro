# Synergy Pro Discord Bot

A powerful multi-purpose Discord bot built with **Py-cord** and **MySQL**.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Status](https://img.shields.io/badge/status-under%20development-orange.svg)
![Python](https://img.shields.io/badge/python-3.8%2B-blue.svg)

## ⚠️ Development Status

**This project is currently under heavy development.** Features may change, and there may be bugs or incomplete functionality. Use in production at your own risk.

## 📋 Features

- **Moderation Commands**
  - `/mod ban` - Ban members with optional message deletion
  - `/mod kick` - Kick members from the server
  - `/mod timeout` - Timeout members with flexible duration
  - `/mod untimeout` - Remove timeouts early
  
- **Logging System**
  - Configurable mod log channel
  - Detailed punishment logs with unique IDs
  - Database storage of all moderation actions
  - Beautiful embed notifications

- **Advanced Features**
  - MySQL database integration with connection pooling
  - Unique punishment ID tracking
  - DM notifications to punished users
  - Role hierarchy checking
  - Permission validation
  - Comprehensive error handling

## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
- MySQL or MariaDB server
- Discord Bot Token ([Create one here](https://discord.com/developers/applications))

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/bobbiemcdylan24/synergy-pro.git
   cd synergy-pro
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up MySQL Database**
   
   Create a new database for the bot:
   ```sql
   CREATE DATABASE synergy_pro
   ```

4. **Configure Environment Variables**
   
   Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` with your credentials:
   ```env
   # Discord Bot Token
   DISCORD_TOKEN=your_discord_bot_token_here

   # MySQL Configuration
   MYSQL_HOST=localhost
   MYSQL_DATABASE=synergy_pro
   MYSQL_USER=root
   MYSQL_PASSWORD=your_password_here
   MYSQL_PORT=3306
   MYSQL_POOL_SIZE=5
   ```

5. **Run the bot**
   ```bash
   python bot.py
   ```

## 📁 Project Structure

```
synergy-pro/
├── bot.py                 # Main bot file
├── config.py              # Configuration loader
├── requirements.txt       # Python dependencies
├── . env.example          # Example environment variables
├── . env                  # Your environment variables (create this)
├── utils/
│   └── mysql_helper.py   # MySQL database helper
└── cogs/
    └── moderation.py     # Moderation commands cog
```

## 🗄️ Database Schema

The bot automatically creates the following tables:

### `guilds` Table
Stores server-specific configuration. 

| Column             | Type      | Description                    |
|--------------------|-----------|--------------------------------|
| id                 | INT       | Primary key                    |
| guild_id           | BIGINT    | Discord server ID              |
| mod_log_channel_id | BIGINT    | Mod log channel ID (nullable)  |
| created_at         | TIMESTAMP | Record creation time           |
| updated_at         | TIMESTAMP | Last update time               |

### `punishment_actions` Table
Stores all moderation actions.

| Column         | Type         | Description                        |
|----------------|--------------|------------------------------------|
| id             | INT          | Primary key                        |
| punishment_id  | VARCHAR(36)  | Unique punishment identifier       |
| guild_id       | BIGINT       | Discord server ID                  |
| user_id        | BIGINT       | Punished user ID                   |
| moderator_id   | BIGINT       | Moderator user ID                  |
| action_type    | VARCHAR(50)  | Type (BAN, KICK, TIMEOUT)         |
| reason         | TEXT         | Reason for punishment              |
| duration       | VARCHAR(50)  | Duration (for timeouts, nullable)  |
| created_at     | TIMESTAMP    | Action timestamp                   |

## 🎮 Commands

### Moderation Commands

| Command                                          | Permission Required    | Description                          |
|--------------------------------------------------|------------------------|--------------------------------------|
| `/mod ban <member> [reason] [delete_messages]`  | Ban Members            | Ban a member from the server         |
| `/mod kick <member> [reason]`                    | Kick Members           | Kick a member from the server        |
| `/mod timeout <member> <duration> <unit> [reason]` | Moderate Members     | Timeout a member                     |
| `/mod untimeout <member> [reason]`               | Moderate Members       | Remove a timeout from a member       |


## ⚙️ Configuration

### Setting Up Mod Logs

Coming soon in web dashboard.

### Timeout Durations

Timeouts support the following units:
- **Minutes** (max:  40320 minutes / 28 days)
- **Hours** (max: 672 hours / 28 days)
- **Days** (max: 28 days)
- **Weeks** (max: 4 weeks / 28 days)

Examples:
- `/mod timeout @user 30 minutes Spamming`
- `/mod timeout @user 2 hours Inappropriate behavior`
- `/mod timeout @user 7 days Multiple violations`

## 🔧 Development

### Adding New Commands

1. Open `cogs/moderation.py`
2. Add a new command method decorated with `@mod.command()`
3. Implement your logic using the existing helper methods
4. Add error handling with a corresponding error handler

Example:
```python
@mod.command(name="warn", description="Warn a member")
@commands.has_permissions(manage_messages=True)
async def warn(self, ctx, member: discord.Member, reason: str = "No reason provided"):
    # Your implementation here
    pass
```

### Using the MySQL Helper

The `MySQLHelper` class provides easy database operations:

```python
# Fetch one row
user = self.db.fetch_one_dict("SELECT * FROM users WHERE user_id = %s", (user_id,))

# Fetch all rows
users = self.db.fetch_all_dict("SELECT * FROM users WHERE guild_id = %s", (guild_id,))

# Insert data
self.db.insert("table_name", {"column":  "value"})

# Update data
self.db.update("table_name", {"column": "new_value"}, "id = %s", (id,))

# Execute custom query
self.db.execute_query("DELETE FROM table WHERE condition = %s", (value,))
```

## 🐛 Troubleshooting

### Bot doesn't respond to commands
- Ensure the bot has been invited with the `applications.commands` scope
- Check that the bot has the required permissions in your server
- Verify your bot token is correct in `.env`

### Database connection errors
- Verify MySQL/MariaDB is running
- Check database credentials in `.env`
- Ensure the database exists
- Check if the MySQL port is accessible

### Permission errors
- Ensure the bot's role is higher than the roles it's moderating
- Check that the bot has the required permissions (Ban Members, Kick Members, Moderate Members)
- Verify channel permissions for sending messages and embeds

### Commands not showing up
- Try restarting the bot
- Discord may take up to an hour to sync commands globally
- Use guild-specific commands for instant updates during development

## 📝 License

This project is licensed under the **MIT License** - see below for details: 

```
MIT License

Copyright (c) 2025 Bobbie McDylan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.  Since this project is under heavy development, please open an issue first to discuss major changes.

### Contribution Guidelines

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📞 Support

If you encounter any issues or have questions: 

1. Check the [Troubleshooting](#-troubleshooting) section
2. Search existing [GitHub Issues](https://github.com/bobbiemcdylan24/synergy-pro/issues)
3. Create a new issue with detailed information about your problem

## 🗺️ Roadmap

- [ ] Warning system with accumulation tracking
- [ ] Mute/unmute commands
- [ ] Case management and lookup
- [ ] Appeal system
- [ ] Auto-moderation features
- [ ] Moderation statistics
- [ ] Export punishment history
- [ ] Multi-language support
- [ ] Web dashboard

## ✨ Acknowledgments

- [Py-cord](https://github.com/Pycord-Development/pycord) - Discord API wrapper
- [mysql-connector-python](https://dev.mysql.com/doc/connector-python/en/) - MySQL database connector
- Discord. py community for inspiration and support

## 📊 Statistics

- **Language:** Python 3.8+
- **Database:** MySQL/MariaDB
- **Framework:** Py-cord
- **License:** MIT
- **Status:** Under Development

---

**Note:** This bot is under active development. Features, commands, and database schemas may change. Always backup your database before updating! 

Made with ❤️ by Bobbie McDylan