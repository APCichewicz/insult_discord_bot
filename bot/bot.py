import os
import asyncio
import discord
from discord.ext import commands
import aio_pika
import json
import requests
import re
import logging
from riotwatcher import RiotWatcher

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable not set")
rabbitMQ_URL = os.getenv('RABBITMQ_URL', 'amqp://guest:guest@rabbitmq:5672/')
database_url = os.getenv('DATABASE_URL')

logger.info(f"Starting Discord bot with database URL: {database_url}")
logger.info(f"RabbitMQ URL: {rabbitMQ_URL}")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True 
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents)  
audio_lock = asyncio.Lock()

@bot.command(name='add_summoner', description='Add a summoner to the bot')
async def add_summoner(ctx, summoner_info: str):
    logger.info(f"Received add_summoner command from {ctx.author} in guild {ctx.guild.id}: {summoner_info}")
    
    if '#' not in summoner_info:
        logger.warning(f"Invalid summoner format from {ctx.author}: {summoner_info}")
        await ctx.send("Summoner info must be of form summoner_name#summoner_tagline")
        return
    
    summoner_info = summoner_info.split('#')
    summoner_name = summoner_info[0]
    summoner_tagline = summoner_info[1]
    summoner_guild_id = ctx.guild.id
    
    if not re.match(r'^[a-zA-Z0-9]{3,}$', summoner_tagline):
        logger.warning(f"Invalid tagline format from {ctx.author}: {summoner_tagline}")
        await ctx.send("Summoner tagline must be at least 3 characters long and alphanumeric")
        return
    
    if not re.match(r'^[a-zA-Z0-9]+$', summoner_name):
        logger.warning(f"Invalid summoner name format from {ctx.author}: {summoner_name}")
        await ctx.send("Summoner name must be alphanumeric and no spaces")
        return


    try:
        watcher = RiotWatcher(os.getenv('RIOT_API_KEY'))
        summoner_puuid = watcher.account.by_riot_id("AMERICAS", summoner_name, summoner_tagline)
        if not summoner_puuid:
            logger.error(f"Failed to get summoner PUUID for {summoner_name}#{summoner_tagline}")
            await ctx.send("❌ Failed to add summoner")
            return
        logger.info(f"Sending add_summoner request to database: {summoner_name}#{summoner_tagline}")
        response = requests.post(f"{database_url}/add_summoner", json={'summoner_name': str(summoner_name), 'summoner_guild_id': str(summoner_guild_id), 'summoner_tagline': str(summoner_tagline), 'summoner_puuid': str(summoner_puuid)})
        if response.status_code != 201:
            logger.error(f"Failed to add summoner {summoner_name}#{summoner_tagline}: {response.status_code} - {response.text}")
            await ctx.send("❌ Failed to add summoner")
            return
        logger.info(f"Successfully added summoner {summoner_name}#{summoner_tagline}")
        await ctx.send(f"✅ Summoner {summoner_name}#{summoner_tagline} added to the bot")
    except Exception as e:
        logger.error(f"Exception adding summoner {summoner_name}#{summoner_tagline}: {e}")
        await ctx.send("❌ Failed to add summoner")


@bot.command(name='ping')
async def ping(ctx):
    logger.info(f"Ping command from {ctx.author} in guild {ctx.guild.id}")
    await ctx.send('Pong! Bot is working!')


async def play_audio(guild, channel, filename):
    async with audio_lock:
        logger.info(f"Attempting to play audio in guild {guild.name}, channel {channel.name}: {filename}")
        try:
            voice_client = await channel.connect()
            logger.info(f"Connected to voice channel {channel.name}")
        except discord.ClientException:
            voice_client = discord.utils.get(bot.voice_clients, guild=guild)
            logger.info(f"Using existing voice connection for {guild.name}")
        if not voice_client:
            logger.error("Failed to connect to voice channel")
            return
        if not os.path.isfile(filename):
            logger.error(f"Audio file {filename} not found")
            await voice_client.disconnect()
            return
        
        logger.info(f"Playing audio file: {filename}")
        if not os.path.isfile(filename):
            logger.error(f"Audio file {filename} not found")
            await voice_client.disconnect()
            return
        audio_source = discord.FFmpegPCMAudio(filename)
        voice_client.play(audio_source)
        while voice_client.is_playing():
            await asyncio.sleep(0.1)
        voice_client.stop()
        logger.info(f"Finished playing audio, disconnecting from {channel.name}")
        await voice_client.disconnect()

async def setup_rabbitmq():
    connection = await aio_pika.connect_robust(rabbitMQ_URL)
    channel = await connection.channel()
    queue = await channel.declare_queue('audio_queue', durable=True)
    
    async def process_message(message):
        try:
            async with message.process():
                msg = json.loads(message.body.decode())
                filename = msg.get('filename')
                path = msg.get('path')
                guild_id = msg.get('guild_id')
                
                guild = bot.get_guild(int(guild_id))
                if not guild:
                    logger.error(f"Guild {guild_id} not found")
                    return
                
                audio_path = path if path else filename
                
                voice_channel = None
                for channel in guild.voice_channels:
                    if channel.members:
                        voice_channel = channel
                        break
                
                if voice_channel:
                    await play_audio(guild, voice_channel, audio_path)
                    
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    await queue.consume(process_message)
    return connection

async def main():
    logger.info("Starting Discord bot and RabbitMQ...")
    
    await asyncio.gather(
        setup_rabbitmq(),
        bot.start(TOKEN)
    )

if __name__ == "__main__":
    asyncio.run(main())