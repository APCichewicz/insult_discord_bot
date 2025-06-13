# TFT Discord Bot System

A microservices-based Discord bot system that tracks Teamfight Tactics (TFT) matches and generates AI-powered trash talk using Claude AI, with text-to-speech functionality.

## Architecture

This system consists of 6 microservices orchestrated with Docker Compose:

- **Discord Bot** - Main bot interface handling Discord commands and voice playback
- **TFT Watcher** - Monitors TFT matches for registered summoners
- **Claude Querier** - Generates witty trash talk using Anthropic's Claude AI
- **TTS Service** - Converts text to speech using Go TTS library
- **Database Service** - HTTP API for summoner data with PostgreSQL backend
- **Infrastructure** - Redis cache, RabbitMQ message broker, PostgreSQL database

## Features

- Track multiple TFT summoners across Discord servers
- Real-time match monitoring with Redis caching
- AI-generated trash talk for match results
- Text-to-speech audio playback in Discord voice channels
- RESTful API for summoner management
- Scalable microservices architecture with message queues

## Quick Start

1. Clone the repository
2. Set up environment variables (see Configuration section)
3. Run with Docker Compose:

```bash
docker-compose up -d
```

## Discord Commands

- `!add_summoner <name>#<tagline>` - Add a summoner to track
- `!ping` - Test bot connectivity

## Configuration

Required environment variables:

- `DISCORD_TOKEN` - Discord bot token
- `RIOT_API_KEY` - Riot Games API key
- `ANTHROPIC_API_KEY` - Anthropic Claude API key

## Services

### Discord Bot (`bot/`)
- Python Discord.py bot
- Handles voice channel connections
- Processes RabbitMQ audio messages
- Validates and adds summoners via Riot API

### TFT Watcher (`lolwatcher/`)
- Python service monitoring TFT matches
- Redis caching for performance
- Publishes match data to message queue
- Configurable polling intervals

### Claude Querier (`claude-querier/`)
- Consumes match data from queue
- Generates trash talk using Claude AI
- Publishes zingers to TTS queue

### TTS Service (`tts_service/`)
- Go service for text-to-speech
- Converts text to Opus audio format
- Publishes audio messages for Discord playback

### Database Service (`db_service/`)
- Go HTTP API server
- PostgreSQL backend for summoner data
- Redis caching layer
- RESTful endpoints for CRUD operations

## Development

Each service has its own Dockerfile and can be developed independently. The system uses RabbitMQ for inter-service communication and Redis for caching.

## Dependencies

- Docker & Docker Compose
- FFmpeg (for audio conversion)
- PostgreSQL 15
- Redis 7
- RabbitMQ 3

## API Endpoints

- `POST /add_summoner` - Add new summoner
- `GET /get_summoners` - List all summoners
- `POST /update_summoner` - Update summoner data