package main

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"

	"github.com/go-redis/redis/v8"
	"github.com/jackc/pgx/v5"
)

type Summoner struct {
	Name     string `json:"summoner_name"`
	Guild_id string `json:"summoner_guild_id"`
	Tagline  string `json:"summoner_tagline"`
}

var db *pgx.Conn
var rdb *redis.Client

func init() {
	var err error
	db, err = pgx.Connect(context.Background(), os.Getenv("DATABASE_URL"))
	if err != nil {
		log.Fatal(err)
	}

	redisHost := os.Getenv("REDIS_HOST")
	if redisHost == "" {
		redisHost = "localhost:6379"
	}
	rdb = redis.NewClient(&redis.Options{
		Addr: redisHost,
		DB:   0,
	})

	_, err = rdb.Ping(context.Background()).Result()
	if err != nil {
		log.Printf("Redis connection failed: %v", err)
	} else {
		log.Println("Connected to Redis")
	}
	_, err = db.Exec(context.Background(), "CREATE TABLE IF NOT EXISTS summoners (id SERIAL PRIMARY KEY, name VARCHAR(255), guildid VARCHAR(255), tagline VARCHAR(255))")
	if err != nil {
		log.Fatal(err)
	}
}

func main() {
	log.Println("Starting database service on port 8000")
	http.HandleFunc("POST /add_summoner", add_summoner)
	http.HandleFunc("GET /get_summoners", get_summoners)
	http.HandleFunc("POST /update_summoner", update_summoner)
	log.Fatal(http.ListenAndServe(":8000", nil))
}

func invalidateCache() {
	err := rdb.Del(context.Background(), "summoners_cache").Err()
	if err != nil {
		log.Printf("Error invalidating summoners cache: %v", err)
	}
}

func add_summoner(w http.ResponseWriter, r *http.Request) {
	log.Printf("Received add_summoner request from %s", r.RemoteAddr)
	summoner := Summoner{}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		log.Printf("Error reading request body: %v", err)
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}
	err = json.Unmarshal(body, &summoner)
	if err != nil {
		log.Printf("Error unmarshaling JSON: %v", err)
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}
	log.Printf("Adding summoner: %s#%s for guild %s", summoner.Name, summoner.Tagline, summoner.Guild_id)
	var summonerID int
	err = db.QueryRow(context.Background(), "INSERT INTO summoners (name, guildid, tagline) VALUES ($1, $2, $3) RETURNING id", summoner.Name, summoner.Guild_id, summoner.Tagline).Scan(&summonerID)
	if err != nil {
		log.Printf("Error inserting summoner: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	invalidateCache()

	log.Printf("Successfully added summoner %s#%s with ID %d", summoner.Name, summoner.Tagline, summonerID)
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"status": "success"})
}

func get_summoners(w http.ResponseWriter, r *http.Request) {
	log.Printf("Received get_summoners request from %s", r.RemoteAddr)
	summoners := []Summoner{}
	rows, err := db.Query(context.Background(), "SELECT name, guildid, tagline FROM summoners")
	if err != nil {
		log.Printf("Error querying summoners: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}
	defer rows.Close()
	for rows.Next() {
		summoner := Summoner{}
		err = rows.Scan(&summoner.Name, &summoner.Guild_id, &summoner.Tagline)
		if err != nil {
			log.Printf("Error scanning summoner row: %v", err)
			continue
		}
		summoners = append(summoners, summoner)
	}
	log.Printf("Returning %d summoners", len(summoners))
	json.NewEncoder(w).Encode(summoners)
}

func update_summoner(w http.ResponseWriter, r *http.Request) {
	log.Printf("Received update_summoner request from %s", r.RemoteAddr)
	summoner := Summoner{}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		log.Printf("Error reading request body: %v", err)
		http.Error(w, "Invalid request body", http.StatusBadRequest)
		return
	}
	err = json.Unmarshal(body, &summoner)
	if err != nil {
		log.Printf("Error unmarshaling JSON: %v", err)
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}
	log.Printf("Updating summoner: %s#%s for guild %s", summoner.Name, summoner.Tagline, summoner.Guild_id)
	_, err = db.Exec(context.Background(), "UPDATE summoners SET name = $1, guildid = $2, tagline = $3 WHERE name = $5", summoner.Name, summoner.Guild_id, summoner.Tagline, summoner.Name)
	if err != nil {
		log.Printf("Error updating summoner: %v", err)
		http.Error(w, "Database error", http.StatusInternalServerError)
		return
	}

	invalidateCache()

	log.Printf("Successfully updated summoner %s#%s", summoner.Name, summoner.Tagline)
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "success"})
}
