# Flight Search AI API

Conversational AI flight search API powered by LangChain agents and multiple flight search sources (Kiwi.com + Duffel).

## Features

- **Multi-source search**: Queries both Kiwi.com and Duffel APIs in parallel for best prices
- **Conversational interface**: Chat endpoint asks for missing details (origin, date, passengers)
- **Smart defaults**: Portland destination, 1 passenger, 5-day round trip
- **Structured endpoint**: Direct search with all parameters provided

## Setup

### 1. Environment Variables

Copy `.env.example` to `../.env`:

```bash
cp .env.example ../.env
```

Edit `../.env`:

```env
OPENAI_API_KEY=sk-...                    # Required
DUFFEL_API_KEY_LIVE=duffel_live_...                   # Optional - enables Duffel search
FLIGHT_APP_TOKEN=your-secret-token       # Optional - enables auth
```

### 2. Install Dependencies

Using `uv`:

```bash
cd /Users/sedhu.m/Documents/Workspace/Langchain/lca-lc-foundations
uv sync
```

### 3. Install Duffel MCP (Optional)

If you have a Duffel API key:

```bash
uvx --from flights-mcp flights-mcp --version
```

This downloads the Duffel MCP server. App will auto-enable it if `DUFFEL_API_KEY_LIVE` is set.

### 4. Run Locally

```bash
cd my_agents
uv run uvicorn app:app --reload
```

Visit: http://127.0.0.1:8000/docs

## Usage

### Web Interface (Easy for Everyone)

Just open the URL in a browser: `https://your-app.up.railway.app`

Simple form:
1. Enter origin (Nashville, BNA, etc.)
2. Pick departure date
3. Select passengers
4. Click "Search Flights"

Results show instantly on same page.

## API Endpoints (for Developers)

### `POST /chat`

Conversational multi-turn flight search.

**Request:**
```json
{
  "message": "I need flights from Nashville",
  "thread_id": "user123"
}
```

**Response:**
```json
{
  "thread_id": "user123",
  "message": "Great! When would you like to depart? Please provide the date (e.g., June 15 or 2026-06-15)."
}
```

Continue conversation with same `thread_id` - agent remembers context.

### `POST /search-flights`

Direct structured search (no conversation).

**Request:**
```json
{
  "origin": "BNA",
  "departure_date": "2026-06-15",
  "passengers": 2,
  "destination": "LAX",
  "return_date": "2026-06-20"
}
```

**Response:**
```json
{
  "request": {...},
  "answer": "🎯 Best Option Found..."
}
```

### `GET /`

Health check.

## Deployment

### Railway

1. Fork/clone repo
2. Create new Railway project from GitHub
3. Set root directory: `my_agents`
4. Add environment variables in Railway dashboard:
   - `OPENAI_API_KEY`
   - `DUFFEL_API_KEY_LIVE` (optional)
   - `FLIGHT_APP_TOKEN` (optional)
5. Set start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
6. Deploy

### AWS (Elastic Beanstalk / ECS)

Coming soon - need Dockerfile or requirements.txt from `uv export`.

## Flight Search Sources

- **Kiwi.com**: Always enabled, no API key needed
- **Duffel**: Enabled when `DUFFEL_API_KEY_LIVE` set (requires verified account)

Agent queries all available sources in parallel and shows cheapest option.

## Development

```bash
# Run with auto-reload
uv run uvicorn app:app --reload --port 8000

# Test chat endpoint
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Find flights from BNA on June 15", "thread_id": "test"}'
```

## Authentication (Optional)

Set `FLIGHT_APP_TOKEN` in environment. Include as header in requests:

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -H "X-App-Token: your-secret-token" \
  -d '{"message": "...", "thread_id": "..."}'
```
