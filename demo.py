"""
After-Life Demo Mode
====================
Runs the full app with:
  - Local MongoDB (no credentials needed)
  - Local Ollama LLM (llama3.2:3b)
  - Local Chroma vector DB (no memory retrieval in demo)
  - TTS skipped (text only, no audio)

Usage:
    pip install -r requirements.txt
    ollama pull llama3.2:3b
    python demo.py

Then visit: http://localhost:8000/docs
Or test via curl — see bottom of this file.
"""

# ruff: noqa: E402
import os

# ── Set env vars before importing the app ─────────────────────────────────────
os.environ["MONGODB_URI"] = "mongodb://localhost:27017"
os.environ["MONGODB_DB"] = "afterlife_demo"
os.environ["OLLAMA_HOST"] = "http://localhost:11434"
os.environ["OLLAMA_MODEL"] = "llama3.2:3b"
os.environ["CHROMA_PATH"] = "/tmp/afterlife_chroma_demo"

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


# ── Seed and start ─────────────────────────────────────────────────────────────
seed_demo_contact()

print("\n" + "=" * 60)
print("  After-Life DEMO MODE")
print("=" * 60)
print("  Contact seeded: 'mom'")
print("  LLM:            Ollama llama3.2:3b (local)")
print("  TTS:            skipped (text only)")
print("  Memory:         Chroma (local, /tmp/afterlife_chroma_demo)")
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
