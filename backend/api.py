"""
FastAPI backend – exposes the FMCG agentic pipeline to the React frontend.
"""

from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict

from memory.conversation import ConversationBuffer
from memory.chat_engine import ChatEngine, clear_session_history

# Per-session conversation buffers  { session_id: ConversationBuffer }
_sessions: Dict[str, ConversationBuffer] = {}


def _get_or_create_buffer(session_id: Optional[str]) -> Optional[ConversationBuffer]:
    if not session_id:
        return None
    if session_id not in _sessions:
        _sessions[session_id] = ConversationBuffer()
    return _sessions[session_id]

from governance.policy import GovernanceEngine
from routing.a2a import A2ABus
from mcp.yfinance_client import YFinanceClient
from agents.specialists import (
    FinancialAgent, MomentumAgent, CredibilityAgent, GrowthAgent, SynthesizerAgent,
)
from routing.router import RouterAgent

app = FastAPI(title="FMCG Agentic Scorecard API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Wire the system once at startup
governance = GovernanceEngine(audit_path=str(ROOT / "logs" / "audit.jsonl"))
bus = A2ABus()
yf = YFinanceClient()

financial = FinancialAgent(governance, bus, yf)
momentum = MomentumAgent(governance, bus, yf)
credibility = CredibilityAgent(governance, bus, yf)
growth = GrowthAgent(governance, bus, yf)
synthesizer = SynthesizerAgent(governance, bus, yf)

router = RouterAgent(
    governance, bus, yf,
    financial, momentum, credibility, growth, synthesizer,
)


class ScoreRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    momentum_period: Optional[str] = "6mo"


class ChatRequest(BaseModel):
    session_id: str
    message: str


class TickerRequest(BaseModel):
    symbol: str


@app.get("/")
async def root():
    return {"message": "FMCG Agentic Scorecard API is running. Check /health for status."}


@app.get("/favicon.ico")
async def favicon():
    return {"message": "No favicon"}


@app.get("/health")
async def health():
    from tracing import tracer
    return {
        "status": "ok",
        "framework": "FMCG",
        "governance": "AGT-patterns",
        "tracing": {
            "local": True,
            "langsmith": tracer.langsmith_enabled,
            "project": tracer.langsmith_project if tracer.langsmith_enabled else None,
        },
    }


@app.post("/api/scorecard")
async def scorecard(req: ScoreRequest):
    """Run full agentic FMCG scorecard pipeline (traced)."""
    result = await router.handle(req.query, momentum_period=req.momentum_period or "6mo")
    if "error" in result and "scores" not in result:
        raise HTTPException(status_code=400, detail=result["error"])

    # Inject result into session memory
    buf = _get_or_create_buffer(req.session_id)
    if buf and result.get("ticker"):
        buf.add_scorecard(result["ticker"], result)

    return result


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """Memory-aware chatbot endpoint."""
    buf = _get_or_create_buffer(req.session_id)
    if buf is None:
        raise HTTPException(status_code=400, detail="session_id is required")

    engine = ChatEngine(buf, session_id=req.session_id)
    response = engine.respond(req.message, session_id=req.session_id)
    return {
        "reply": response["reply"],
        "suggestions": response["suggestions"],
        "intent": response["intent"],
        "provider": response.get("provider"),
        "provider_reason": response.get("provider_reason"),
        "tickers_in_memory": response["tickers_in_memory"],
        "history": [
            {
                "role": m["role"],
                "content": m["content"],
                "timestamp": m["timestamp"],
            }
            for m in buf.get_visible_history()[-40:]
        ],
    }


@app.delete("/api/chat/{session_id}")
async def clear_chat(session_id: str):
    """Clear conversation history for a given session."""
    if session_id in _sessions:
        del _sessions[session_id]
    clear_session_history(session_id)
    return {"message": "Chat history cleared"}


@app.get("/api/scorecard/{symbol}")
async def scorecard_by_symbol(symbol: str):
    result = await router.handle(f"FMCG scorecard for {symbol}")
    if "error" in result and "scores" not in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/api/info/{symbol}")
async def ticker_info(symbol: str):
    data = await yf.get_ticker_info(symbol)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


@app.get("/api/history/{symbol}")
async def price_history(symbol: str, period: str = "6mo"):
    data = await yf.get_price_history(symbol, period=period)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


@app.get("/api/traces")
async def list_traces(limit: int = 20):
    """Return recent local LangSmith-style trace trees (newest first)."""
    import json
    from tracing import tracer

    path = tracer.log_path
    if not path.exists():
        return {"traces": [], "count": 0}

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    rows.reverse()
    # Lightweight index for list view
    index = [
        {
            "trace_id": t.get("trace_id") or t.get("id"),
            "name": t.get("name"),
            "start_time": t.get("start_time"),
            "latency_ms": t.get("latency_ms") or t.get("extra", {}).get("latency_ms"),
            "ticker": (t.get("extra") or {}).get("ticker") or (t.get("outputs") or {}).get("ticker"),
            "overall_score": (t.get("outputs") or {}).get("overall_score"),
            "error": t.get("error"),
            "child_count": len(t.get("children") or []),
        }
        for t in rows
    ]
    return {"traces": index, "count": len(index)}


@app.get("/api/traces/{trace_id}")
async def get_trace(trace_id: str):
    """Full hierarchical trace tree for one run."""
    import json
    from tracing import tracer

    path = tracer.log_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="No traces yet")

    for line in reversed(path.read_text(encoding="utf-8").strip().splitlines()):
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t.get("trace_id") == trace_id or t.get("id") == trace_id:
            return t
    raise HTTPException(status_code=404, detail="Trace not found")


# ── Governance API (AGT-style dashboard) ─────────────────────────────

@app.get("/api/governance/status")
async def governance_status():
    """Full governance engine status: OWASP controls, circuit breakers, counters."""
    return governance.status()


@app.post("/api/governance/toggle-kill")
async def toggle_kill_switch():
    """Toggle the global kill switch (ASI-10)."""
    governance.kill_switch = not governance.kill_switch
    return {
        "kill_switch": governance.kill_switch,
        "message": "Kill switch ACTIVATED — all agent actions blocked"
        if governance.kill_switch
        else "Kill switch deactivated — normal operations resumed",
    }


@app.get("/api/governance/audit")
async def governance_audit(limit: int = 50):
    """Recent audit chain entries (newest first)."""
    return {"records": governance.audit_history(limit), "total": len(governance.audit_chain)}


@app.post("/api/governance/verify")
async def governance_verify():
    """Cryptographic verification of the full audit JSONL chain (ASI-04)."""
    return governance.verify_chain()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
