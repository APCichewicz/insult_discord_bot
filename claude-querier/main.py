import anthropic
import os
import json
import pika
import sys

print("Script starting...", flush=True)

try:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    print("Anthropic client created", flush=True)
    
    rabbitmq_url = os.getenv("RABBITMQ_URL")
    if not rabbitmq_url:
        raise ValueError("RABBITMQ_URL environment variable is not set")
    print(f"RabbitMQ URL: {rabbitmq_url}", flush=True)
    
    print("Attempting RabbitMQ connection...", flush=True)
    connection = pika.BlockingConnection(pika.URLParameters(rabbitmq_url))
    print("RabbitMQ connected successfully", flush=True)
    
    listen_channel = connection.channel()
    send_channel = connection.channel()
    print("Channels created", flush=True)
    
    listen_channel.queue_declare(queue="tft_matches")
    send_channel.queue_declare(queue="zingers", durable=True)
    print("Queues declared", flush=True)
    
except Exception as e:
    print(f"Error during setup: {e}", flush=True)
    sys.exit(1)

def get_zinger(match_details, target_player):
    messages = [
        {
            "role": "user",
            "content": f"""
            target player: {target_player}
            match details: {match_details}
            """
        }
    ]
    response = client.messages.create(
        model="claude-3-5-sonnet-20240620",
        system="You are a trash-talk generator you will only respond with your zinger, a snarky line tailored to the target player and provided match details. the zinger should include the target player's name and their placement in the match. the zinger should be creative and funny. Never include explanations, quotes, or special characters.",
        messages=messages,
        max_tokens=1000,
        temperature=1,
    )

    return response.content[0].text

def listen_for_matches():
    listen_channel.basic_consume(queue="tft_matches", on_message_callback=callback, auto_ack=False)
    print("Waiting for matches...", flush=True)
    listen_channel.start_consuming()

def callback(ch, method, properties, body):
    try:
        message = json.loads(body)
        target_player = message["summoner_name"]
        match_data = message["match_data"]
        guild_id = message["guild_id"]

        print(f"Received match data for {target_player} in guild {guild_id}", flush=True)
        
        zinger = get_zinger(match_data, target_player)
        zinger_message = {
            "zinger": zinger,
            "guild_id": guild_id,
        }
        print(f"Sending zinger to {guild_id}: {zinger}", flush=True)
        send_channel.basic_publish(exchange='', routing_key='zingers', body=json.dumps(zinger_message))
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except Exception as e:
        print(f"Error processing message: {e}", flush=True)
        ch.basic_nack(delivery_tag=method.delivery_tag)

if __name__ == "__main__":
    try:
        listen_for_matches()
    except Exception as e:
        print(f"Error in main: {e}", flush=True)
        sys.exit(1)