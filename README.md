# Slack Monitoring Digest Agent

Runs daily on Railway. Reads Grafana alerts from a Slack monitoring channel,
deduplicates against the previous run, and posts a clean digest.

## Environment Variables (set in Railway)

| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Bot token `xoxb-...` |
| `SOURCE_CHANNEL` | Channel ID to read alerts from (default: C05MKV2869X) |
| `DEST_CHANNEL` | Channel ID to post digest to (default: C0ANSHX9LL8) |
| `BASELINE_JSON` | Auto-updated after each run (start with `{}`) |
| `RAILWAY_API_TOKEN` | Railway token for auto-updating baseline |
| `RAILWAY_SERVICE_ID` | Railway service ID |
| `RAILWAY_ENVIRONMENT_ID` | Railway environment ID |
| `RAILWAY_PROJECT_ID` | Railway project ID |

## Schedule

Runs every day at 08:00 UTC (`0 8 * * *`).
Edit `railway.toml` to change the schedule.
