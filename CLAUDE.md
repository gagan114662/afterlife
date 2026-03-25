# Afterlife — Coding Standards

This document defines the coding standards for all services in this repository.
All contributors (human and AI) must follow these rules.

## Python Standards

### No Bare Excepts

Never use bare `except:` clauses. Always catch specific exceptions.

```python
# WRONG
try:
    result = do_thing()
except:
    pass

# RIGHT
try:
    result = do_thing()
except ValueError as e:
    logger.error("invalid value", error=str(e))
except httpx.HTTPError as e:
    logger.error("http error", error=str(e))
```

### Pydantic Field Validation

All data models must use Pydantic with explicit `Field` validation.

```python
from pydantic import BaseModel, Field

class PersonalityProfile(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    tone: str = Field(..., pattern=r"^(warm|neutral|direct)$")
    confidence: float = Field(..., ge=0.0, le=1.0)
    traits: list[str] = Field(default_factory=list, max_items=20)
```

### MongoDB for State

Persistent state must use MongoDB. Do not use SQLite, flat files, or in-memory
dicts for state that must survive restarts.

```python
from motor.motor_asyncio import AsyncIOMotorClient

client = AsyncIOMotorClient(settings.MONGODB_URI)
db = client[settings.MONGODB_DB]
collection = db["personalities"]

# Upsert pattern
await collection.update_one(
    {"_id": profile_id},
    {"$set": profile.model_dump()},
    upsert=True,
)
```

### Structured Logging with structlog

All logging must use `structlog`. No `print()` statements in service code.

```python
import structlog

logger = structlog.get_logger(__name__)

# Bind context, then log
logger.info("personality extracted", user_id=user_id, trait_count=len(traits))
logger.error("extraction failed", user_id=user_id, error=str(e))
```

Configure structlog at service startup:

```python
import structlog
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
```

### Environment Variable Validation

All environment variables must be validated at startup using Pydantic Settings.
Never use `os.getenv()` with silent fallbacks for required config.

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    MONGODB_URI: str = Field(..., description="MongoDB connection string")
    MONGODB_DB: str = Field(default="afterlife")
    ANTHROPIC_API_KEY: str = Field(..., description="Anthropic API key")
    ELEVENLABS_API_KEY: str = Field(..., description="ElevenLabs API key")
    PORT: int = Field(default=8000)

settings = Settings()  # Raises ValidationError at startup if required vars missing
```

### /health Endpoints

Every FastAPI service must expose a `/health` endpoint.

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="api", version="0.1.0")
```

The health endpoint must return `200 OK` when the service is ready to accept requests.

## TypeScript Standards

### Strict Mode

All TypeScript must be compiled with `strict: true` in tsconfig.json.

### No `any`

Avoid `any` types. Use `unknown` and narrow types explicitly.

### Error Handling

Use typed error handling:

```typescript
try {
  const result = await fetchData();
} catch (error: unknown) {
  if (error instanceof Error) {
    console.error("fetch failed", { message: error.message });
  }
}
```

## CI/CD Gates

Before merging, all code must pass:

- **Python**: `ruff check .` (lint) + `pytest` (tests)
- **TypeScript**: `tsc --noEmit` (typecheck) + `npm test` (tests)

These gates are enforced by GitHub Actions (`.github/workflows/ci.yml`) and the
Refinery merge queue.
