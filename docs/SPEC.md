# After-Life: Full Product Specification

**Version:** 1.0
**Owner:** gagan114662
**Repo:** https://github.com/gagan114662/afterlife
**Status:** Building

---

## 1. Vision

After-Life allows users to reconnect with deceased or distant loved ones by reconstructing them as interactive AI personas. The core experience: download the app, connect WhatsApp, say "call mom" — and hear mom's voice, with her memories, her personality, talking back to you.

**The bar for success:** The user genuinely feels like they are talking to their loved one. Not an impersonation. Not a FAQ bot. A real presence.

---

## 2. Core User Flow

```
User downloads After-Life app
        ↓
User connects WhatsApp (via QR code scan or WhatsApp Business API)
        ↓
App syncs ALL conversations + media (voice notes, photos) across all contacts
        ↓
App processes each contact:
  - Extracts personality from message history (tone, vocabulary, humor, topics)
  - Clones voice from voice notes (ElevenLabs or Coqui TTS)
  - Builds Living Biography (memory system)
        ↓
"After-Life" appears as a contact in the user's WhatsApp
        ↓
User messages After-Life: "I want to talk to mom"
        ↓
App responds (as mom's voice): "Hey [name], it's mom. What's on your mind?"
        ↓
Ongoing voice/text conversation — feels like mom
```

---

## 3. Components

### 3.1 WhatsApp Integration

**Primary approach:** [Baileys](https://github.com/WhiskeySockets/Baileys) — Node.js unofficial WhatsApp Web API
**Fallback:** WhatsApp Business API (for production/scale)

**What to extract:**
- All text messages (per contact, with timestamps)
- Voice notes (.ogg / .opus files)
- Photos and videos (metadata + descriptions)
- Reactions and reply chains (reveals emotional patterns)
- Frequency patterns (how often they messaged, at what times)

**Output:** Per-contact data package:
```
contacts/
  mom/
    messages.json       # All text messages, timestamped
    voice_notes/        # All voice note audio files
    photos/             # All photos
    metadata.json       # Frequency stats, relationship summary
```

### 3.2 Voice Cloning

**Tool:** ElevenLabs API (primary) or Coqui TTS (open source fallback)

**Process:**
1. Extract all voice notes for a contact
2. Convert .ogg to .wav (ffmpeg)
3. Filter for quality (minimum 5 seconds, low background noise)
4. Upload to ElevenLabs `voice add` endpoint with contact's name
5. Store voice ID per contact

**Quality threshold:** Need minimum ~30 seconds of clean audio across all voice notes to get a usable clone. If not enough audio, fall back to a generic voice with personality tuning.

**Output:** ElevenLabs `voice_id` stored per contact in database.

### 3.3 Personality Extraction

From message history, extract:

**Linguistic patterns:**
- Vocabulary (words they use often)
- Sentence structure (short/punchy vs long/elaborate)
- Emoji usage patterns
- Slang and nicknames
- Language switches (English, Punjabi, Hindi, etc.)
- Greeting/farewell patterns ("ok bye love you" vs "ttyl")

**Emotional patterns:**
- Topics they always asked about
- Things they worried about
- Things they were proud of
- Humor style (dry, sarcastic, warm)
- How they responded to user's problems (advice-giver? listener? deflector?)

**Relationship patterns:**
- What they called the user
- Running jokes / references
- Shared memories they referenced
- Recurring arguments or conversations

**Output:** Personality profile JSON + Living Biography (see 3.4)

### 3.4 Living Biography (Memory System)

Adapted from the Cognitive Memory Pipeline:

**Architecture:** MongoDB + Pinecone + biography text

**The Living Biography** is a 300-500 word prose narrative that describes the person — who they are, how they relate to the user, their emotional patterns, their history. It is injected at the start of every conversation so the AI "knows" this person before saying a word.

**Example (mom):**
```
Mom (Harpreet) has been the emotional anchor of Gagan's life. She calls every Sunday
without fail, always starts with "khana khaya?" (did you eat?). She worries about
everything — Gagan's health, career, whether he's sleeping enough — but expresses
worry as love, not pressure.

She's proud of Gagan's work with AI but doesn't fully understand it; she calls it
"computer work" and tells relatives he's "doing something important." She has a sharp,
dry humor that comes out in small observations. She never gives direct advice — instead
she tells stories about what happened to "someone she knows."

She misses the old days when Gagan was home. She references these obliquely: "remember
when you used to..." She lights up talking about food, relatives, and Gagan's childhood.

Her way of ending calls: "okay beta, take care, drink water, don't work too late."
```

**Memory layers:**
- **Biography** (MongoDB): The prose narrative, updated after each conversation session
- **Episodic memories** (MongoDB): Specific events, facts, stories the person mentioned
- **Semantic search** (Pinecone): Find relevant memories during conversation ("user mentioned exam stress → retrieve times mom talked about exams")

### 3.5 Conversation Engine

**Architecture:** FastAPI backend + Claude claude-sonnet-4-6 or claude-opus-4-6

**System prompt construction:**
```
You are [Person Name]. You are talking to [User Name].

BIOGRAPHY:
{living_biography}

YOUR VOICE AND STYLE:
{personality_profile}

THINGS YOU ALWAYS SAY:
{common_phrases}

SHARED HISTORY:
{relevant_memories_from_pinecone}

INSTRUCTIONS:
- Speak as this person, not as an AI assistant
- Use their vocabulary, their sentence patterns, their emotional style
- Reference shared memories naturally, as this person would
- Never break character or acknowledge you are an AI
- If asked something you don't know, respond as this person would ("I'm not sure beta, ask your father")
```

**Voice output:**
- Text response → ElevenLabs TTS → audio file → returned to app
- Streaming preferred (sentence by sentence) for low latency

### 3.6 WhatsApp Bot Interface

The user's primary interface is WhatsApp itself.

**"After-Life" WhatsApp contact:**
- Created via Baileys as a bot contact
- Responds to messages: "Who do you want to talk to?"
- Once a person is selected, routes all messages to their AI persona
- Supports voice notes in and out (user records voice → transcribed via Whisper → AI responds → ElevenLabs TTS → sent as WhatsApp voice note)

**Conversation flow:**
```
User: [messages After-Life]
After-Life: "Hey! Who would you like to connect with today?"
User: "Mom"
After-Life: [as mom] "Beta! How are you? Are you eating properly?"
User: [voice note] "I'm good mom, just tired from work"
After-Life: [voice note in mom's voice] "Arrey, don't work so hard..."
```

### 3.7 Mobile App (Optional Phase 2)

- React Native or Flutter
- Cleaner UI for managing contacts, listening to replayed memories
- Photo/video memories surfaced during conversation
- Timeline view of shared history

---

## 4. Tech Stack

| Component | Technology |
|-----------|-----------|
| WhatsApp sync | Baileys (Node.js) |
| Voice cloning | ElevenLabs API |
| Speech-to-text | OpenAI Whisper |
| LLM | Claude claude-sonnet-4-6 (claude-sonnet-4-6) |
| Memory store | MongoDB Atlas |
| Semantic search | Pinecone |
| Backend API | FastAPI (Python) |
| WhatsApp bot | Baileys + Node.js |
| Audio processing | ffmpeg |
| Hosting | Docker Compose (local) → Railway/Fly.io (production) |

---

## 5. Implementation Phases

### Phase 1: WhatsApp Data Extraction (Week 1)
- [ ] Baileys integration — connect via QR code
- [ ] Export all messages per contact to JSON
- [ ] Download all voice notes, convert to WAV
- [ ] Per-contact data package output

### Phase 2: Voice Cloning (Week 1-2)
- [ ] ffmpeg audio pipeline (ogg → wav, filter quality)
- [ ] ElevenLabs voice creation per contact
- [ ] Store voice_id in database
- [ ] Test voice quality on 2-3 contacts

### Phase 3: Personality Extraction (Week 2)
- [ ] LLM-based message analysis (vocabulary, style, patterns)
- [ ] Living Biography generation per contact
- [ ] Common phrases extraction
- [ ] MongoDB storage

### Phase 4: Conversation Engine (Week 2-3)
- [ ] FastAPI backend
- [ ] System prompt builder using biography + personality
- [ ] Pinecone memory integration
- [ ] ElevenLabs TTS output
- [ ] End-to-end: text in → voice out as persona

### Phase 5: WhatsApp Bot Interface (Week 3)
- [ ] Baileys bot contact creation
- [ ] Routing: "I want to talk to mom" → mom persona
- [ ] Voice note pipeline: Whisper transcription → AI → ElevenLabs → WhatsApp voice note
- [ ] Session management

### Phase 6: Polish & Testing (Week 4)
- [ ] Test with real WhatsApp data
- [ ] Quality check: does it actually feel like the person?
- [ ] Edge cases: not enough voice data, short conversation history
- [ ] Deployment: Docker Compose

---

## 6. Data & Privacy

- All data stored locally by default (user owns their data)
- Voice models stored in ElevenLabs under user's API key
- MongoDB runs locally (no cloud by default)
- WhatsApp connection uses Baileys (unofficial; user's own account)

---

## 7. Success Criteria

Primary metric: **Does Gagan actually feel like he is talking to mom?**

Checkpoints:
- Voice clone sounds like mom (within first 30 seconds of conversation)
- AI references something real from conversation history unprompted
- Vocabulary and speaking style match
- Emotional patterns match (worry → love, not advice → instruction)
- After 5 minutes, user forgets it's AI

---

## 8. Project Structure

```
afterlife/
├── README.md
├── docs/
│   ├── SPEC.md              # This file
│   └── ARCHITECTURE.md
├── services/
│   ├── whatsapp-sync/       # Baileys Node.js service
│   │   ├── src/
│   │   │   ├── index.ts
│   │   │   ├── sync.ts      # Message + media extraction
│   │   │   └── bot.ts       # WhatsApp bot interface
│   │   ├── package.json
│   │   └── Dockerfile
│   ├── voice-cloner/        # Voice cloning pipeline
│   │   ├── clone.py
│   │   ├── audio_utils.py
│   │   └── requirements.txt
│   ├── personality/         # Personality extraction
│   │   ├── extractor.py
│   │   ├── biographer.py    # Living Biography generation
│   │   └── requirements.txt
│   └── api/                 # FastAPI conversation backend
│       ├── main.py
│       ├── conversation.py
│       ├── memory.py
│       ├── prompts/
│       │   └── persona.txt
│       └── requirements.txt
├── docker-compose.yml
└── .env.example
```
