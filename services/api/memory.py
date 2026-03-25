"""
Memory module: retrieve relevant memories from Chroma and manage biography in MongoDB.
"""

import logging
import os
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer
from pymongo import MongoClient
from pymongo.collection import Collection

logger = logging.getLogger(__name__)

_EMBEDDING_MODEL = "all-MiniLM-L6-v2"  # 384-dim, runs on CPU
_CHROMA_PATH = os.environ.get("CHROMA_PATH", "./data/chroma")
_COLLECTION_NAME = "afterlife-memories"

_embedding_model: Optional[SentenceTransformer] = None
_chroma_client: Optional[chromadb.PersistentClient] = None


def _get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer(_EMBEDDING_MODEL)
    return _embedding_model


def _get_embedding(text: str) -> list[float]:
    model = _get_embedding_model()
    return model.encode([text])[0].tolist()


def _get_chroma_collection() -> chromadb.Collection:
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=_CHROMA_PATH)
    return _chroma_client.get_or_create_collection(_COLLECTION_NAME)


def _get_contacts_collection() -> Collection:
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = MongoClient(uri)
    return client[db_name]["contacts"]


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
    from Chroma for the given contact.

    Returns a formatted string of relevant memories, or empty string if unavailable.
    """
    try:
        collection = _get_chroma_collection()
        embedding = _get_embedding(message)
        results = collection.query(
            query_embeddings=[embedding],
            n_results=top_k,
            where={"contact": contact_name},
            include=["documents"],
        )
        memories = []
        for doc in results.get("documents", [[]])[0]:
            if doc:
                memories.append(f"- {doc}")
        return "\n".join(memories)
    except Exception as exc:
        logger.warning("Could not retrieve memories from Chroma: %s", exc)
        return ""


def store_memory(contact_name: str, memory_text: str, memory_id: str) -> None:
    """
    Store a new episodic memory for the given contact in Chroma.
    Called after conversation sessions to persist notable exchanges.
    """
    try:
        collection = _get_chroma_collection()
        embedding = _get_embedding(memory_text)
        collection.add(
            ids=[memory_id],
            embeddings=[embedding],
            documents=[memory_text],
            metadatas=[{"contact": contact_name}],
        )
    except Exception as exc:
        logger.warning("Could not store memory in Chroma: %s", exc)


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
        logger.warning(
            "No contact document found for '%s' — biography not saved", contact_name
        )
