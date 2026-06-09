"""Register (or delete) the Telegram webhook for the deployed bot.

Run locally after deploying to Vercel:

    BOT_TOKEN=xxxx python set_webhook.py https://your-project.vercel.app

Optionally pass a secret (must match WEBHOOK_SECRET on Vercel):

    BOT_TOKEN=xxxx WEBHOOK_SECRET=mysecret \\
        python set_webhook.py https://your-project.vercel.app

To remove the webhook (e.g. to switch back to polling):

    BOT_TOKEN=xxxx python set_webhook.py --delete
"""
import json
import os
import sys
import urllib.parse
import urllib.request


def call(token: str, method: str, params: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=data)) as r:
        return json.loads(r.read())


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN env var is required.")
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)

    if sys.argv[1] == "--delete":
        print(call(token, "deleteWebhook", {"drop_pending_updates": "true"}))
        return

    base = sys.argv[1].rstrip("/")
    webhook_url = f"{base}/api/webhook"
    params = {"url": webhook_url, "drop_pending_updates": "true"}
    secret = os.environ.get("WEBHOOK_SECRET")
    if secret:
        params["secret_token"] = secret
    print("Setting webhook to:", webhook_url)
    print(call(token, "setWebhook", params))
    print(call(token, "getWebhookInfo", {}))


if __name__ == "__main__":
    main()
