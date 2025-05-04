import discord
from discord.ext import commands, tasks
import sqlite3
import os
from discord.ui import Button, View
from discord.ext import commands
import aiohttp
import requests
import time
import re
from datetime import timedelta

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.bans = True
intents.messages = True
intents.typing = False
intents.presences = False
bot = commands.Bot(command_prefix="&", intents=intents)

# Database setup
def db_connect():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS punishments (
                user_id INTEGER,
                punishment TEXT,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS warnings (
                user_id INTEGER,
                reason TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    return conn, c

# Logging and Punishments
def log_punishment(user_id, punishment, reason):
    conn, c = db_connect()
    c.execute("INSERT INTO punishments (user_id, punishment, reason) VALUES (?, ?, ?)", (user_id, punishment, reason))
    conn.commit()
    conn.close()

def log_warning(user_id, reason):
    conn, c = db_connect()
    c.execute("INSERT INTO warnings (user_id, reason) VALUES (?, ?)", (user_id, reason))
    conn.commit()
    conn.close()

# Message Filter (Inappropriate Language)
blacklist = ["badword1", "badword2", "badword3"]  # Add more bad words

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    if any(word in message.content.lower() for word in blacklist):
        await message.delete()
        await message.channel.send(f"{message.author.mention}, your message contains inappropriate language!")
        log_warning(message.author.id, "Inappropriate language")
        await check_for_auto_ban_or_kick(message.author)

    if message.guild:
        # Anti-Spam (Existing feature)
        if message.content and len(message.content) > 0:
            await check_spam(message)

        # Anti-Invite Links
        if "discord.gg/" in message.content.lower():
            await message.delete()
            await message.channel.send(f"{message.author.mention}, posting invites is not allowed.")
            log_warning(message.author.id, "Posting invite links")
            await check_for_auto_ban_or_kick(message.author)

    await bot.process_commands(message)

async def check_spam(message):
    user = message.author
    current_time = time.time()

    conn, c = db_connect()
    c.execute("SELECT timestamp FROM punishments WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5", (user.id,))
    recent_msgs = c.fetchall()

    if len(recent_msgs) > 4:
        first_time = time.mktime(time.strptime(recent_msgs[-1][0], '%Y-%m-%d %H:%M:%S'))
        if current_time - first_time < 30:  # 30 seconds
            await message.delete()
            await message.channel.send(f"{user.mention}, please slow down. You're spamming.")
            log_punishment(user.id, "Spam", "Spamming messages in a short period.")
    conn.close()

# Anti-Bot Protection (Existing feature)
@bot.event
async def on_member_join(member):
    if member.bot:
        await member.kick(reason="Bots are not allowed to join.")
        await member.guild.system_channel.send(f"{member} was kicked for being a bot.")
        log_punishment(member.id, "Bot Kick", "Attempted to join as a bot.")

# Anti-Nuke Protection (Existing feature)
@bot.event
async def on_guild_channel_create(channel):
    # If too many channels are created in a short time, consider it as a nuke attempt
    conn, c = db_connect()
    c.execute("SELECT timestamp FROM punishments WHERE user_id = ? ORDER BY timestamp DESC LIMIT 3", (channel.guild.owner.id,))
    recent_activities = c.fetchall()

    if len(recent_activities) > 2:
        first_time = time.mktime(time.strptime(recent_activities[-1][0], '%Y-%m-%d %H:%M:%S'))
        if time.time() - first_time < 600:  # 10 minutes
            await channel.guild.system_channel.send(f"{channel.guild.owner.mention} is attempting to nuke the server!")
            log_punishment(channel.guild.owner.id, "Nuke Attempt", "Possible nuke attempt detected.")
    conn.close()

# Check if a user has crossed a threshold for banning/kicking
async def check_for_auto_ban_or_kick(user):
    conn, c = db_connect()
    c.execute("SELECT COUNT(*) FROM warnings WHERE user_id = ?", (user.id,))
    warning_count = c.fetchone()[0]

    if warning_count >= 3:
        # Auto-Kick or Auto-Ban
        if warning_count == 3:
            await user.kick(reason="Exceeded warning limit")
            log_punishment(user.id, "Kick", "Exceeded warning limit")
            await user.guild.system_channel.send(f"{user} was kicked for exceeding the warning limit.")
        elif warning_count >= 5:
            await user.ban(reason="Exceeded warning limit")
            log_punishment(user.id, "Ban", "Exceeded warning limit")
            await user.guild.system_channel.send(f"{user} was banned for exceeding the warning limit.")
    conn.close()

# Kick/Ban commands (Existing feature)


@bot.command(name="timeout")
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, user: discord.Member, duration: str, *, reason="No reason provided"):
    try:
        match = re.match(r"^(\d+)(s|sec|m|min|h|hr|d|day)$", duration.lower())
        if not match:
            await ctx.send("❌ Invalid duration format. Use formats like `1s`, `5min`, `2h`, `1d`.")
            return
        
        value, unit = int(match.group(1)), match.group(2)
        
        if unit in ['s', 'sec']:
            delta = timedelta(seconds=value)
        elif unit in ['m', 'min']:
            delta = timedelta(minutes=value)
        elif unit in ['h', 'hr']:
            delta = timedelta(hours=value)
        elif unit in ['d', 'day']:
            delta = timedelta(days=value)
        else:
            await ctx.send("❌ Invalid duration unit.")
            return

        until_time = discord.utils.utcnow() + delta
        await user.timeout(until_time, reason)  # ✅ FIXED: positional args only
        log_punishment(user.id, f"Timeout for {duration}", reason)
        await ctx.send(f"✅ {user.mention} has been timed out for **{duration}**.\n📝 Reason: {reason}")
    
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to timeout this member.")
    except Exception as e:
        await ctx.send(f"⚠️ An error occurred: `{e}`")



@bot.command(name="kick")
@commands.has_permissions(administrator=True)
async def kick(ctx, user: discord.Member, *, reason="No reason provided"):
    try:
        await user.send(f"You were kicked from **{ctx.guild.name}** for: {reason}")
    except discord.Forbidden:
        pass  # Can't send DM

    await user.kick(reason=reason)
    log_punishment(user.id, "Kick", reason)

    embed = discord.Embed(title="User Kicked", color=discord.Color.orange())
    embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.avatar.url)
    await ctx.send(embed=embed)


@bot.command(name="ban")
@commands.has_permissions(administrator=True)
async def ban(ctx, user: discord.Member, *, reason="No reason provided"):
    try:
        await user.send(f"You were banned from **{ctx.guild.name}** for: {reason}")
    except discord.Forbidden:
        pass  # Can't send DM

    await user.ban(reason=reason)
    log_punishment(user.id, "Ban", reason)

    embed = discord.Embed(title="User Banned", color=discord.Color.red())
    embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"Action by {ctx.author}", icon_url=ctx.author.avatar.url)
    await ctx.send(embed=embed)


# Custom Emoji Button for UI (Existing feature)
@bot.command(name="emoji")
async def emoji(ctx):
    button = Button(label="Click Me!", emoji="😊")

    async def button_callback(interaction):
        await interaction.response.send_message("You clicked the button!")
    
    button.callback = button_callback

    view = View()
    view.add_item(button)
    await ctx.send("Here’s a button for you!", view=view)

# Update bot status to show member protection count and custom playing status (Existing feature)
@tasks.loop(seconds=60)
async def update_status():
    total_members = 0
    for guild in bot.guilds:
        total_members += len(guild.members)
    
    await bot.change_presence(activity=discord.Game(name="The Best Auto-Moderation Bot"), status=discord.Status.online)
    await bot.change_presence(activity=discord.Game(name=f"Protecting {total_members} members"))

# Start the task when the bot is ready (Existing feature)
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    update_status.start()  # Start updating the bot's status

# Run the bot
bot.run(os.getenv('DISCORD_TOKEN'))
