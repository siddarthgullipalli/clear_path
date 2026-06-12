# Weather-Driven Supply Chain Risk Agent
Harness Engineering Hack — June 12, 2026

## Setup (every teammate does this)

```bash
git clone <repo-url>
cd supply-chain-risk-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# fill in your .env with keys from sponsor tables
```

## Composio Slack setup (Person 1 only, do once)
```bash
composio add slack
# follow the OAuth flow in browser
# paste your SLACK_CHANNEL_ID into .env
```

## ClickHouse setup (Person 2 only, do once)
```bash
# 1. Sign up at clickhouse.cloud (free tier)
# 2. Copy host/password into .env
# 3. Run the seed script:
python database.py
```

## Run locally
```bash
uvicorn main:app --reload --port 8000
# open http://localhost:8000
```

## Demo mode (set before presentation)
```bash
# in .env:
DEMO_MODE=true
# guarantees CRITICAL hit on SH-01 (typhoon scenario)
```

## Team
- Person 1 (Siddarth): agent.py — LangGraph + TrueFoundry + Pioneer + Composio
- Person 2: database.py — ClickHouse schema + seed + Jua AI integration
- Person 3: main.py + openui_component.py — FastAPI + OpenUI dashboard

## Stack
Jua AI · ClickHouse · LangGraph · TrueFoundry · Pioneer · OpenUI · Composio · Render
