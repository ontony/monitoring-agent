import os
import json
import re
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
SLACK_TOKEN       = os.environ["SLACK_BOT_TOKEN"]
SOURCE_CHANNEL    = os.environ.get("SOURCE_CHANNEL", "C05MKV2869X")   # monitoring alerts
DEST_CHANNEL      = os.environ.get("DEST_CHANNEL",   "C0ANSHX9LL8")   # digest destination
BASELINE_ENV_VAR  = "BASELINE_JSON"   # Railway variable name where baseline is stored
RAILWAY_TOKEN     = os.environ.get("RAILWAY_API_TOKEN", "")
RAILWAY_SERVICE   = os.environ.get("RAILWAY_SERVICE_ID", "")
RAILWAY_ENV       = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")

HEADERS = {
    "Authorization": f"Bearer {SLACK_TOKEN}",
    "Content-Type": "application/json",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def slack_get(method, params):
    r = requests.get(f"https://slack.com/api/{method}", headers=HEADERS, params=params)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack API error ({method}): {data.get('error')}")
    return data


def slack_post(channel, text):
    r = requests.post("https://slack.com/api/chat.postMessage", headers=HEADERS,
                      json={"channel": channel, "text": text})
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack post error: {data.get('error')}")


def load_baseline():
    raw = os.environ.get(BASELINE_ENV_VAR, "")
    if not raw:
        return {"unreachable": [], "disk_low": []}
    try:
        data = json.loads(raw)
        return {
            "unreachable": data.get("unreachable", []),
            "disk_low": data.get("disk_low", []),
        }
    except Exception:
        return {"unreachable": [], "disk_low": []}


def save_baseline(baseline: dict):
    """Update Railway variable via GraphQL API so next run picks up new baseline."""
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE or not RAILWAY_ENV:
        # Fallback: just print — user can copy-paste into Railway dashboard
        print("⚠️  Railway API credentials not set. New baseline (copy to Railway Variables):")
        print(f"BASELINE_JSON={json.dumps(baseline)}")
        return

    query = """
    mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "projectId": os.environ.get("RAILWAY_PROJECT_ID", ""),
            "environmentId": RAILWAY_ENV,
            "serviceId": RAILWAY_SERVICE,
            "variables": {
                BASELINE_ENV_VAR: json.dumps(baseline)
            }
        }
    }
    r = requests.post(
        "https://backboard.railway.app/graphql/v2",
        headers={"Authorization": f"Bearer {RAILWAY_TOKEN}", "Content-Type": "application/json"},
        json={"query": query, "variables": variables}
    )
    r.raise_for_status()
    result = r.json()
    if "errors" in result:
        print(f"⚠️  Railway variable update failed: {result['errors']}")
    else:
        print("✅ Baseline saved to Railway Variables")


# ── Core logic ────────────────────────────────────────────────────────────────

def fetch_today_alerts():
    """Fetch today's messages from the monitoring channel and parse host names."""
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()

    unreachable = []
    disk_low    = []
    cursor      = None

    while True:
        params = {
            "channel": SOURCE_CHANNEL,
            "oldest":  today_start,
            "limit":   200,
        }
        if cursor:
            params["cursor"] = cursor

        data     = slack_get("conversations.history", params)
        messages = data.get("messages", [])

        for msg in messages:
            # Alerts come as bot messages with attachments
            for att in msg.get("attachments", []):
                text = att.get("text", "") + "\n" + att.get("fallback", "")
                for line in text.split("\n"):
                    ur = re.search(r"PU is unreachable\s*[-–—*]\s*([a-zA-Z0-9][\w\-]*)", line)
                    if ur:
                        unreachable.append(ur.group(1))
                    dk = re.search(r"PU root disk space is low\s*[-–—*]\s*([a-zA-Z0-9][\w\-]*)", line)
                    if dk:
                        host = dk.group(1)
                        host = re.sub(r"\.\s*<.*$", "", host).rstrip(".")
                        disk_low.append(host)

            # Also check plain text messages
            text = msg.get("text", "")
            for line in text.split("\n"):
                ur = re.search(r"PU is unreachable\s*[-–—*]\s*([a-zA-Z0-9][\w\-]*)", line)
                if ur:
                    unreachable.append(ur.group(1))
                dk = re.search(r"PU root disk space is low\s*[-–—*]\s*([a-zA-Z0-9][\w\-]*)", line)
                if dk:
                    host = dk.group(1)
                    host = re.sub(r"\.\s*<.*$", "", host).rstrip(".")
                    disk_low.append(host)

        next_cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not next_cursor:
            break
        cursor = next_cursor

    return {
        "unreachable": list(set(unreachable)),
        "disk_low":    list(set(disk_low)),
    }


def build_digest(today, history):
    new_unreachable      = [h for h in today["unreachable"] if h not in history["unreachable"]]
    new_disk_low         = [h for h in today["disk_low"]    if h not in history["disk_low"]]
    resolved_unreachable = [h for h in history["unreachable"] if h not in today["unreachable"]]
    resolved_disk_low    = [h for h in history["disk_low"]    if h not in today["disk_low"]]

    if not any([new_unreachable, new_disk_low, resolved_unreachable, resolved_disk_low]):
        return (
            f"📋 *Host Monitoring Digest* — No changes today.\n"
            f"All previously known hosts remain in the same state.\n"
            f"_({len(history['unreachable'])} unreachable, {len(history['disk_low'])} low disk — all known)_"
        )

    lines = ["📋 *Host Monitoring Digest*\n"]
    if new_unreachable:
        lines.append(f"🆕 *Newly unreachable* ({len(new_unreachable)}):")
        lines += [f"• {h}" for h in sorted(new_unreachable)]
        lines.append("")
    if new_disk_low:
        lines.append(f"🆕 *Newly low disk* ({len(new_disk_low)}):")
        lines += [f"• {h}" for h in sorted(new_disk_low)]
        lines.append("")
    if resolved_unreachable:
        lines.append(f"✅ *Resolved — back online* ({len(resolved_unreachable)}):")
        lines += [f"• {h}" for h in sorted(resolved_unreachable)]
        lines.append("")
    if resolved_disk_low:
        lines.append(f"✅ *Resolved — disk normal* ({len(resolved_disk_low)}):")
        lines += [f"• {h}" for h in sorted(resolved_disk_low)]

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"🚀 Agent started at {datetime.now(timezone.utc).isoformat()}")

    history = load_baseline()
    print(f"📂 Baseline loaded: {len(history['unreachable'])} unreachable, {len(history['disk_low'])} disk_low")

    today = fetch_today_alerts()
    print(f"📡 Today's alerts: {len(today['unreachable'])} unreachable, {len(today['disk_low'])} disk_low")

    if not today["unreachable"] and not today["disk_low"]:
        slack_post(DEST_CHANNEL, "⚠️ Monitoring Digest: extracted 0 hosts today — please check the monitoring channel manually.")
        print("⚠️ No hosts found, posted warning. Baseline NOT updated.")
        return

    digest = build_digest(today, history)
    slack_post(DEST_CHANNEL, digest)
    print("✅ Digest posted to Slack")

    save_baseline(today)
    print("✅ Done")


if __name__ == "__main__":
    main()
