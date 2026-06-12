# agent.py
# Person 1 — Siddarth
# LangGraph StateGraph agent: fetch → score → alert → (log_results) → END
# Uses TrueFoundry LLM gateway + Pioneer model + Composio Slack tool

from typing import TypedDict, List, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv
import json
import os
import time
import logging
from datetime import datetime, timezone

load_dotenv()

# ── Structured logging ───────────────────────────────────────────
logger = logging.getLogger("agent")
logger.propagate = False  # don't bubble up to root — we handle our own output
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ── State schema ─────────────────────────────────────────────────
class AgentState(TypedDict):
    shipment_risks: List[dict]   # from db_output (section 5.3)
    risk_results:   List[dict]   # final RiskResult list (section 5.4)
    alerts_sent:    List[str]    # shipment IDs already alerted this run

# ── LLM via Pioneer (OpenAI-compatible endpoint) ─────────────────
# Pioneer API: https://api.pioneer.ai/v1
# Model: meta-llama/Llama-3.3-70B-Instruct — fast reasoning, no thinking-mode overhead
AGENT_LLM_TIMEOUT = int(os.getenv("AGENT_LLM_TIMEOUT", "10"))
llm = ChatOpenAI(
    model=os.getenv("PIONEER_MODEL_NAME", "meta-llama/Llama-3.3-70B-Instruct"),
    api_key=os.getenv("PIONEER_API_KEY", ""),
    base_url=os.getenv("PIONEER_BASE_URL", "https://api.pioneer.ai/v1"),
    temperature=0.1,             # low temp = consistent risk verdicts
    request_timeout=AGENT_LLM_TIMEOUT,
    max_retries=0,               # we handle retries ourselves with backoff
)

# ── Composio Slack tool (lazy-loaded — avoids init hang without creds) ──
# NEW API (composio >= 0.13): uses Composio().tools.execute()
# OLD API (composio < 0.7): used ComposioToolSet + Action enum
# Both attempted; falls back to Slack webhook if SLACK_WEBHOOK_URL is set
_slack_ready = None
_slack_checked = False

def _get_slack_tools():
    """Lazy-load Slack sending capability. Returns list of tools or empty list."""
    global _slack_ready, _slack_checked
    if _slack_checked:
        return _slack_ready or []
    _slack_checked = True

    api_key = os.getenv("COMPOSIO_API_KEY", "")
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    channel = os.getenv("SLACK_CHANNEL_ID", "")

    # Priority 1: Composio (sponsor tool)
    if api_key:
        try:
            from composio import Composio
            client = Composio(api_key=api_key)
            # Verify Slack is connected
            accts = client.connected_accounts.list()
            slack_accts = []
            if hasattr(accts, 'items'):
                slack_accts = [a for a in accts.items if 'slack' in str(getattr(a, 'appName', '')).lower()]
            if slack_accts and channel:
                _slack_ready = [{"client": client, "account_id": slack_accts[0].id, "channel": channel}]
                logger.info("Composio Slack ready — %d connection(s)", len(slack_accts))
                return _slack_ready
            elif not slack_accts:
                logger.warning("Composio API key set but no Slack connection — run: composio add slack")
        except Exception as e:
            logger.warning("Composio init failed: %s", e)

    # Priority 2: Telegram bot (zero-dependency, no OAuth)
    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if telegram_token and telegram_chat:
        _slack_ready = [{"telegram_token": telegram_token, "telegram_chat": telegram_chat}]
        logger.info("Telegram bot configured — using Telegram for alerts")
        return _slack_ready

    # Priority 3: Slack incoming webhook (zero-dependency fallback)
    if webhook_url:
        _slack_ready = [{"webhook_url": webhook_url}]
        logger.info("Slack webhook configured — using direct webhook")
        return _slack_ready

    logger.info("No Slack sending method configured — alerts will be logged only")
    _slack_ready = []
    return []

# ── Node 1: fetch_shipment_risks ─────────────────────────────────
def fetch_shipment_risks(state: AgentState) -> AgentState:
    """Pull shipment + weather data into the agent state.

    Uses mock data from ``mocks/db_output.json`` when ``USE_MOCKS=true``
    (the default).  When mocks are disabled the node imports Person 2's
    ``database.get_shipment_risks()``.  Falls back to mocks if the
    database module cannot be imported.

    Returns:
        A new ``AgentState`` dict with ``shipment_risks`` populated.
    """
    use_mocks = os.getenv("USE_MOCKS", "true").lower() == "true"

    if use_mocks:
        mock_path = os.path.join(os.path.dirname(__file__), "mocks", "db_output.json")
        with open(mock_path) as f:
            risks = json.load(f)
        logger.info("fetch_shipment_risks: loaded %d shipments from mocks/db_output.json", len(risks))
    else:
        try:
            from database import get_shipment_risks
            risks = get_shipment_risks()
            logger.info("fetch_shipment_risks: loaded %d shipments from database.get_shipment_risks()", len(risks))
        except ImportError:
            logger.warning("database.py not found, falling back to mocks")
            mock_path = os.path.join(os.path.dirname(__file__), "mocks", "db_output.json")
            with open(mock_path) as f:
                risks = json.load(f)

    return {**state, "shipment_risks": risks}


# ── Node 2: score_and_reason ─────────────────────────────────────
def score_and_reason(state: AgentState) -> AgentState:
    """Score every shipment and generate natural-language reasoning.

    For each shipment in ``state["shipment_risks"]``:

    1. Classify severity deterministically via
       ``schemas.classify_severity()`` (no LLM call).
    2. If severity is **HIGH** or **CRITICAL**, ask the Pioneer LLM
       (via TrueFoundry) for a two-sentence risk explanation.  The LLM
       call is retried once with a 1-second backoff before falling back
       to a hand-crafted string specific to the severity level.
    3. Attach an alternate route (from ``schemas.ALTERNATE_ROUTES``)
       and update the shipment status.

    Returns:
        ``AgentState`` with ``risk_results`` populated and summary
        counts logged.
    """
    from schemas import classify_severity, ALTERNATE_ROUTES

    results = []
    for s in state["shipment_risks"]:
        snap = s["weather_snapshot"]
        severity = classify_severity(
            snap["wind_knots_max_72h"],
            snap["storm_probability"]
        )

        if severity in ("HIGH", "CRITICAL"):
            prompt = f"""
You are a maritime risk analyst. Write 2 sentences explaining this risk.
Vessel: {s['vessel']} ({s['origin']} → {s['destination']})
Cargo: {s['cargo']}
Severity: {severity}
Wind: {snap['wind_knots_max_72h']} knots max in 72hrs
Storm probability: {snap['storm_probability']*100:.0f}%
Worst waypoint: {s['worst_waypoint']}
Be specific and urgent. Include the financial risk.
"""

            # Retry once with backoff, then fall back
            reasoning = None
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    response = llm.invoke(prompt)
                    reasoning = response.content.strip()
                    break
                except Exception as e:
                    if attempt < max_attempts - 1:
                        logger.warning(
                            "LLM call attempt %d/%d failed for %s: %s — retrying in 1s",
                            attempt + 1, max_attempts, s["shipment_id"], e,
                        )
                        time.sleep(1)
                    else:
                        logger.warning(
                            "LLM call failed after %d attempts for %s: %s — using fallback reasoning",
                            max_attempts, s["shipment_id"], e,
                        )

            if reasoning is None:
                wind = snap["wind_knots_max_72h"]
                storm = snap["storm_probability"] * 100
                wp = s["worst_waypoint"]
                if severity == "CRITICAL":
                    reasoning = (
                        f"CRITICAL THREAT: {wind} knot winds with {storm:.0f}% storm probability "
                        f"at waypoint {wp} pose an immediate danger to vessel {s['vessel']} and its "
                        f"{s['cargo']} cargo. Emergency rerouting is mandatory — continued exposure "
                        f"risks hull damage, cargo loss, and potential crew endangerment."
                    )
                else:  # HIGH
                    reasoning = (
                        f"HIGH RISK: {wind} knot winds and {storm:.0f}% storm probability "
                        f"near waypoint {wp} threaten on-time delivery of {s['cargo']} aboard "
                        f"{s['vessel']}. Recommend proactive rerouting to avoid schedule slippage "
                        f"and potential cargo damage — ETA impact is estimated at +{ALTERNATE_ROUTES.get(s['shipment_id'], {}).get('eta_impact_hrs', '?')} hours."
                    )

            alt = ALTERNATE_ROUTES.get(s["shipment_id"], {})
            new_status = "DIVERTED" if severity == "CRITICAL" else "DELAYED"
        else:
            reasoning = "Conditions within normal parameters. No action required."
            alt = {}
            new_status = s["status"]  # keep as-is

        results.append({
            "shipment_id":    s["shipment_id"],
            "vessel":         s["vessel"],
            "origin":         s["origin"],
            "destination":    s["destination"],
            "cargo":          s["cargo"],
            "status":         new_status,
            "severity":       severity,
            "reasoning":      reasoning,
            "alternate_route": alt.get("route"),
            "eta_impact_hrs": alt.get("eta_impact_hrs", 0),
            "weather_snapshot": {
                "wind_knots_now":     snap["wind_knots_now"],
                "wind_knots_max_72h": snap["wind_knots_max_72h"],
                "wave_height_m":      snap["wave_height_m"],
                "storm_probability":  snap["storm_probability"],
                "worst_waypoint":     s["worst_waypoint"],
            },
        })

    # Summary counts
    criticals = [r for r in results if r["severity"] == "CRITICAL"]
    highs     = [r for r in results if r["severity"] == "HIGH"]
    mediums   = [r for r in results if r["severity"] == "MEDIUM"]
    lows      = len(results) - len(criticals) - len(highs) - len(mediums)
    logger.info(
        "score_and_reason: processed %d shipments — CRITICAL: %d | HIGH: %d | MEDIUM: %d | LOW: %d",
        len(results), len(criticals), len(highs), len(mediums), lows,
    )

    return {**state, "risk_results": results}


# ── Node 3: send_alerts ──────────────────────────────────────────
def send_alerts(state: AgentState) -> AgentState:
    """Fire Slack alerts for HIGH and CRITICAL shipments via Composio or webhook.

    Supports two methods (checked in order):
    1. **Composio** — uses ``SLACK_SEND_MESSAGE`` via the new SDK
    2. **Slack webhook** — simple POST if ``SLACK_WEBHOOK_URL`` is set

    Deduplicates within a single agent run via ``state["alerts_sent"]``.
    """
    slack_cfg = _get_slack_tools()
    if not slack_cfg:
        logger.info("send_alerts: no Slack sending method available, skipping")
        return state

    cfg = slack_cfg[0]
    sent = list(state.get("alerts_sent", []))
    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for r in state["risk_results"]:
        if r["severity"] not in ("HIGH", "CRITICAL"):
            continue
        if r["shipment_id"] in sent:
            continue

        snap = r["weather_snapshot"]
        emoji = "\U0001f534" if r["severity"] == "CRITICAL" else "\U0001f7e0"
        action_text = r["alternate_route"] or "*Monitor closely"

        msg = (
            f"{emoji} *{r['severity']} WEATHER ALERT* {emoji}\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"*Vessel:*      `{r['vessel']}`\n"
            f"*Route:*       {r['origin']} \u2192 {r['destination']}\n"
            f"*Cargo:*       {r['cargo']}\n"
            f"*Wind:*        {snap['wind_knots_max_72h']} knots (max 72 hr)\n"
            f"*Storm prob:*  {snap['storm_probability']*100:.0f}%\n"
            f"*Wave height:* {snap['wave_height_m']} m\n"
            f"*Action:*      {action_text}\n"
            f"*ETA impact:*  +{r['eta_impact_hrs']} hrs\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"_{r['reasoning']}_\n"
            f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
            f"\U0001f550 *Alert generated:* {now_ts}"
        )
        try:
            if "telegram_token" in cfg:
                _send_telegram(cfg["telegram_token"], cfg["telegram_chat"], msg)
            elif "webhook_url" in cfg:
                _send_webhook(cfg["webhook_url"], msg)
            elif "client" in cfg:
                cfg["client"].tools.execute(
                    slug="SLACK_SEND_MESSAGE",
                    arguments={"channel": cfg["channel"], "text": msg},
                    connected_account_id=cfg["account_id"],
                )
            sent.append(r["shipment_id"])
            logger.info("Slack alert sent for %s (%s)", r["shipment_id"], r["severity"])
        except Exception as e:
            logger.error("Slack send failed for %s: %s", r["shipment_id"], e)

    return {**state, "alerts_sent": sent}


def _send_webhook(webhook_url: str, text: str):
    """Send a message via Slack incoming webhook (no SDK needed)."""
    import urllib.request, json as _json
    data = _json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=10)


def _send_telegram(bot_token: str, chat_id: str, text: str):
    """Send a message via Telegram Bot API (no SDK needed)."""
    import urllib.request, json as _json
    data = _json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=10)

# ── Node 4 (optional): log_results ───────────────────────────────
def log_results(state: AgentState) -> AgentState:
    """Persist risk results to a timestamped JSON file and print a summary table.

    Enabled by default; opt out with ``AGENT_ENABLE_LOG_RESULTS=false``.
    The output file is written to ``agent_results_<ISO8601>.json`` in the
    current working directory.  A human-readable summary table is also
    printed to the log at INFO level.

    Returns:
        Unmodified ``AgentState`` (read-only node).
    """
    results = state.get("risk_results", [])
    if not results:
        logger.info("log_results: no results to write")
        return state

    # Write JSON file
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(os.getcwd(), f"agent_results_{ts}.json")
    try:
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info("log_results: wrote %d results to %s", len(results), out_path)
    except Exception as e:
        logger.error("log_results: failed to write %s: %s", out_path, e)

    # Print summary table
    header = f"{'ID':<8} {'Vessel':<24} {'Severity':<10} {'Status':<12} {'ETA+':>6}"
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for r in results:
        lines.append(
            f"{r['shipment_id']:<8} "
            f"{r['vessel']:<24} "
            f"{r['severity']:<10} "
            f"{r['status']:<12} "
            f"+{r['eta_impact_hrs']}hrs".rjust(8)
        )
    lines.append(sep)
    logger.info("log_results: summary table:\n%s", "\n".join(lines))

    return state


# ── Build & compile the graph ────────────────────────────────────
def build_graph():
    """Construct and compile the LangGraph StateGraph.

    Graph topology::

        fetch → score → alert ──(conditional)──> log_results → END
                           └──(conditional)──> END

    The ``log_results`` node is included only when the env var
    ``AGENT_ENABLE_LOG_RESULTS`` is not set to ``"false"`` (default: true).

    Returns:
        A compiled LangGraph ``CompiledGraph`` ready for ``.invoke()``.
    """
    g = StateGraph(AgentState)
    g.add_node("fetch",  fetch_shipment_risks)
    g.add_node("score",  score_and_reason)
    g.add_node("alert",  send_alerts)

    enable_log = os.getenv("AGENT_ENABLE_LOG_RESULTS", "true").lower() != "false"
    if enable_log:
        g.add_node("log_results", log_results)

    g.set_entry_point("fetch")
    g.add_edge("fetch", "score")
    g.add_edge("score", "alert")

    if enable_log:
        g.add_edge("alert", "log_results")
        g.add_edge("log_results", END)
    else:
        g.add_edge("alert", END)

    return g.compile()


# Lazy-built singleton — compiled on first import, then reused.
# Calling build_graph() at module level is intentional: the graph
# object is a data structure (no I/O), so it's safe to build eagerly.
agent = build_graph()


# ── Public API ───────────────────────────────────────────────────
def run_agent() -> list:
    """Execute the full agent pipeline and return ``risk_results``.

    Person 3's FastAPI server calls this via ``POST /api/run``.
    The function resets state on every call (no cross-run caching).

    Returns:
        ``list[dict]`` — the ``risk_results`` produced by the agent.
    """
    result = agent.invoke({
        "shipment_risks": [],
        "risk_results":   [],
        "alerts_sent":    [],
    })
    return result["risk_results"]


# ── CLI test entrypoint ──────────────────────────────────────────
if __name__ == "__main__":
    # Ensure logger is visible when run directly
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    logger.info("=" * 60)
    logger.info("Running ClearPath agent (mock mode)")
    logger.info("=" * 60)
    results = run_agent()
    logger.info("Agent complete. %d shipments processed.", len(results))
    for r in results:
        logger.info(
            "  %s  %-22s  %-8s  → %s",
            r["shipment_id"], r["vessel"], r["severity"], r["status"],
        )
    logger.info("Full results:\n%s", json.dumps(results, indent=2, default=str))
