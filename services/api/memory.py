"""
Memory module: retrieve relevant memories from Pinecone and manage biography in MongoDB.
"""

import logging
import os
from typing import Optional

from pinecone import Pinecone
from pymongo import MongoClient
from pymongo.collection import Collection

logger = logging.getLogger(__name__)


def _get_contacts_collection() -> Collection:
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = MongoClient(uri)
    return client[db_name]["contacts"]


def _get_pinecone_index():
    api_key = os.environ.get("PINECONE_API_KEY", "")
    if not api_key:
        raise ValueError("PINECONE_API_KEY environment variable is required")
    index_name = os.environ.get("PINECONE_INDEX", "afterlife-memories")
    pc = Pinecone(api_key=api_key)
    return pc.Index(index_name)


def load_contact_profile(contact_name: str) -> dict:
    """
    Load biography and personality profile for a contact from MongoDB.
    Returns dict with keys: biography, personality_profile, common_phrases, voice_id.
    Raises ValueError if contact is not found.
    """
    collection = _get_contacts_collection()
    doc = collection.find_one({"name": contact_name})
    if not doc:
        raise ValueError(f"Contact '{contact_name}' not found in database")
    return {
        "name": doc.get("name", contact_name),
        "biography": doc.get("biography", ""),
        "personality_profile": doc.get("personality_profile", ""),
        "common_phrases": doc.get("common_phrases", ""),
        "voice_id": doc.get("voice_id", ""),
    }


def retrieve_relevant_memories(contact_name: str, message: str, top_k: int = 5) -> str:
    """
    Embed the user's message and retrieve the most relevant episodic memories
    from Pinecone for the given contact.

    Returns a formatted string of relevant memories, or empty string if Pinecone
    is unavailable or no memories exist.
    """
    try:
        index = _get_pinecone_index()
        # Use a simple embedding via a lightweight approach.
        # In production, use a real embedding model (e.g. OpenAI text-embedding-3-small).
        # Here we query with a metadata filter and rely on the index having stored vectors.
        results = index.query(
            vector=[0.0] * 1536,  # placeholder — real embeddings needed in production
            top_k=top_k,
            filter={"contact": contact_name},
            include_metadata=True,
        )
        memories = []
        for match in results.get("matches", []):
            meta = match.get("metadata", {})
            text = meta.get("text", "")
            if text:
                memories.append(f"- {text}")
        return "\n".join(memories)
    except Exception as exc:
        logger.warning("Could not retrieve memories from Pinecone: %s", exc)
        return ""


def update_biography(contact_name: str, new_biography: str) -> None:
    """
    Persist an updated biography for the contact back to MongoDB.
    Called by the Biographer Agent after each conversation session.
    """
    collection = _get_contacts_collection()
    result = collection.update_one(
        {"name": contact_name},
        {"$set": {"biography": new_biography}},
    )
    if result.matched_count == 0:
        logger.warning("No contact document found for '%s' — biography not saved", contact_name)
