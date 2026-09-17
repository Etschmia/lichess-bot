# Voigtsbach Challenge-Crons

Siehe Martuni `BETRIEB-lichess-bot.md` Abschnitt 6 — hier nur **standard**
(Funken kann keine Varianten).

- Wrapper: `./run_challenge_cron.sh {blitz|rapid} [standard]`
- Token: `token.env` / `LICHESS_BOT_TOKEN` (nicht der Platzhalter in `config.yml`)
- Crontab User `box`, `CRON_TZ=Europe/Berlin`
- Logs: `lichess_bot_auto_logs/challenge_cron_{blitz,rapid}.log`
- 429-Cooldown: `challenge_cron_cooldown.json` (nicht anfassen)

Diese Box hat kein systemd; der `cron`-Daemon wurde manuell gestartet
(`service cron start`). Nach einem Image-/Box-Restart ggf. erneut starten
und `crontab -l` prüfen.
