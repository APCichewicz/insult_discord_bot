import os
from time import time, sleep
import json
from typing import Optional
import pika
from riotwatcher import TftWatcher, RiotWatcher
from dotenv import load_dotenv
import requests
import redis
import logging
import sys

load_dotenv()

def setup_logging():
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    logging.basicConfig(
        level=getattr(logging, log_level),
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler('lolwatcher.log')
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized with level: {log_level}")
    return logger

logger = setup_logging()

database_url = os.getenv('DATABASE_URL')
if not database_url:
    raise ValueError("DATABASE_URL environment variable not set")

class TftMatchWatcher:
    def __init__(self):
        # Logging setup
        self.logger = logging.getLogger(f"{__name__}.TftMatchWatcher")
        self.logger.info("Initializing TftMatchWatcher")
        
        # Riot API setup
        self.api_key = os.getenv('RIOT_API_KEY')
        self.region = os.getenv('RIOT_REGION', 'na1')
        self.watcher = RiotWatcher(self.api_key)
        self.tft_watcher = TftWatcher(self.api_key)
        self.logger.info(f"Using region: {self.region}")
        
        # RabbitMQ setup
        self.rabbitmq_url = os.getenv('RABBITMQ_URL', 'localhost')
        self.queue_name = os.getenv('RABBITMQ_QUEUE', 'tft_matches')
        self.logger.info(f"RabbitMQ URL: {self.rabbitmq_url}, Queue: {self.queue_name}")
        self.setup_rabbitmq()
        
        # Redis setup
        self.redis_host = os.getenv('REDIS_HOST', 'localhost')
        self.redis_port = int(os.getenv('REDIS_PORT', '6379'))
        self.redis_db = int(os.getenv('REDIS_DB', '0'))
        self.logger.info(f"Redis config - Host: {self.redis_host}, Port: {self.redis_port}, DB: {self.redis_db}")
        self.setup_redis()
        
        self.logger.info("TftMatchWatcher initialization complete")


    def setup_rabbitmq(self):
        """Initialize RabbitMQ connection and channel"""
        try:
            self.logger.info(f"Connecting to RabbitMQ at {self.rabbitmq_url}")
            self.connection = pika.BlockingConnection(
                pika.URLParameters(
                    self.rabbitmq_url
                )
            )
            self.channel = self.connection.channel()
            self.channel.queue_declare(queue=self.queue_name)
            self.logger.info(f"RabbitMQ connection established, queue '{self.queue_name}' declared")
        except Exception as e:
            self.logger.error(f"Failed to setup RabbitMQ: {e}")
            raise

    def setup_redis(self):
        """Initialize Redis connection"""
        try:
            self.logger.info("Attempting to connect to Redis")
            self.redis_client = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                db=self.redis_db,
                decode_responses=True
            )
            self.redis_client.ping()
            self.logger.info("Successfully connected to Redis")
        except Exception as e:
            self.logger.error(f"Error connecting to Redis: {e}")
            self.logger.warning("Continuing without Redis cache")
            self.redis_client = None

    def cache_match(self, summoner_name: str, match_id: str):
        """Cache match ID in Redis with 24 hour TTL"""
        if not self.redis_client:
            self.logger.debug(f"Redis not available, skipping match cache for {summoner_name}")
            return
        try:
            self.redis_client.setex(f"match:{summoner_name}", 86400, match_id)
            self.logger.debug(f"Cached match {match_id} for {summoner_name}")
        except Exception as e:
            self.logger.error(f"Error caching match for {summoner_name}: {e}")

    def get_cached_match(self, summoner_name: str) -> Optional[str]:
        """Get match ID from Redis cache"""
        if not self.redis_client:
            self.logger.debug(f"Redis not available, no cached match for {summoner_name}")
            return None
        try:
            cached_match = self.redis_client.get(f"match:{summoner_name}")
            if cached_match:
                self.logger.debug(f"Found cached match {cached_match} for {summoner_name}")
            else:
                self.logger.debug(f"No cached match found for {summoner_name}")
            return cached_match
        except Exception as e:
            self.logger.error(f"Error getting cached match for {summoner_name}: {e}")
            return None

    def get_latest_match(self, puuid: str) -> Optional[str]:
        """Get the latest match ID for the summoner"""
        try:
            now = time() - (15 * 60)
            self.logger.debug(f"Fetching latest match for PUUID {puuid[:8]}... since {int(now)}")
            matches = self.tft_watcher.match.by_puuid(self.region, puuid, count=1, start_time=int(now))
            if matches:
                self.logger.debug(f"Found latest match: {matches[0]}")
                return matches[0]
            else:
                self.logger.debug(f"No recent matches found for PUUID {puuid[:8]}...")
                return None
        except Exception as e:
            self.logger.error(f"Error fetching latest match for PUUID {puuid[:8]}...: {e}")
            return None

    def get_match_details(self, match_id: str) -> dict:
        """Get detailed information about a specific match"""
        try:
            self.logger.debug(f"Fetching match details for {match_id}")
            match_details = self.tft_watcher.match.by_id(self.region, match_id)
            self.logger.info(f"Successfully retrieved match details for {match_id}")
            return match_details
        except Exception as e:
            self.logger.error(f"Error fetching match details for {match_id}: {e}")
            raise

    def publish_match(self, match_data: dict, summoner_name: str, guild_id: str):
        """Publish match data to RabbitMQ queue"""
        try:
            message = {
                'match_data': match_data,
                'summoner_name': summoner_name,
                'guild_id': guild_id,
            }
            self.logger.info(f"Publishing match data for {summoner_name} to queue {self.queue_name}")
            self.channel.basic_publish(
                exchange='',
                routing_key=self.queue_name,
                body=json.dumps(message),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                )
            )
            self.logger.debug(f"Successfully published match message for {summoner_name}")
            self.cache_match(summoner_name, match_data['metadata']['match_id'])
        except Exception as e:
            self.logger.error(f"Error publishing match data for {summoner_name}: {e}")
            raise

    def get_cached_summoners(self):
        """Get summoners from Redis cache"""
        if not self.redis_client:
            self.logger.debug("Redis not available, no cached summoners")
            return None
        try:
            cached_data = self.redis_client.get("summoners_cache")
            if cached_data:
                summoners = json.loads(cached_data)
                self.logger.debug(f"Retrieved {len(summoners)} summoners from cache")
                return summoners
            else:
                self.logger.debug("No summoners found in cache")
        except Exception as e:
            self.logger.error(f"Error reading summoners from Redis cache: {e}")
        return None

    def cache_summoners(self, summoners):
        """Cache summoners in Redis with 5 minute TTL"""
        if not self.redis_client:
            self.logger.debug("Redis not available, skipping summoners cache")
            return
        try:
            self.redis_client.setex(
                "summoners_cache", 
                300,  
                json.dumps(summoners)
            )
            self.logger.debug(f"Cached {len(summoners)} summoners for 5 minutes")
        except Exception as e:
            self.logger.error(f"Error caching summoners: {e}")

    def update_summoners(self):
        """Fetch updated list of summoners from cache or database"""
        self.logger.debug("Updating summoners list")
        cached_summoners = self.get_cached_summoners()
        if cached_summoners:
            summoners = cached_summoners
            self.logger.debug("Using cached summoners")
        else:
            try:
                self.logger.info(f"Fetching summoners from database at {database_url}")
                response = requests.get(f"http://{database_url}/get_summoners", timeout=30)
                if response.status_code == 200:
                    summoners = response.json()
                    self.cache_summoners(summoners)
                    self.logger.info(f"Retrieved {len(summoners)} summoners from database")
                else:
                    self.logger.error(f"Database request failed with status {response.status_code}")
                    return []
            except Exception as e:
                self.logger.error(f"Error fetching summoners from database: {e}")
                return []
        return summoners
    def get_summoner_puuid(self, summoner_name: str, tagline: str) -> str:
        """Get summoner's PUUID from cache, memory, or API"""
        cached_puuid = self.get_cached_puuid(summoner_name)
        if cached_puuid:
            return cached_puuid
        return self.get_puid(summoner_name, tagline)
    
    def get_puid(self, summoner_name: str, tagline: str) -> str:
        """Get summoner's PUUID from API"""
        summoner = self.watcher.account.by_riot_id('AMERICAS', summoner_name, tagline)
        puuid = summoner['puuid']
        self.cache_puuid(summoner_name, puuid)
        return puuid

    def get_cached_puuid(self, summoner_name: str) -> Optional[str]:
        """Get PUUID from Redis cache"""
        if not self.redis_client:
            self.logger.debug(f"Redis not available, no cached PUUID for {summoner_name}")
            return None
        try:
            puuid = self.redis_client.get(f"puuid:{summoner_name}")
            if puuid:
                self.logger.debug(f"Found cached PUUID for {summoner_name}")
            else:
                self.logger.debug(f"No cached PUUID found for {summoner_name}")
            return puuid
        except Exception as e:
            self.logger.error(f"Error reading PUUID from cache for {summoner_name}: {e}")
            return None

    def cache_puuid(self, summoner_name: str, puuid: str):
        """Cache PUUID in Redis with 24 hour TTL"""
        if not self.redis_client:
            self.logger.debug(f"Redis not available, skipping PUUID cache for {summoner_name}")
            return
        try:
            self.redis_client.setex(f"puuid:{summoner_name}", 86400, puuid) 
            self.logger.debug(f"Cached PUUID for {summoner_name}")
        except Exception as e:
            self.logger.error(f"Error caching PUUID for {summoner_name}: {e}")

    def get_ƒuid(self, summoner_name: str, tagline: str) -> str:
        """Get summoner's PUUID from cache, memory, or API"""
        
        cached_puuid = self.get_cached_puuid(summoner_name)
        if cached_puuid:
            return cached_puuid
        
        try:
            self.logger.info(f"Fetching PUUID from API for {summoner_name}#{tagline}")
            summoner = self.watcher.account.by_riot_id('AMERICAS', summoner_name, tagline)
            puuid = summoner['puuid']
            self.cache_puuid(summoner_name, puuid)
            self.logger.info(f"Successfully retrieved PUUID for {summoner_name}")
            return puuid
        except Exception as e:
            self.logger.error(f"Error getting PUUID for {summoner_name}#{tagline}: {e}")
            return None


    def watch_matches(self, interval: int = 15):
        """Main loop to watch for new matches for all summoners"""
        self.logger.info(f"Starting to watch matches for all summoners (interval: {interval}s)")
        
        while True:
            try:
                summoners = self.update_summoners()
                if not summoners:
                    self.logger.warning("No summoners found, waiting for next cycle")
                    sleep(interval)
                    continue
                
                self.logger.info(f"Checking matches for {len(summoners)} summoners")
                
                matches_processed = 0
                errors_count = 0
                
                for summoner in summoners:
                    summoner_name = summoner['summoner_name']
                    guild_id = summoner['summoner_guild_id']
                    tagline = summoner.get('summoner_tagline', '')
                    
                    try:
                        self.logger.debug(f"Processing summoner: {summoner_name}#{tagline}")
                        puuid = self.get_summoner_puuid(summoner_name, tagline)
                        if not puuid:
                            self.logger.warning(f"Could not get PUUID for {summoner_name}, skipping")
                            continue
                            
                        latest_match = self.get_latest_match(puuid)
                        
                        cached_match = self.get_cached_match(summoner_name)
                        if latest_match and latest_match != cached_match:
                            self.logger.info(f"New match found for {summoner_name}: {latest_match} (previous: {cached_match})")
                            match_details = self.get_match_details(latest_match)
                            self.publish_match(match_details, summoner_name, guild_id)
                            matches_processed += 1
                        else:
                            self.logger.debug(f"No new matches for {summoner_name}")
                    
                    except Exception as e:
                        errors_count += 1
                        self.logger.error(f"Error processing summoner {summoner_name}: {e}")
                        continue
                    
                    sleep(1)
                
                sleep(interval)
                
            except KeyboardInterrupt:
                self.logger.info("Received shutdown signal")
                raise
            except Exception as e:
                sleep(interval)

    def close(self):
        """Clean up connections"""
        self.logger.info("Cleaning up connections")
        try:
            if hasattr(self, 'connection') and self.connection.is_open:
                self.connection.close()
                self.logger.info("RabbitMQ connection closed")
        except Exception as e:
            self.logger.error(f"Error closing RabbitMQ connection: {e}")
            
        try:
            if hasattr(self, 'redis_client') and self.redis_client:
                self.redis_client.close()
                self.logger.info("Redis connection closed")
        except Exception as e:
            self.logger.error(f"Error closing Redis connection: {e}")
        
        self.logger.info("Cleanup complete")

def main():
    logger.info("Starting TFT Match Watcher application")
    watcher = None
    try:
        watcher = TftMatchWatcher()
        watcher.watch_matches()
    except KeyboardInterrupt:
        logger.info("Received shutdown signal, stopping match watcher...")
    except Exception as e:
        logger.error(f"Fatal error in main: {e}")
        raise
    finally:
        if watcher:
            watcher.close()
        logger.info("TFT Match Watcher application stopped")


if __name__ == "__main__":
    main()
