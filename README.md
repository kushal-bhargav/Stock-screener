# FMCG Scorecard Pro

**Agentic multi-dimensional stock analysis for the US market**  
Financial · Momentum · Credibility · Growth

**Market scope: US only (NYSE / NASDAQ)**

Built with:
- **yfinance** data layer (aligned with [narumiruna/yfinance-mcp](https://github.com/narumiruna/yfinance-mcp))
- **Piotroski F-Score** for Financial dimension
- **Agentic routing + A2A messaging**
- **Microsoft AGT-style governance** (OWASP Agentic Top 10 patterns)
- **React** stock analysis frontend

---

## Architecture

```
React Frontend (port 5173)
        │
        ▼
FastAPI Backend (port 8000)
        │
        ▼
Router Agent
   ├── Financial Agent  → yfinance financials + Piotroski F-Score
   ├── Momentum Agent   → real OHLCV price history
   ├── Credibility Agent→ holders + news
   └── Growth Agent     → sector peers + growth metrics
        │
        ▼
Governance Layer (capability allow-lists, audit, circuit breakers)
```

---

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python api.py
```

API runs at http://localhost:8000

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI runs at http://localhost:5173

### Public dev URLs without a token

For temporary public URLs while both local servers are running, use Cloudflare Quick Tunnel.
This does not require a Cloudflare account or token.

Install `cloudflared` once and make sure it is on `PATH`, then run from the project root:

```bat
scripts\public-dev.bat
```

The script starts:
- FastAPI on `http://127.0.0.1:8000`
- Vite on `http://127.0.0.1:5173`
- a public backend URL like `https://...trycloudflare.com`
- a public frontend URL like `https://...trycloudflare.com`

It writes the temporary backend tunnel URL to `frontend/.env.public.local` as `VITE_API_URL`,
so the public frontend calls the public backend instead of `localhost`.
Keep the Command Prompt window open; the public URLs live only while the script and tunnels are running.

---

## API

| Endpoint | Description |
|----------|-------------|
| `POST /api/scorecard` | Full agentic FMCG pipeline `{"query": "AAPL"}` |
| `GET /api/scorecard/{symbol}` | Same by symbol |
| `GET /api/info/{symbol}` | Ticker info |
| `GET /api/history/{symbol}` | Price history |

---

## FMCG Dimensions

| Dim | Source | Key Signal |
|-----|--------|------------|
| **F** | yfinance financials | **Piotroski F-Score** (0–9) |
| **M** | price history | 1M / 3M / 6M returns |
| **C** | holders + news | Institutional ownership, insider activity, negative news flags |
| **G** | sector peers + growth | Revenue/earnings growth + peer set |

---

## Governance

Every tool call is evaluated by the policy engine before execution:
- Per-agent capability allow-lists
- Circuit breakers
- Tamper-evident audit log (`backend/logs/audit.jsonl`)
- Kill switch support

---

## License

MIT

---

## Observability (LangSmith-style tracing)

Every scorecard run produces a **hierarchical trace tree**:

```
fmcg_scorecard (root)
├── governance.route
├── agent.financial
│   ├── tool.yfinance.get_financials
│   └── tool.yfinance.get_ticker_info
├── agent.momentum
│   └── tool.yfinance.get_price_history
├── agent.credibility
│   ├── tool.yfinance.get_holders
│   └── tool.yfinance.get_ticker_news
├── agent.growth
│   └── tool.yfinance.get_top
├── a2a.fan_in
└── agent.synthesizer
```

### Local traces (always on)

Written to `backend/logs/traces.jsonl`.

```bash
# List recent runs
curl http://localhost:8000/api/traces

# Full tree for one run
curl http://localhost:8000/api/traces/<trace_id>
```

The scorecard API response also includes `trace_id` so the UI can deep-link.

### Optional LangSmith cloud export

```bash
export LANGSMITH_API_KEY=lsv2_...
export LANGSMITH_PROJECT=fmcg-scorecard   # optional
# LANGSMITH_TRACING=false   # to disable cloud even if key is set
```

When the key is present, root runs are also posted to LangSmith (project `fmcg-scorecard` by default). Local tracing continues regardless.

Install the optional SDK:

```bash
pip install langsmith
```
