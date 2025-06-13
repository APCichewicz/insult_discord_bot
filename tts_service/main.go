package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"time"

	// htgo-tts
	htgotts "github.com/hegedustibor/htgo-tts"
	handlers "github.com/hegedustibor/htgo-tts/handlers"
	voices "github.com/hegedustibor/htgo-tts/voices"

	amqp "github.com/rabbitmq/amqp091-go"
)

type ZingerMessage struct {
	Text    string `json:"zinger"`
	GuildID string `json:"guild_id"`
}

type AudioMessage struct {
	Filename string `json:"filename"`
	Path     string `json:"path"`
	GuildID  string `json:"guild_id"`
}

func main() {
	rabbitmqURL := getEnv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
	audioDir := getEnv("AUDIO_DIR", "/shared/audio")

	// Ensure audio directory exists
	if err := os.MkdirAll(audioDir, 0755); err != nil {
		log.Fatalf("Failed to create audio directory: %v", err)
	}

	// Connect to RabbitMQ
	conn, err := amqp.Dial(rabbitmqURL)
	if err != nil {
		log.Fatalf("Failed to connect to RabbitMQ: %v", err)
	}
	defer conn.Close()

	ch, err := conn.Channel()
	if err != nil {
		log.Fatalf("Failed to open channel: %v", err)
	}
	defer ch.Close()

	// Declare queues
	_, err = ch.QueueDeclare("zingers", true, false, false, false, nil)
	if err != nil {
		log.Fatalf("Failed to declare zingers queue: %v", err)
	}

	_, err = ch.QueueDeclare("audio_queue", true, false, false, false, nil)
	if err != nil {
		log.Fatalf("Failed to declare audio_queue: %v", err)
	}

	// Consume messages from zingers queue
	msgs, err := ch.Consume("zingers", "", false, false, false, false, nil)
	if err != nil {
		log.Fatalf("Failed to register consumer: %v", err)
	}

	log.Println("TTS Service started. Waiting for messages...")

	for msg := range msgs {
		log.Printf("Received message: %s", msg.Body)

		var zingerMsg ZingerMessage
		if err := json.Unmarshal(msg.Body, &zingerMsg); err != nil {
			log.Printf("Failed to unmarshal message: %v", err)
			msg.Nack(false, false)
			continue
		}

		// Generate TTS file
		filename := fmt.Sprintf("tts_%d", time.Now().Unix())

		opus_path, err := generateTTS(zingerMsg.Text, filename, audioDir)
		if err != nil {
			log.Printf("Failed to generate TTS: %v", err)
			msg.Nack(false, false)
			continue
		}

		audioMsg := AudioMessage{
			Filename: filename,
			Path:     opus_path,
			GuildID:  zingerMsg.GuildID,
		}

		audioMsgJSON, err := json.Marshal(audioMsg)
		if err != nil {
			log.Printf("Failed to marshal audio message: %v", err)
			msg.Nack(false, false)
			continue
		}

		err = ch.Publish("", "audio_queue", false, false, amqp.Publishing{
			ContentType: "application/json",
			Body:        audioMsgJSON,
		})

		if err != nil {
			log.Printf("Failed to publish audio message: %v", err)
			msg.Nack(false, false)
			continue
		}

		log.Printf("Generated TTS file: %s", opus_path)
		msg.Ack(false)
	}

}

func generateTTS(text, filename string, audioDir string) (string, error) {

	speech := htgotts.Speech{
		Folder:   audioDir,
		Language: voices.English,
		Handler:  &handlers.Native{},
	}

	path, err := speech.CreateSpeechFile(text, filename)
	if err != nil {
		return "", fmt.Errorf("failed to create speech: %v", err)
	}

	opus_path := filepath.Join(audioDir, filename+".opus")

	convertCmd := exec.Command("ffmpeg", "-i", path, "-c:a", "libopus", opus_path)
	if err := convertCmd.Run(); err != nil {
		os.Remove(opus_path)
		os.Remove(path)
		return "", fmt.Errorf("failed to convert audio: %v", err)
	}

	os.Remove(path)

	return opus_path, nil
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}
