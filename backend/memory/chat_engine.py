"""
ChatEngine – LangChain Memory Chatbot powered by ChatGroq with Deterministic Fallback.

Follows LangChain Essentials Lesson 8 (RunnableWithMessageHistory + InMemoryChatMessageHistory).
Uses ChatGroq (llama-3.3-70b-versatile / llama-3.1-8b-instant) when GROQ_API_KEY is configured.
Falls back seamlessly to local deterministic rule engine when no API key is provided.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from pathlib import Path
from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

for env_path in (
    BACKEND_ROOT / "config" / ".env",
    BACKEND_ROOT / ".env",
    PROJECT_ROOT / ".env",
):
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

# LangChain Imports (Lesson 8)
from langchain_core.chat_history import InMemoryChatMessageHistory
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.history import RunnableWithMessageHistory

try:
    from langchain_groq import ChatGroq
    _GROQ_AVAILABLE = True
except ImportError:
    _GROQ_AVAILABLE = False

from memory.conversation import ConversationBuffer

# Global in-memory session history store for LangChain RunnableWithMessageHistory
_session_history_store: Dict[str, InMemoryChatMessageHistory] = {}


def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
    """Get or create an InMemoryChatMessageHistory for a session (LangChain Lesson 8)."""
    if session_id not in _session_history_store:
        _session_history_store[session_id] = InMemoryChatMessageHistory()
    return _session_history_store[session_id]


def clear_session_history(session_id: str):
    """Clear memory for a specific session."""
    if session_id in _session_history_store:
        del _session_history_store[session_id]


# ---------------------------------------------------------------------------
# Dimension label maps
# ---------------------------------------------------------------------------
DIM_LABELS = {
    "F": "Financial Performance",
    "M": "Market Momentum",
    "C": "Credibility",
    "G": "Sector Growth",
}

DIM_ALIASES = {
    "financial": "F", "financials": "F", "piotroski": "F",
    "momentum": "M", "price": "M", "trend": "M",
    "credibility": "C", "news": "C", "holders": "C",
    "growth": "G", "sector": "G", "peers": "G",
    "f": "F", "m": "M", "c": "C", "g": "G",
}

SCORE_BANDS = [
    (85, "🟢 Excellent"),
    (70, "🔵 Good"),
    (55, "🟡 Average"),
    (0,  "🔴 Weak"),
]


# Keep fallback output ASCII-safe across Windows consoles and the React renderer.
SCORE_BANDS = [
    (85, "Excellent"),
    (70, "Good"),
    (55, "Average"),
    (0, "Weak"),
]


def _band(score: int) -> str:
    for threshold, label in SCORE_BANDS:
        if score >= threshold:
            return label
    return "🔴 Weak"


def _fmt_score(score: Optional[int]) -> str:
    if score is None:
        return "N/A"
    return f"{score}/100 ({_band(score)})"


def _fmt_price(price) -> str:
    if price is None:
        return "N/A"
    return f"${float(price):,.2f}"


def _fmt_dim_scores(scores: Dict[str, int]) -> str:
    parts = []
    for dim, label in DIM_LABELS.items():
        s = scores.get(dim)
        parts.append(f"  **{dim} – {label}**: {_fmt_score(s)}")
    return "\n".join(parts)


def _metrics_summary(details: Dict, dim: str) -> str:
    dim_data = details.get(dim, {})
    metrics = dim_data.get("metrics", [])
    if not metrics:
        return "  No detailed metrics available."
    return "\n".join(f"  • {m['name']}: {m['value']}" for m in metrics[:8])


def _piotroski_summary(details: Dict) -> str:
    p = (details.get("F") or {}).get("piotroski")
    if not p:
        return ""
    lines = [f"  Piotroski F-Score: **{p['score']}/{p['max_score']}** ({p['pct']}%)"]
    sigs = p.get("signals", {})
    pass_sigs = [k for k, v in sigs.items() if v]
    fail_sigs = [k for k, v in sigs.items() if not v]
    if pass_sigs:
        lines.append(f"  ✅ Passing signals: {', '.join(pass_sigs[:4])}")
    if fail_sigs:
        lines.append(f"  ❌ Weak signals: {', '.join(fail_sigs[:4])}")
    return "\n".join(lines)


def _fmt_dim_scores(scores: Dict[str, int]) -> str:
    parts = []
    for dim, label in DIM_LABELS.items():
        s = scores.get(dim)
        parts.append(f"  - **{dim} - {label}**: {_fmt_score(s)}")
    return "\n".join(parts)


def _metrics_summary(details: Dict, dim: str) -> str:
    dim_data = details.get(dim, {})
    metrics = dim_data.get("metrics", [])
    if not metrics:
        return "  No detailed metrics available."
    return "\n".join(f"  - {m['name']}: {m['value']}" for m in metrics[:8])


def _piotroski_summary(details: Dict) -> str:
    p = (details.get("F") or {}).get("piotroski")
    if not p:
        return ""
    lines = [f"  Piotroski F-Score: **{p['score']}/{p['max_score']}** ({p['pct']}%)"]
    sigs = p.get("signals", {})
    pass_sigs = [k for k, v in sigs.items() if v]
    fail_sigs = [k for k, v in sigs.items() if not v]
    if pass_sigs:
        lines.append(f"  - Passing signals: {', '.join(pass_sigs[:4])}")
    if fail_sigs:
        lines.append(f"  - Weak signals: {', '.join(fail_sigs[:4])}")
    return "\n".join(lines)


def _followup_suggestions(tickers: List[str], last_ticker: Optional[str]) -> List[str]:
    suggestions = []
    if last_ticker:
        suggestions.append(f"Explain the F-score for {last_ticker}")
        suggestions.append(f"What is {last_ticker}'s momentum trend?")
    if len(tickers) >= 2:
        suggestions.append(f"Compare {tickers[-2]} and {tickers[-1]}")
    suggestions.append("What have we analysed this session?")
    return suggestions[:4]


def _extract_ticker_from_text(text: str, known: List[str]) -> Optional[str]:
    upper = text.upper()
    for t in reversed(known):
        if re.search(rf"\b{re.escape(t)}\b", upper):
            return t
    m = re.search(r"\b([A-Z]{1,5})\b", upper)
    if m and m.group(1) not in {"I", "A", "AN", "THE", "FOR", "AND", "OR", "OF",
                                  "IS", "IT", "MY", "ME", "DO", "DIM", "ALL",
                                  "FMCG", "WHY", "HOW", "WHAT", "SHOW", "GIVE",
                                  "GET", "ANY", "ARE"}:
        return m.group(1)
    return None


def _extract_dim(text: str) -> Optional[str]:
    lower = text.lower()
    for alias, dim in DIM_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lower):
            return dim
    return None


def _classify(text: str, known: List[str]) -> Dict[str, Any]:
    lower = text.lower()
    if any(p in lower for p in ["what have", "session", "list", "analysed", "analyzed",
                                  "history", "all tickers", "so far", "covered"]):
        return {"intent": "list_analysed"}

    if any(p in lower for p in ["compare", "vs", "versus", "against", "difference between"]):
        tickers_found = []
        upper = text.upper()
        for t in known:
            if re.search(rf"\b{re.escape(t)}\b", upper):
                tickers_found.append(t)
        if len(tickers_found) < 2:
            for m in re.finditer(r"\b([A-Z]{1,5})\b", upper):
                cand = m.group(1)
                if cand not in tickers_found and cand not in {
                    "VS", "AND", "OR", "THE", "FOR", "COMPARE", "VERSUS"
                }:
                    tickers_found.append(cand)
                if len(tickers_found) >= 2:
                    break
        return {"intent": "compare", "tickers": tickers_found[:2]}

    if any(p in lower for p in ["latest", "last", "most recent", "just analysed", "previous"]):
        return {"intent": "latest"}

    dim = _extract_dim(text)
    ticker = _extract_ticker_from_text(text, known)
    if dim and any(p in lower for p in ["why", "explain", "drill", "detail", "break",
                                         "tell me about", "what about", "how", "show"]):
        return {"intent": "dimension_drill", "dim": dim, "ticker": ticker}

    if any(p in lower for p in ["score", "rating", "result", "overall", "how did",
                                  "performance", "scorecard"]):
        return {"intent": "overall_score", "ticker": ticker}

    if dim:
        return {"intent": "dimension_drill", "dim": dim, "ticker": ticker}

    if ticker:
        return {"intent": "overall_score", "ticker": ticker}

    return {"intent": "fallback"}


# ---------------------------------------------------------------------------
# ChatEngine Class
# ---------------------------------------------------------------------------
class ChatEngine:
    """
    Chat engine implementing LangChain RunnableWithMessageHistory memory management
    powered by ChatGroq, with deterministic rule-based fallback.
    """

    def __init__(self, buffer: ConversationBuffer, session_id: Optional[str] = None) -> None:
        self.buffer = buffer
        self.session_id = session_id or "default_session"
        self._groq_chain = None
        self._provider = "fallback"
        self._provider_reason = "GROQ_API_KEY is not configured."
        self._init_groq()

    def _init_groq(self):
        """Initialize LangChain ChatGroq chain with RunnableWithMessageHistory (Lesson 8)."""
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not _GROQ_AVAILABLE:
            self._provider_reason = "langchain_groq is not installed."
            return
        if not groq_api_key:
            self._provider_reason = "GROQ_API_KEY is not configured."
            return

        try:
            model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
            llm = ChatGroq(
                model=model_name,
                groq_api_key=groq_api_key,
                temperature=0.2,
            )

            prompt = ChatPromptTemplate.from_messages([
                ("system", (
                    "You are an expert AI financial analyst for FMCG Scorecard Pro. "
                    "You evaluate US equities across 4 dimensions: "
                    "Financials (Piotroski F-Score), Momentum (OHLCV price returns), Credibility (Holders & News), and Growth (Sector Peers). "
                    "\n\nHere is the live session data of analysed stocks:\n{context}\n\n"
                    "Use clear markdown formatting (tables, bullet points, bold numbers). "
                    "When asked to compare stocks, generate a clean markdown comparison table. "
                    "Keep answers concise, insightful, and grounded in the provided scorecard data."
                )),
                MessagesPlaceholder(variable_name="history"),
                ("human", "{input}"),
            ])

            chain = prompt | llm

            self._groq_chain = RunnableWithMessageHistory(
                chain,
                get_session_history,
                input_messages_key="input",
                history_messages_key="history",
            )
            self._provider = "groq_llm"
            self._provider_reason = f"ChatGroq ready with model {model_name}."
        except Exception as exc:
            self._groq_chain = None
            self._provider = "fallback"
            self._provider_reason = f"ChatGroq initialization failed: {exc}"

    def respond(self, user_message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Process user message:
          1. Try LangChain ChatGroq with RunnableWithMessageHistory if key is present.
          2. Fallback to deterministic rule engine.
        """
        sid = session_id or self.session_id
        tickers = self.buffer.list_tickers()
        suggestions = _followup_suggestions(tickers, self.buffer.last_ticker())
        ctx = self.buffer.context_snapshot()

        # Try ChatGroq LangChain memory chain first
        if self._groq_chain:
            try:
                context_str = json.dumps(ctx, indent=2) if ctx else "No scorecards analysed yet."
                config = {"configurable": {"session_id": sid}}
                response = self._groq_chain.invoke(
                    {"input": user_message, "context": context_str},
                    config=config,
                )
                reply_text = response.content if hasattr(response, "content") else str(response)
                self.buffer.add_turn(user_message, reply_text, {"intent": "groq_llm", "model": "ChatGroq"})
                return {
                    "reply": reply_text,
                    "suggestions": suggestions,
                    "intent": "groq_llm",
                    "provider": "groq_llm",
                    "provider_reason": self._provider_reason,
                    "tickers_in_memory": tickers,
                }
            except Exception as exc:
                self._provider = "fallback"
                self._provider_reason = f"ChatGroq request failed: {exc}"

        # Deterministic engine fallback
        intent = _classify(user_message, tickers)
        reply = self._dispatch(intent, ctx, tickers, user_message)
        self.buffer.add_turn(user_message, reply, {"intent": intent.get("intent")})

        return {
            "reply": reply,
            "suggestions": suggestions,
            "intent": intent.get("intent"),
            "provider": self._provider,
            "provider_reason": self._provider_reason,
            "tickers_in_memory": tickers,
        }

    # ------------------------------------------------------------------
    # Deterministic Dispatch & Builders
    # ------------------------------------------------------------------
    def _dispatch(
        self,
        intent: Dict[str, Any],
        ctx: Dict[str, Any],
        tickers: List[str],
        raw: str,
    ) -> str:
        name = intent.get("intent")

        if name == "list_analysed":
            return self._reply_list(tickers)

        if name == "latest":
            t = self.buffer.last_ticker()
            if not t:
                return self._no_data_yet()
            return self._reply_overall(t, ctx[t])

        if name == "compare":
            return self._reply_compare(intent.get("tickers", []), ctx)

        if name == "overall_score":
            t = intent.get("ticker") or self.buffer.last_ticker()
            if not t or t not in ctx:
                return self._ticker_not_found(intent.get("ticker"), tickers)
            return self._reply_overall(t, ctx[t])

        if name == "dimension_drill":
            t = intent.get("ticker") or self.buffer.last_ticker()
            dim = intent.get("dim")
            if not t or t not in ctx:
                return self._ticker_not_found(intent.get("ticker"), tickers)
            return self._reply_dimension(t, dim, ctx[t])

        return self._reply_fallback(raw, tickers, ctx)

    def _reply_overall(self, ticker: str, data: Dict) -> str:
        name = data.get("name") or ticker
        overall = data.get("overall_score")
        scores = data.get("scores", {})
        price = _fmt_price(data.get("currentPrice"))
        sector = data.get("sector") or "N/A"

        lines = [
            f"### {ticker} — {name}",
            f"**Overall FMCG Score: {_fmt_score(overall)}**",
            f"Price: {price} · Sector: {sector}",
            "",
            "**Dimension breakdown:**",
            _fmt_dim_scores(scores),
        ]
        piotroski = _piotroski_summary(data.get("details", {}))
        if piotroski:
            lines += ["", "**Piotroski signals:**", piotroski]
        return "\n".join(lines)

    def _reply_dimension(self, ticker: str, dim: Optional[str], data: Dict) -> str:
        scores = data.get("scores", {})
        details = data.get("details", {})
        dim_label = DIM_LABELS.get(dim, dim or "Unknown")
        score = scores.get(dim)
        dim_data = details.get(dim, {})
        notes = dim_data.get("notes", [])

        lines = [
            f"### {ticker} — {dim_label} (Dimension {dim})",
            f"**Score: {_fmt_score(score)}**",
            "",
            "**Metrics:**",
            _metrics_summary(details, dim),
        ]
        if notes:
            lines += ["", "**Analysis notes:**"]
            lines += [f"  • {n}" for n in notes[:5]]

        if dim == "F":
            piotroski = _piotroski_summary(details)
            if piotroski:
                lines += ["", "**Piotroski F-Score detail:**", piotroski]

        if dim == "M":
            returns = dim_data.get("returns", {})
            if returns:
                lines += ["", "**Price returns:**"]
                for period, val in returns.items():
                    arrow = "📈" if val >= 0 else "📉"
                    lines.append(f"  {arrow} {period}: {val:+.1f}%")

        if dim == "C":
            news = dim_data.get("news", [])
            if news:
                lines += ["", "**Recent headlines:**"]
                for n in news[:3]:
                    lines.append(f"  • {n.get('title', '')[:80]}")

        if dim == "G":
            peers = dim_data.get("peers", [])
            if peers:
                lines += ["", f"**Sector peers ({len(peers)} tracked):**"]
                for p in peers[:4]:
                    rg = p.get("revenueGrowth")
                    rg_str = f"{rg*100:+.1f}%" if rg is not None else "N/A"
                    lines.append(f"  • {p.get('symbol', '')} ({p.get('name', '')[:20]}): rev growth {rg_str}")

        return "\n".join(lines)

    def _reply_compare(self, tickers: List[str], ctx: Dict) -> str:
        if len(tickers) < 2:
            known = list(ctx.keys())
            if len(known) >= 2:
                tickers = known[-2:]
            else:
                return ("I need two tickers to compare. "
                        "Run scorecards for at least two stocks first, then ask me again.")

        found = [t for t in tickers if t in ctx]
        missing = [t for t in tickers if t not in ctx]

        if len(found) < 2:
            miss_str = ", ".join(missing)
            return (f"I don't have data for **{miss_str}** yet. "
                    f"Run a scorecard for {miss_str} first, then I can compare.")

        a, b = found[0], found[1]
        da, db = ctx[a], ctx[b]

        lines = [f"## {a} vs {b}", ""]
        lines.append(f"| Dimension | {a} | {b} |")
        lines.append("|-----------|" + "-" * (len(a) + 2) + "|" + "-" * (len(b) + 2) + "|")

        sa = da.get("scores", {})
        sb = db.get("scores", {})

        for dim, label in DIM_LABELS.items():
            va = sa.get(dim, "N/A")
            vb = sb.get(dim, "N/A")
            winner = ""
            if isinstance(va, int) and isinstance(vb, int):
                winner = f" ← {a}" if va > vb else (f" ← {b}" if vb > va else " (tied)")
            lines.append(f"| {label} | {va} | {vb}{winner} |")

        oa = da.get("overall_score", 0)
        ob = db.get("overall_score", 0)
        lines.append(f"| **Overall** | **{oa}** | **{ob}** |")
        lines.append("")
        winner_overall = a if oa > ob else (b if ob > oa else None)
        if winner_overall:
            lines.append(f"**Overall winner: {winner_overall}** ({_band(max(oa, ob))})")
        else:
            lines.append("**Both stocks are evenly matched overall.**")
        return "\n".join(lines)

    def _reply_list(self, tickers: List[str]) -> str:
        if not tickers:
            return self._no_data_yet()
        lines = ["### Tickers analysed this session", ""]
        for i, t in enumerate(tickers, 1):
            lines.append(f"{i}. **{t}**")
        lines += ["", f"Total: **{len(tickers)}** stock(s). Ask me about any of them!"]
        return "\n".join(lines)

    def _no_data_yet(self) -> str:
        return ("No scorecards in memory yet. "
                "Search for a ticker (e.g. **AAPL**) to run an analysis, "
                "then I can answer questions about it.")

    def _ticker_not_found(self, ticker: Optional[str], tickers: List[str]) -> str:
        if not tickers:
            return self._no_data_yet()
        known = ", ".join(f"**{t}**" for t in tickers[-5:])
        if ticker:
            return (f"I don't have **{ticker}** in memory yet. "
                    f"Run a scorecard first.\n\nTickers I know: {known}")
        return f"I know about: {known}. Ask me about any of them!"

    def _score_leaders(self, scores: Dict[str, int]) -> tuple[Optional[tuple[str, int]], Optional[tuple[str, int]]]:
        numeric = [
            (DIM_LABELS.get(dim, dim), score)
            for dim, score in scores.items()
            if isinstance(score, int)
        ]
        if not numeric:
            return None, None
        numeric.sort(key=lambda item: item[1], reverse=True)
        return numeric[0], numeric[-1]

    def _reply_grounded_fallback(self, ticker: str, data: Dict, raw: str) -> str:
        name = data.get("name") or ticker
        overall = data.get("overall_score")
        scores = data.get("scores", {})
        strongest, weakest = self._score_leaders(scores)

        lines = [
            f"I can answer that from the scorecard data I have for **{ticker} ({name})**.",
            "",
            f"- Overall score: **{_fmt_score(overall)}**",
        ]

        if strongest:
            lines.append(f"- Strongest area: **{strongest[0]}** at **{_fmt_score(strongest[1])}**")
        if weakest and weakest != strongest:
            lines.append(f"- Main watch area: **{weakest[0]}** at **{_fmt_score(weakest[1])}**")

        details = data.get("details", {})
        piotroski = _piotroski_summary(details)
        if piotroski and re.search(r"\b(financial|quality|risk|why|f-score|piotroski)\b", raw, re.I):
            lines += ["", "**Financial evidence:**", piotroski]

        momentum = (details.get("M") or {}).get("returns", {})
        if momentum and re.search(r"\b(momentum|trend|price|move|return)\b", raw, re.I):
            lines += ["", "**Momentum evidence:**"]
            for period, val in list(momentum.items())[:4]:
                direction = "up" if val >= 0 else "down"
                lines.append(f"- {period}: {direction} {abs(val):.1f}%")

        lines += [
            "",
            "For a more conversational answer, configure `GROQ_API_KEY`; otherwise I will stay grounded to the local scorecard fields.",
        ]
        return "\n".join(lines)

    def _reply_fallback(self, raw: str, tickers: List[str], ctx: Dict[str, Any]) -> str:
        if tickers:
            selected = _extract_ticker_from_text(raw, tickers) or self.buffer.last_ticker()
            if selected and selected in ctx:
                return self._reply_grounded_fallback(selected, ctx[selected], raw)

            known = ", ".join(f"**{t}**" for t in tickers[-5:])
            return (f"I'm not sure what you mean. I have data for {known}.\n\n"
                    "Try asking:\n"
                    "- *What is AAPL's score?*\n"
                    "- *Explain MSFT's momentum*\n"
                    "- *Compare AAPL and MSFT*\n"
                    "- *What have we analysed?*")
        return self._no_data_yet()
