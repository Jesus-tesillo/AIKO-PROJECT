"""
twitch_events_webhook.py — Standalone Twitch EventSub listener.

Writes events (follower, sub, raid, bits) to events/stream_events.json
which the main VTuber system picks up and reacts to.

Usage:
    1. Set your CLIENT_ID, CLIENT_SECRET, and BROADCASTER_ID below
    2. Run: python twitch_events_webhook.py
    3. Keep running alongside main.py

Requires: pip install twitchAPI
"""

import json, os, asyncio
from datetime import datetime

EVENTS_FILE = "events/stream_events.json"

# ── Set your Twitch App credentials here ──────────────────────
CLIENT_ID = "YOUR_CLIENT_ID"           # From dev.twitch.tv
CLIENT_SECRET = "YOUR_CLIENT_SECRET"   # From dev.twitch.tv
BROADCASTER_ID = "YOUR_BROADCASTER_ID" # Your Twitch user ID


def write_event(event_type: str, user: str, **kwargs):
    """Append an event to the events JSON file."""
    os.makedirs(os.path.dirname(EVENTS_FILE), exist_ok=True)
    events = []
    try:
        if os.path.exists(EVENTS_FILE):
            with open(EVENTS_FILE, "r") as f:
                content = f.read().strip()
                if content:
                    events = json.loads(content)
    except Exception:
        events = []

    events.append({
        "type": event_type,
        "user": user,
        "timestamp": datetime.now().isoformat(),
        **kwargs
    })

    with open(EVENTS_FILE, "w") as f:
        json.dump(events, f, indent=2)
    print(f"[Webhook] Event: {event_type} from {user}")


async def run():
    """Set up Twitch EventSub with WebSocket transport."""
    try:
        from twitchAPI.twitch import Twitch
        from twitchAPI.eventsub.websocket import EventSubWebsocket
        from twitchAPI.type import AuthScope
        from twitchAPI.oauth import UserAuthenticator
    except ImportError:
        print("Install twitchAPI: pip install twitchAPI")
        return

    print("[Webhook] Starting Twitch EventSub listener...")

    # Authenticate
    twitch = await Twitch(CLIENT_ID, CLIENT_SECRET)
    target_scope = [AuthScope.CHANNEL_READ_SUBSCRIPTIONS,
                    AuthScope.MODERATOR_READ_FOLLOWERS,
                    AuthScope.BITS_READ]
    auth = UserAuthenticator(twitch, target_scope)
    token, refresh = await auth.authenticate()
    await twitch.set_user_authentication(token, target_scope, refresh)

    # Set up EventSub
    eventsub = EventSubWebsocket(twitch)
    eventsub.start()

    # Register event handlers
    async def on_follow(data):
        write_event("new_follower", data.event.user_name)

    async def on_subscribe(data):
        write_event("new_subscriber", data.event.user_name,
                     tier=str(data.event.tier))

    async def on_raid(data):
        write_event("raid", data.event.from_broadcaster_user_name,
                     raiders=data.event.viewers)

    async def on_cheer(data):
        write_event("bits_donation", data.event.user_name,
                     amount=data.event.bits)

    try:
        await eventsub.listen_channel_follow_v2(BROADCASTER_ID, BROADCASTER_ID, on_follow)
        await eventsub.listen_channel_subscribe(BROADCASTER_ID, on_subscribe)
        await eventsub.listen_channel_raid(on_raid, to_broadcaster_user_id=BROADCASTER_ID)
        await eventsub.listen_channel_cheer(BROADCASTER_ID, on_cheer)
        print("[Webhook] ✓ Listening for events. Press Ctrl+C to stop.")
    except Exception as e:
        print(f"[Webhook] Error registering events: {e}")

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await eventsub.stop()
        await twitch.close()


if __name__ == "__main__":
    asyncio.run(run())
