# Import required modules
import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor

# Load environment variables (e.g., Discord bot token)
load_dotenv()
TOKEN = os.getenv("discord_token")

# Set up Discord bot with necessary intents (specifically, to read message content)
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='?', intents=intents)

# Data storage for each server (guild)
voice_clients = {}        # Tracks active voice clients per server
song_queues = {}          # Tracks queues per server
loop_flags = {}           # Tracks if looping is on/off per server
currently_playing = {}    # Tracks the currently playing song per server

# Configure yt_dlp to extract best quality audio stream (preferably m4a)
yt_dl_options = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",  # Prefer m4a to avoid compatibility issues
    "noplaylist": True,      # Prevents playlists from being queued accidentally
    "quiet": True,           # Suppress verbose output
    "default_search": "ytsearch",  # Allow search terms instead of only URLs
    "extract_flat": False,   # We want full metadata, not just URLs
    "forceurl": True,        # Return direct media URL
    "simulate": True,        # Don't download, just simulate for metadata
    "no_warnings": True,     # Suppress warnings
    "source_address": "0.0.0.0",  # Avoid network issues on some systems
    "geo_bypass": True       # Bypass regional restrictions if any
}

# Initialize yt_dlp and a thread pool to avoid blocking the main bot loop
ytdl = yt_dlp.YoutubeDL(yt_dl_options)
ytdl_executor = ThreadPoolExecutor(max_workers=2)  # Helps reduce lag when queuing

# Options for ffmpeg audio streaming
ffmpeg_options = {
    'before_options': '-nostdin',
    'options': '-vn'  # Disable video processing
}

# Plays the next song in the queue if available
async def play_next(ctx, guild_id):
    if guild_id not in song_queues or not song_queues[guild_id]:
        currently_playing[guild_id] = None
        return

    # Pop the next song and start playback
    song = song_queues[guild_id].pop(0)
    player = discord.FFmpegPCMAudio(song['url'], **ffmpeg_options)
    currently_playing[guild_id] = song

    # Callback after the song finishes
    def after_playing(error):
        if error:
            print(f"Error during playback: {error}")
        # If loop is enabled, reinsert the song
        if loop_flags.get(guild_id):
            song_queues[guild_id].insert(0, song)
        # Schedule the next song
        fut = asyncio.run_coroutine_threadsafe(play_next(ctx, guild_id), bot.loop)
        try:
            fut.result()
        except Exception as e:
            print(f"Playback callback error: {e}")

    voice_clients[guild_id].play(player, after=after_playing)
    await ctx.send(f"🎶 Now playing: **{song['title']}**")

# Let us know the bot is ready and online
@bot.event
async def on_ready():
    print(f'{bot.user} is connected and ready!')

# Grabs metadata from YouTube based on URL or search term
async def resolve_song_metadata(search_term):
    data = await bot.loop.run_in_executor(ytdl_executor, lambda: ytdl.extract_info(search_term, download=False))
    if 'entries' in data:
        data = data['entries'][0]  # Get the first result (in case of search/playlist)

    # Block livestreams to prevent ffmpeg crashes
    if data.get('is_live'):
        raise Exception("Livestreams are not supported.")

    return {
        'url': data['url'],
        'title': data['title']
    }

# Play command: queues a song and starts playback if nothing is playing
@bot.command()
@commands.cooldown(1, 2, commands.BucketType.user)  # Prevents spam
async def play(ctx, *, search: str = None):
    if not search:
        await ctx.send("❗ Please provide a YouTube link or search term.")
        return

    guild_id = ctx.guild.id
    try:
        # Connect to the voice channel if not already connected
        if guild_id not in voice_clients or not voice_clients[guild_id].is_connected():
            voice_clients[guild_id] = await ctx.author.voice.channel.connect()

        # Get song metadata
        song = await resolve_song_metadata(search)

        # Add to queue
        if guild_id not in song_queues:
            song_queues[guild_id] = []
        song_queues[guild_id].append(song)

        await ctx.send(f"✅ Queued: **{song['title']}**")

        # Start playing if nothing is already playing
        if not voice_clients[guild_id].is_playing():
            await play_next(ctx, guild_id)

    except Exception as e:
        print(e)
        await ctx.send("❌ Error playing the song.")

# Pause the current song
@bot.command()
async def pause(ctx):
    try:
        voice_clients[ctx.guild.id].pause()
        await ctx.send("⏸️ Paused.")
    except Exception as e:
        print(e)

# Resume the paused song
@bot.command()
async def resume(ctx):
    try:
        voice_clients[ctx.guild.id].resume()
        await ctx.send("▶️ Resumed.")
    except Exception as e:
        print(e)

# Stop playback and leave the voice channel
@bot.command()
async def stop(ctx):
    try:
        voice_clients[ctx.guild.id].stop()
        await voice_clients[ctx.guild.id].disconnect()
        song_queues[ctx.guild.id] = []
        currently_playing[ctx.guild.id] = None
        await ctx.send("🛑 Stopped and disconnected.")
    except Exception as e:
        print(e)

# Skip the current song
@bot.command()
async def skip(ctx):
    try:
        voice_clients[ctx.guild.id].stop()
        await ctx.send("⏭️ Skipping current song...")
    except Exception as e:
        print(e)

# Toggle looping for the current queue
@bot.command()
async def loop(ctx):
    guild_id = ctx.guild.id
    loop_flags[guild_id] = not loop_flags.get(guild_id, False)
    status = "enabled 🔁" if loop_flags[guild_id] else "disabled ➿"
    await ctx.send(f"Loop is now {status}.")

# Show the current queue of songs
@bot.command()
async def queue(ctx):
    guild_id = ctx.guild.id
    if guild_id not in song_queues or not song_queues[guild_id]:
        await ctx.send("📭 Queue is empty.")
        return

    queue_list = '\n'.join([f"{idx+1}. {song['title']}" for idx, song in enumerate(song_queues[guild_id])])
    await ctx.send(f"📜 Current queue:\n{queue_list}")

# Show the currently playing song
@bot.command()
async def nowplaying(ctx):
    guild_id = ctx.guild.id
    current = currently_playing.get(guild_id)
    if current:
        await ctx.send(f"🎧 Currently playing: **{current['title']}**")
    else:
        await ctx.send("❌ Nothing is playing right now.")

# Start the bot using the token from .env
bot.run(TOKEN)
