import requests, json
import yaml
with open("/home/librechat/lichess-bot/config.yml") as f:
    BEARER_TOKEN = yaml.safe_load(f)["token"]


TEAM_ID = "daily-bot-tournaments"

def join_team_tournaments():
    arena_url = f"https://lichess.org/api/team/{TEAM_ID}/arena?status=created"
    resp = requests.get(arena_url)
    resp.raise_for_status()
    tournaments = [
        json.loads(line)
        for line in resp.text.splitlines()
        if line.strip()
    ]
    for tournament in tournaments:
        tournament_id = tournament["id"]
        join_url = f"https://lichess.org/api/tournament/{tournament_id}/join"

        join_resp = requests.post(join_url, headers={"Authorization": f"Bearer {BEARER_TOKEN}"})
        print(f"Joining {tournament_id}: {join_resp.status_code} * {join_resp.text}")

if __name__ == "__main__":
    join_team_tournaments()