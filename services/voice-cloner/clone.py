"""
Voice-cloner service: clone voices from WhatsApp voice notes via ElevenLabs.

Usage (CLI):
    python clone.py --contact "mom" --voice-notes /path/to/voice_notes/

Usage (library):
    from clone import clone_voice_for_contact

Environment variables required:
    ELEVENLABS_API_KEY   - ElevenLabs API key
    MONGODB_URI          - MongoDB connection string (default: mongodb://localhost:27017)
    MONGODB_DB           - Database name (default: afterlife)
"""

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import requests
from pymongo import MongoClient
from pymongo.collection import Collection

from audio_utils import convert_to_wav, filter_quality_voice_notes

logger = logging.getLogger(__name__)

# Minimum total clean audio needed to attempt a real clone (30 seconds per spec)
MIN_CLONE_DURATION_SECONDS = 30.0

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"

# Generic fallback voice used when there is not enough audio
GENERIC_FALLBACK_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel" voice


def get_mongo_collection() -> Collection:
    uri = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.environ.get("MONGODB_DB", "afterlife")
    client = MongoClient(uri)
    return client[db_name]["contacts"]


def get_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise EnvironmentError("ELEVENLABS_API_KEY environment variable not set")
    return key


# ---------------------------------------------------------------------------
# ElevenLabs helpers
# ---------------------------------------------------------------------------

def elevenlabs_add_voice(
    api_key: str,
    name: str,
    wav_paths: list[str],
    description: str = "",
) -> str:
    """
    Upload audio samples to ElevenLabs and create a new cloned voice.

    Returns the new voice_id.
    """
    url = f"{ELEVENLABS_API_BASE}/voices/add"
    headers = {"xi-api-key": api_key}

    files = [("files", (Path(p).name, open(p, "rb"), "audio/wav")) for p in wav_paths]
    data = {"name": name, "description": description}

    try:
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=120)
        resp.raise_for_status()
        voice_id = resp.json()["voice_id"]
        logger.info("Created ElevenLabs voice '%s' → voice_id=%s", name, voice_id)
        return voice_id
    finally:
        for _, (_, fh, _) in files:
            fh.close()


def elevenlabs_list_voices(api_key: str) -> list[dict]:
    """Return all voices in the ElevenLabs account."""
    url = f"{ELEVENLABS_API_BASE}/voices"
    resp = requests.get(url, headers={"xi-api-key": api_key}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("voices", [])


def elevenlabs_find_voice_by_name(api_key: str, name: str) -> Optional[str]:
    """Return voice_id if a voice with the given name already exists."""
    for voice in elevenlabs_list_voices(api_key):
        if voice.get("name", "").lower() == name.lower():
            return voice["voice_id"]
    return None


def elevenlabs_delete_voice(api_key: str, voice_id: str) -> None:
    url = f"{ELEVENLABS_API_BASE}/voices/{voice_id}"
    resp = requests.delete(url, headers={"xi-api-key": api_key}, timeout=30)
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def store_voice_id(collection: Collection, contact_name: str, voice_id: str, is_fallback: bool) -> None:
    """Upsert voice_id for a contact in MongoDB."""
    collection.update_one(
        {"contact_name": contact_name},
        {
            "$set": {
                "contact_name": contact_name,
                "voice_id": voice_id,
                "voice_is_fallback": is_fallback,
            }
        },
        upsert=True,
    )
    logger.info(
        "Stored voice_id=%s for contact='%s' (fallback=%s)",
        voice_id,
        contact_name,
        is_fallback,
    )


def get_stored_voice_id(collection: Collection, contact_name: str) -> Optional[dict]:
    """Return stored voice record for a contact, or None."""
    return collection.find_one({"contact_name": contact_name}, {"_id": 0})


def clone_voice_for_contact(
    contact_name: str,
    voice_note_paths: list[str],
    *,
    api_key: Optional[str] = None,
    mongo_collection: Optional[Collection] = None,
    force_reclone: bool = False,
) -> dict:
    """
    High-level entry point: clone a voice for a contact from their voice notes.

    Returns a result dict:
        {
            "contact_name": str,
            "voice_id": str,
            "is_fallback": bool,
            "accepted_clips": int,
            "accepted_duration_seconds": float,
            "message": str,
        }
    """
    if api_key is None:
        api_key = get_api_key()
    if mongo_collection is None:
        mongo_collection = get_mongo_collection()

    # Check if we already have a non-fallback voice and reclone was not requested
    if not force_reclone:
        existing = get_stored_voice_id(mongo_collection, contact_name)
        if existing and not existing.get("voice_is_fallback", True):
            logger.info("Using existing voice_id for '%s'", contact_name)
            return {
                "contact_name": contact_name,
                "voice_id": existing["voice_id"],
                "is_fallback": False,
                "accepted_clips": 0,
                "accepted_duration_seconds": 0.0,
                "message": "reused existing clone",
            }

    # Filter voice notes for quality
    accepted_paths, total_duration = filter_quality_voice_notes(voice_note_paths)

    if total_duration < MIN_CLONE_DURATION_SECONDS or not accepted_paths:
        logger.warning(
            "Insufficient audio for '%s': %.1fs (need %.1fs). Using fallback voice.",
            contact_name,
            total_duration,
            MIN_CLONE_DURATION_SECONDS,
        )
        store_voice_id(mongo_collection, contact_name, GENERIC_FALLBACK_VOICE_ID, is_fallback=True)
        return {
            "contact_name": contact_name,
            "voice_id": GENERIC_FALLBACK_VOICE_ID,
            "is_fallback": True,
            "accepted_clips": len(accepted_paths),
            "accepted_duration_seconds": total_duration,
            "message": f"fallback: only {total_duration:.1f}s of clean audio (need {MIN_CLONE_DURATION_SECONDS}s)",
        }

    # Convert accepted files to WAV
    wav_paths = []
    tmp_files = []
    try:
        for src in accepted_paths:
            wav_path = convert_to_wav(src)
            if wav_path:
                wav_paths.append(wav_path)
                tmp_files.append(wav_path)
            else:
                logger.warning("Could not convert %s to WAV, skipping", src)

        if not wav_paths:
            store_voice_id(mongo_collection, contact_name, GENERIC_FALLBACK_VOICE_ID, is_fallback=True)
            return {
                "contact_name": contact_name,
                "voice_id": GENERIC_FALLBACK_VOICE_ID,
                "is_fallback": True,
                "accepted_clips": 0,
                "accepted_duration_seconds": 0.0,
                "message": "fallback: all conversions failed",
            }

        # If a voice with this name already exists on ElevenLabs, delete it first
        existing_id = elevenlabs_find_voice_by_name(api_key, contact_name)
        if existing_id and force_reclone:
            logger.info("Deleting existing ElevenLabs voice for '%s'", contact_name)
            elevenlabs_delete_voice(api_key, existing_id)

        voice_id = elevenlabs_add_voice(
            api_key,
            name=contact_name,
            wav_paths=wav_paths,
            description=f"Voice clone for After-Life contact: {contact_name}",
        )

        store_voice_id(mongo_collection, contact_name, voice_id, is_fallback=False)

        return {
            "contact_name": contact_name,
            "voice_id": voice_id,
            "is_fallback": False,
            "accepted_clips": len(wav_paths),
            "accepted_duration_seconds": total_duration,
            "message": "cloned successfully",
        }
    finally:
        # Clean up temp WAV files
        for tmp in tmp_files:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def clone_all_contacts(
    contacts_dir: str,
    *,
    api_key: Optional[str] = None,
    mongo_collection: Optional[Collection] = None,
    force_reclone: bool = False,
) -> list[dict]:
    """
    Process all contacts found under contacts_dir.

    Expected layout:
        contacts_dir/
            <contact_name>/
                voice_notes/
                    *.ogg / *.opus / *.wav / *.mp3

    Returns list of result dicts from clone_voice_for_contact.
    """
    contacts_path = Path(contacts_dir)
    if not contacts_path.is_dir():
        raise ValueError(f"Not a directory: {contacts_dir}")

    if api_key is None:
        api_key = get_api_key()
    if mongo_collection is None:
        mongo_collection = get_mongo_collection()

    results = []
    for contact_dir in sorted(contacts_path.iterdir()):
        if not contact_dir.is_dir():
            continue
        contact_name = contact_dir.name
        voice_notes_dir = contact_dir / "voice_notes"
        if not voice_notes_dir.is_dir():
            logger.info("No voice_notes/ dir for contact '%s', skipping", contact_name)
            continue

        audio_extensions = {".ogg", ".opus", ".wav", ".mp3", ".m4a", ".aac"}
        voice_note_paths = [
            str(p)
            for p in voice_notes_dir.iterdir()
            if p.suffix.lower() in audio_extensions
        ]

        if not voice_note_paths:
            logger.info("No audio files for contact '%s'", contact_name)
            continue

        logger.info("Processing contact '%s' with %d audio files", contact_name, len(voice_note_paths))
        result = clone_voice_for_contact(
            contact_name,
            voice_note_paths,
            api_key=api_key,
            mongo_collection=mongo_collection,
            force_reclone=force_reclone,
        )
        results.append(result)
        logger.info("Result: %s", result)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Clone voices from WhatsApp voice notes via ElevenLabs."
    )
    sub = p.add_subparsers(dest="command", required=True)

    # clone single contact
    single = sub.add_parser("contact", help="Clone voice for a single contact")
    single.add_argument("--contact", required=True, help="Contact name (used as ElevenLabs voice name)")
    single.add_argument("--voice-notes", required=True, nargs="+", help="Paths to audio files")
    single.add_argument("--force", action="store_true", help="Re-clone even if voice already exists")

    # clone all contacts
    all_cmd = sub.add_parser("all", help="Clone voices for all contacts in a directory")
    all_cmd.add_argument("--contacts-dir", required=True, help="Root directory containing contact subdirs")
    all_cmd.add_argument("--force", action="store_true", help="Re-clone all contacts")

    # show stored voice
    show = sub.add_parser("show", help="Show stored voice_id for a contact")
    show.add_argument("--contact", required=True)

    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _build_parser().parse_args()

    if args.command == "contact":
        result = clone_voice_for_contact(
            args.contact,
            args.voice_notes,
            force_reclone=args.force,
        )
        print(result)

    elif args.command == "all":
        results = clone_all_contacts(args.contacts_dir, force_reclone=args.force)
        for r in results:
            print(r)

    elif args.command == "show":
        col = get_mongo_collection()
        record = get_stored_voice_id(col, args.contact)
        if record:
            print(record)
        else:
            print(f"No voice stored for contact '{args.contact}'")
            sys.exit(1)


if __name__ == "__main__":
    main()
