"""
OpenAI Brain — System Health Monitor & Active Regulator
======================================================
• Tracks every process event (STT, TTS, OpenAI, Deepgram, WebSocket)
• Uses GPT-4o to analyse faults and decide recovery actions
• Active Regulator: Can execute commands to fix system state
• Exposes a /brain WebSocket endpoint for live dashboarding
"""

import asyncio
import json
import time
import traceback
import os
import sys
import subprocess
import signal
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
from collections import deque

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False
    httpx = None  # type: ignore

# ── Config ────────────────────────────────────────────────────────────────────
LOG_FILE = Path(__file__).parent / "brain.log"
MAX_EVENTS = 200       # rolling event buffer size
ANALYSIS_INTERVAL = 30  # seconds between proactive GPT analysis cycles
MAX_ANALYSIS_BACKLOG = 30 


def _load_env() -> dict:
    env_path = Path(__file__).parent / ".env"
    envs = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                envs[k.strip()] = v.strip()
    return envs


_ENV = _load_env()
OPENAI_KEY = _ENV.get("OPENAI_API_KEY", "")


def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    full = f"[BRAIN {ts}] {msg}"
    print(full)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(full + "\n")
    except Exception:
        pass


# ── In-memory state ───────────────────────────────────────────────────────────
class SystemBrain:
    """
    Central singleton that aggregates system events and uses GPT-4o
    to decide if something is wrong and what to do about it.
    """

    def __init__(self):
        self.events: deque = deque(maxlen=MAX_EVENTS)
        self.subscribers: List[Any] = []       # WebSocket connections subscribed to /brain feed
        self.component_status: Dict[str, Dict[str, Any]] = {
            "websocket_server":  {"status": "unknown", "last_update": None, "detail": ""},
            "deepgram_stt":      {"status": "unknown", "last_update": None, "detail": ""},
            "sarvam_tts":        {"status": "unknown", "last_update": None, "detail": ""},
            "openai_llm":        {"status": "unknown", "last_update": None, "detail": ""},
            "question_gen":      {"status": "unknown", "last_update": None, "detail": ""},
            "scoring":           {"status": "unknown", "last_update": None, "detail": ""},
            "frontend_ws":       {"status": "unknown", "last_update": None, "detail": ""},
        }
        self.recovery_actions: List[Dict[str, Any]] = []   # Suggested/taken actions from GPT
        self.analysis_in_progress = False
        self.last_analysis_time = 0.0
        self.total_errors = 0
        self.total_events = 0
        self.session_start = time.time()
        self._analysis_task: Optional[asyncio.Task] = None # type: ignore[type-arg]
        self.connectivity_status: Dict[str, bool] = {"internet": True, "openai": True, "deepgram": True}
        
        # Regulation Throttling
        self.RESTART_COOLOFF = 300  # 5 minutes
        self._last_restart_time = 0.0

    # ── Event ingestion ───────────────────────────────────────────────────────
    def record(self, component: str, event_type: str, detail: str, severity: str = "info"):
        """
        Record a system event. Call this from ws_server.py on every notable action.
        severity: 'info' | 'warn' | 'error' | 'ok'
        """
        ts = time.time()
        event = {
            "ts": ts,
            "ts_str": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S"),
            "component": component,
            "type": event_type,
            "detail": detail,
            "severity": severity,
        }
        self.events.append(event)
        self.total_events += 1
        if severity == "error":
            self.total_errors += 1

        # Update component status
        if component in self.component_status:
            self.component_status[component]["last_update"] = ts
            self.component_status[component]["detail"] = detail
            if severity == "ok":
                self.component_status[component]["status"] = "healthy"
            elif severity == "error":
                self.component_status[component]["status"] = "error"
            elif severity == "warn":
                self.component_status[component]["status"] = "degraded"
            else:
                self.component_status[component]["status"] = "active"

        _log(f"[{severity.upper()}] [{component}] {event_type}: {detail}")

        # Broadcast to all subscribed frontends (fire and forget)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(event))
            
            # Trigger GPT analysis on errors
            if severity == "error":
                loop.create_task(self._maybe_analyse_now())
        except RuntimeError:
            pass

    def _get_component_summary(self) -> str:
        summary = ""
        for name, data in self.component_status.items():
            last = data['last_update']
            last_str = datetime.fromtimestamp(last, tz=timezone.utc).strftime("%H:%M:%S") if last else "NEVER"
            summary += f"- {name}: status={data['status']}, last_update={last_str}, detail={data['detail']}\n"
        return summary

    # ── GPT Analysis ──────────────────────────────────────────────────────────
    async def _maybe_analyse_now(self):
        """Trigger an immediate GPT review when an error is detected."""
        now = time.time()
        if self.analysis_in_progress:
            return
        # Debounce: don't call GPT more than once every 10 seconds
        if now - self.last_analysis_time < 10:
            return
        await self._run_gpt_analysis("error_triggered")

    async def _run_gpt_analysis(self, trigger_type: str = "periodic"):
        """Call GPT-4o to analyze the system state."""
        if self.analysis_in_progress or not OPENAI_KEY or not _HTTPX_AVAILABLE:
            return
        self.analysis_in_progress = True
        self.last_analysis_time = time.time()
        try:
            # Gather recent events
            all_events = list(self.events)
            start_idx = max(0, len(all_events) - MAX_ANALYSIS_BACKLOG)
            recent = all_events[start_idx:]
            if not recent:
                self.analysis_in_progress = False
                return

            error_events = [e for e in recent if e["severity"] == "error"]
            warn_events  = [e for e in recent if e["severity"] == "warn"]

            # Regulation: Check connectivity before blaming code
            await self._check_external_services()
            conn_report = ", ".join([f"{k}={'UP' if v else 'DOWN'}" for k, v in self.connectivity_status.items()])

            component_summary = self._get_component_summary()
            recent_events_text = "\n".join([f"[{e['ts_str']}] [{e['component']}] {e['type']}: {e['detail']}" 
                                  for e in recent[-15:]])

            prompt = f"""You are the AI Operations Brain for a real-time voice HR interview system.
The system has the following components:
- websocket_server  : Python WebSocket server on port 3002
- deepgram_stt      : Real-time speech-to-text streaming
- sarvam_tts        : Text-to-speech audio generation (bulbul:v2)
- openai_llm        : GPT-4o conversational AI for interview questions
- question_gen      : GPT-4o question generation from job description
- scoring           : GPT-4o/mini scoring of candidate answers
- frontend_ws       : Browser WebSocket connection to logic server

CONNECTIVITY REPORT:
{conn_report}

CURRENT COMPONENT STATUS:
{component_summary}
(Note: "unknown" means the component has not been used yet in this session. This is NORMAL and NOT an error.)

RECENT EVENTS (triggered by: {trigger_type}):
{recent_events_text}

ERRORS detected: {len(error_events)}
WARNINGS detected: {len(warn_events)}

Your task:
1. Identify what went wrong.
2. Assess severity: "ok" | "degraded" | "critical".
3. List 'auto_recovery' actions. Use "RESTART_WS" (sparingly), "FLUSH_BUFFERS", "LOG_DEBUG".
4. Health summary (1-2 sentences).

Respond ONLY with valid JSON:
{{
  "severity": "ok|degraded|critical",
  "root_cause": "<brief explanation>",
  "auto_recovery": ["RESTART_WS", "FLUSH_BUFFERS", ...],
  "operator_actions": ["check internet", ...],
  "health_summary": "System is fine.",
  "affected_components": ["sarvam_tts"]
}}"""

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENAI_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "gpt-4o",
                        "messages": [{"role": "system", "content": prompt}],
                        "temperature": 0.2,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=20.0,
                )

            if resp.status_code == 200:
                data = resp.json()
                analysis = json.loads(data["choices"][0]["message"]["content"])

                action_record = {
                    "ts": time.time(),
                    "ts_str": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    "trigger": trigger_type,
                    "analysis": analysis,
                }
                self.recovery_actions.append(action_record)
                if len(self.recovery_actions) > 20:
                    self.recovery_actions.pop(0)

                _log(f"GPT Analysis: severity={analysis.get('severity')} - {analysis.get('health_summary')}")

                # Execute auto-recovery
                await self._execute_recovery(analysis.get("auto_recovery", []))

                # Broadcast
                await self._broadcast({"type": "brain_analysis", **action_record})

        except Exception as e:
            _log(f"GPT Analysis error: {e}")
        finally:
            self.analysis_in_progress = False

    async def _execute_recovery(self, actions: List[str]):
        """Regulate the system by taking action."""
        for action in actions:
            self.record("websocket_server", "regulation", f"Executing recovery: {action}", "info")
            if action == "RESTART_WS":
                current_time = time.time()
                if current_time - self._last_restart_time < self.RESTART_COOLOFF:
                    _log(f"Active Regulation: RESTART_WS suppressed (cooloff active).")
                    self.record("websocket_server", "regulation", "Restart suppressed (cooloff)", "info")
                    continue
                
                self._last_restart_time = current_time
                _log("Active Regulation: RESTART_WS requested. Restarting current process...")
                # Give a small delay for logs to flush
                await asyncio.sleep(1)
                os.execv(sys.executable, [sys.executable] + sys.argv)
            elif action == "FLUSH_BUFFERS":
                 self.events.clear()
                 _log("Active Regulation: Event buffers flushed.")
                 self.record("websocket_server", "regulation", "Buffers flushed", "ok")
            elif action == "LOG_DEBUG":
                # Create a snapshot file for debugging
                debug_file = Path(__file__).parent / f"debug_snapshot_{int(time.time())}.json"
                debug_file.write_text(json.dumps(self.snapshot(), indent=2))
                _log(f"Active Regulation: Debug snapshot saved to {debug_file.name}")

    async def start_periodic_analysis(self):
        _log(f"Brain watchdog started (interval={ANALYSIS_INTERVAL}s)")
        while True:
            await asyncio.sleep(ANALYSIS_INTERVAL)
            await self._run_gpt_analysis("periodic")

    async def _broadcast(self, payload: dict):
        if not self.subscribers:
            return
        msg = json.dumps({"type": "event", "data": payload})
        dead = []
        for ws in self.subscribers:
            try:
                await ws.send(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.subscribers.remove(ws)

    def snapshot(self) -> dict:
        uptime = int(time.time() - self.session_start)
        return {
            "uptime_sec": uptime,
            "total_events": self.total_events,
            "total_errors": self.total_errors,
            "error_rate": float(round(float(self.total_errors) / max(1, self.total_events), 4)),
            "connectivity": self.connectivity_status,
            "component_status": self.component_status,
            "latest_analysis": self.recovery_actions[-1] if self.recovery_actions else None,
            "recent_events": list(self.events)[max(0, len(self.events)-30):],
        }


    async def _check_external_services(self):
        """Regulation Helper: Verify if external services are reachable."""
        if not _HTTPX_AVAILABLE: return
        async with httpx.AsyncClient(timeout=3.0) as client:
            try:
                # Internet check
                await client.get("https://1.1.1.1", timeout=1.0)
                self.connectivity_status["internet"] = True
            except:
                self.connectivity_status["internet"] = False
            
            try:
                # OpenAI check
                r = await client.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {OPENAI_KEY}"})
                self.connectivity_status["openai"] = (r.status_code != 503)
            except:
                self.connectivity_status["openai"] = False

            try:
                # Deepgram check
                r = await client.get("https://api.deepgram.com/v1/projects")
                self.connectivity_status["deepgram"] = (r.status_code != 503)
            except:
                self.connectivity_status["deepgram"] = False

brain = SystemBrain()

async def brain_ws_handler(websocket):
    _log(f"Brain connected: {websocket.remote_address}")
    brain.subscribers.append(websocket)
    try:
        await websocket.send(json.dumps({"type": "snapshot", "data": brain.snapshot()}))
        async for msg in websocket:
            data = json.loads(msg)
            if data.get("action") == "force_analysis":
                asyncio.create_task(brain._run_gpt_analysis("manual"))
            elif data.get("action") == "record":
                brain.record(data.get("component"), data.get("type"), data.get("detail"), data.get("severity"))
    except:
        pass
    finally:
        if websocket in brain.subscribers:
            brain.subscribers.remove(websocket)
        _log("Brain disconnected")
