"""
After-Life Demo Mode
====================
Runs the full app with:
  - Local MongoDB (no credentials needed)
  - Stubbed Anthropic Claude (canned persona responses)
  - ElevenLabs skipped (text only, no audio)
  - Pinecone skipped (no memory retrieval)

Usage:
    pip install -r requirements.txt
    python demo.py

Then visit: http://localhost:8000/docs
Or test via curl — see bottom of this file.
"""

# ruff: noqa: E402
import os
import sys
import random
import unittest.mock
from unittest.mock import MagicMock

# ── Set env vars before importing the app ─────────────────────────────────────
os.environ["ANTHROPIC_API_KEY"] = "demo-key-not-real"
os.environ["ELEVENLABS_API_KEY"] = "demo-key-not-real"
os.environ["MONGODB_URI"] = "mongodb://localhost:27017"
os.environ["MONGODB_DB"] = "afterlife_demo"

# ── Seed demo contact in MongoDB ───────────────────────────────────────────────
from pymongo import MongoClient


def seed_demo_contact():
    client = MongoClient("mongodb://localhost:27017")
    db = client["afterlife_demo"]
    db.contacts.delete_many({"name": "mom"})
    db.contacts.insert_one({
        "name": "mom",
        "biography": (
            "Margaret was a warm, witty woman who loved gardening and cooking. "
            "She always had a pot of chai on the stove and greeted everyone with "
            "a hug. She called her son Gagan 'janu' and always asked if he'd eaten. "
            "She passed away in 2023 after a brief illness, leaving behind a family "
            "who misses her every day."
        ),
        "personality_profile": (
            "Warm, nurturing, slightly worrying. Uses Punjabi endearments. "
            "Always brings conversations back to food, family, and health. "
            "Laughs easily. Never complains about herself."
        ),
        "common_phrases": "Janu, have you eaten? | Waheguru meherbaan | Come home soon | I made your favourite",
        "voice_id": "",  # No voice in demo mode
    })
    print("✓ Seeded demo contact: 'mom'")
    client.close()


# ── Stub responses for the persona ────────────────────────────────────────────
DEMO_RESPONSES = [
    "Janu! So good to hear from you. Have you eaten today? You always forget to eat when you're busy.",
    "Waheguru meherbaan. I was just thinking about you. How is everything going, my love?",
    "You know, I made your favourite aloo parathas this morning. Wish you were here to have some.",
    "Don't work too hard, janu. Your health is more important than anything. Are you sleeping enough?",
    "I miss you so much. Come home soon, okay? The house feels empty without you.",
    "Whatever happens, I am always proud of you. You know that, right?",
    "Janu, just remember — I am always with you. Always. Don't ever forget that.",
]


def make_stub_claude():
    """Return a mock Anthropic client that gives persona-appropriate responses."""
    mock_client = MagicMock()

    def side_effect(*args, **kwargs):
        r = MagicMock()
        r.content = [MagicMock(text=random.choice(DEMO_RESPONSES))]
        return r

    mock_client.messages.create.side_effect = side_effect
    return mock_client


# ── Patch Anthropic and Pinecone before app loads ─────────────────────────────
import anthropic

anthropic.Anthropic = lambda **kwargs: make_stub_claude()

# Patch Pinecone import so memory retrieval silently returns empty (no crash)
sys.modules["pinecone"] = unittest.mock.MagicMock()

# ── Seed and start ─────────────────────────────────────────────────────────────
seed_demo_contact()

print("\n" + "=" * 60)
print("  After-Life DEMO MODE")
print("=" * 60)
print("  Contact seeded: 'mom'")
print("  Claude:         stubbed (canned responses)")
print("  ElevenLabs:     skipped (text only)")
print("  Pinecone:       skipped")
print("  MongoDB:        localhost:27017/afterlife_demo")
print("=" * 60)
print("\n  API docs:  http://localhost:8000/docs")
print("  Health:    http://localhost:8000/health")
print("\n  Quick test:")
print("  curl -s -X POST http://localhost:8000/conversation/start \\")
print('    -H "Content-Type: application/json" \\')
print('    -d \'{"contact_name":"mom","user_name":"Gagan"}\' | python3 -m json.tool')
print("\n" + "=" * 60 + "\n")

import uvicorn
from services.api.main import app

uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
