import os
import json
import re
import requests
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
SLACK_TOKEN      = os.environ["SLACK_BOT_TOKEN"]
SOURCE_CHANNEL   = os.environ.get("SOURCE_CHANNEL", "C05MKV2869X")
DEST_CHANNEL     = os.environ.get("DEST_CHANNEL",   "C0ANSHX9LL8")
BASELINE_ENV_VAR = "BASELINE_JSON"
RAILWAY_TOKEN    = os.environ.get("RAILWAY_API_TOKEN", "")
RAILWAY_SERVICE  = os.environ.get("RAILWAY_SERVICE_ID", "")
RAILWAY_ENV      = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")

HEADERS = {
    "Authorization": f"Bearer {SLACK_TOKEN}",
    "Content-Type": "application/json",
}

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
            "disk_low":    data.get("disk_low", []),
        }
    except Exception:
        return {"unreachable": [], "disk_low": []}

def save_baseline(baseline):
    if not RAILWAY_TOKEN or not RAILWAY_SERVICE or not RAILWAY_ENV:
        print("BASELINE_JSON=" + json.dumps(baseline))
        return
    query = """
    mutation UpsertVariables($input: VariableCollectionUpsertInput!) {
      variableCollectionUpsert(input: $input)
    }
    """
    variables = {
        "input": {
            "projectId":     os.environ.get("RAILWAY_PROJECT_ID", ""),
            "environmentId": RAILWAY_ENV,
            "serviceId":     RAILWAY_SERVICE,
            "variables":     {BASELINE_ENV_VAR: json.dumps(baseline)}
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
        print(f"Railway variable update failed: {result['errors']}")
    else:
        print("Baseline saved to Railway Variables")

def parse_hosts(text):
    unreachable = []
    disk_low    = []
    for m in re.finditer(r"PU is unreachable\s*-\s*\*?([a-zA-Z0-9][\w-]*)\*?", text):
        unreachable.append(m.group(1))
    for m in re.finditer(r"PU root disk space is low\s*-\s*\*?([a-zA-Z0-9][\w-]*)\*?", text):
        host = re.sub(r"\.\s*<.*$", "", m.group(1)).rstrip(".")
        disk_low.append(host)
    return unreachable, disk_low

def fetch_today_alerts():
    now         = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp()
    unreachable = []
    disk_low    = []
    cursor      = None
    while True:
        params = {"channel": SOURCE_CHANNEL, "oldest": today_start, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = slack_get("conversations.history", params)
        for msg in data.get("messages", []):
            parts = [msg.get("text", "")]
            for att in msg.get("attachments", []):
                parts.append(att.get("text",    ""))
                parts.append(att.get("fallback",""))
                parts.append(att.get("pretext", ""))
            ur, dk = parse_hosts(" ".join(parts))
            unreachable.extend(ur)
            disk_low.extend(dk)
        next_cursor = data.get("response_metadata", {}).get("next_cursor", "")
        if not next_cursor:
            break
        cursor = next_cursor
    return {
        "unreachable": list(set(unreachable)),
        "disk_low":    list(set(disk_low)),
    }

def build_digest(today, history):
    new_ur  = [h for h in today["unreachable"]   if h not in history["unreachable"]]
    new_dk  = [h for h in today["disk_low"]      if h not in history["disk_low"]]
    res_ur  = [h for h in history["unreachable"] if h not in today["unreachable"]]
    res_dk  = [h for h in history["disk_low"]    if h not in today["disk_low"]]
    if not any([new_ur, new_dk, res_ur, res_dk]):
        return (
            f"📋 *Host Monitoring Digest* — No changes today.\n"
            f"_({len(history['unreachable'])} unreachable, {len(history['disk_low'])} low disk — all known)_"
        )
    lines = ["📋 *Host Monitoring Digest*\n"]
    if new_ur:
        lines.append(f"🆕 *Newly unreachable* ({len(new_ur)}):")
        lines += [f"• {h}" for h in sorted(new_ur)]
        lines.append("")
    if new_dk:
        lines.append(f"🆕 *Newly low disk* ({len(new_dk)}):")
        lines += [f"• {h}" for h in sorted(new_dk)]
        lines.append("")
    if res_ur:
        lines.append(f"✅ *Resolved — back online* ({len(res_ur)}):")
        lines += [f"• {h}" for h in sorted(res_ur)]
        lines.append("")
    if res_dk:
        lines.append(f"✅ *Resolved — disk normal* ({len(res_dk)}):")
        lines += [f"• {h}" for h in sorted(res_dk)]
    return "\n".join(lines)

def main():
    print(f"Agent started at {datetime.now(timezone.utc).isoformat()}")
    history = load_baseline()
    print(f"Baseline: {len(history['unreachable'])} unreachable, {len(history['disk_low'])} disk_low")
    today = fetch_today_alerts()
    print(f"Today: {len(today['unreachable'])} unreachable, {len(today['disk_low'])} disk_low")
    if not today["unreachable"] and not today["disk_low"]:
        slack_post(DEST_CHANNEL, "Monitoring Digest: 0 hosts found — check monitoring channel manually.")
        print("No hosts found. Baseline NOT updated.")
        return
    digest = build_digest(today, history)
    slack_post(DEST_CHANNEL, digest)
    print("Digest posted")
    save_baseline(today)
    print("Done")

if __name__ == "__main__":
    main()
