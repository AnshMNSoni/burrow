import hashlib
import threading
import time
from collections import deque
from typing import Dict, List, Callable, Optional, Deque
from burrow.runtime.models import RuntimeEvent
from burrow.core.engine import BurrowEngine
from burrow.parser import LogParser
from burrow.utils.logging import logger

# Maximum entries tracked in the seen-events dedup map and published-sessions set
_MAX_SEEN_EVENTS = 200
_MAX_SESSIONS = 100


class EventBus:
    """Lightweight event bus that reactively coordinates runtime monitoring and analysis."""

    def __init__(self, engine: BurrowEngine, suppress_window: float = 5.0, debounce_delay: float = 0.5):
        self.engine = engine
        self.suppress_window = suppress_window
        self.debounce_delay = debounce_delay
        self.listeners: List[Callable[[RuntimeEvent], None]] = []

        # Bounded duplicate suppression: hash -> timestamp, capped at _MAX_SEEN_EVENTS
        self.seen_events: Dict[str, float] = {}
        self._seen_order: Deque[str] = deque()  # FIFO order for LRU eviction

        # Bounded session tracker
        self.published_sessions: Deque[str] = deque(maxlen=_MAX_SESSIONS)

        self._lock = threading.Lock()
        self._buffers: Dict[str, List[str]] = {}   # session_id -> lines
        self._timers: Dict[str, threading.Timer] = {}  # session_id -> Timer

        # Subscribe the default analysis listener
        self.subscribe(self._default_analysis_listener)

    def subscribe(self, callback: Callable[[RuntimeEvent], None]):
        """Registers a listener callback for runtime events."""
        with self._lock:
            if callback not in self.listeners:
                self.listeners.append(callback)

    def unsubscribe(self, callback: Callable[[RuntimeEvent], None]):
        """Removes a registered listener callback."""
        with self._lock:
            if callback in self.listeners:
                self.listeners.remove(callback)

    def _evict_seen_if_full(self):
        """Evicts the oldest entry from seen_events when at capacity. Must be called under self._lock."""
        while len(self.seen_events) >= _MAX_SEEN_EVENTS and self._seen_order:
            oldest = self._seen_order.popleft()
            self.seen_events.pop(oldest, None)

    def publish(self, event: RuntimeEvent):
        """Publishes a runtime event to all listeners, applying duplicate suppression."""
        content_stripped = event.content.strip()
        if not content_stripped:
            return

        content_hash = hashlib.md5(content_stripped.encode("utf-8")).hexdigest()
        now = time.time()

        with self._lock:
            last_seen = self.seen_events.get(content_hash, 0.0)
            if now - last_seen < self.suppress_window:
                logger.info(f"Suppressed duplicate event {event.event_id} (hash: {content_hash})")
                return

            # Update seen map with LRU eviction
            self._evict_seen_if_full()
            self.seen_events[content_hash] = now
            self._seen_order.append(content_hash)
            self.published_sessions.append(event.session_id)

            # Copy listeners to release the lock during call invocation
            current_listeners = list(self.listeners)

        logger.info(f"Publishing event {event.event_id} from source: {event.source}")
        for listener in current_listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Error invoking listener callback: {e}")

    def publish_log_line(self, session_id: str, line: str, source: str):
        """Buffers log lines and debounces publication until silence window is reached."""
        with self._lock:
            if session_id not in self._buffers:
                self._buffers[session_id] = []
            self._buffers[session_id].append(line)

            if session_id in self._timers:
                self._timers[session_id].cancel()

            def flush_buffer():
                self.flush(session_id, source)

            t = threading.Timer(self.debounce_delay, flush_buffer)
            self._timers[session_id] = t
            t.start()

    def flush(self, session_id: str, source: str):
        """Immediately flushes any buffered lines for the session and publishes if parseable."""
        with self._lock:
            if session_id in self._timers:
                self._timers[session_id].cancel()
                del self._timers[session_id]  # explicit cleanup prevents timer reference leak
            lines = self._buffers.pop(session_id, [])

        if lines:
            content = "".join(lines)
            parser = LogParser()
            if parser.can_parse(content):
                event = RuntimeEvent(
                    session_id=session_id,
                    source=source,
                    content=content
                )
                self.publish(event)

    def _default_analysis_listener(self, event: RuntimeEvent):
        """Listener that automatically runs analysis in a background daemon thread."""
        def _run():
            try:
                logger.info(f"Triggering automated analysis for session: {event.session_id}")
                result = self.engine.analyze_content(event.content)

                try:
                    from burrow.cli.main import display_analysis_result
                    display_analysis_result(result)
                except ImportError:
                    print("\n=== BURROW ANALYSIS RESULT ===")
                    print(result.model_dump_json(indent=2))
            except Exception as e:
                logger.error(f"Automated runtime analysis listener failed: {e}")

        # Run analysis in a daemon thread so the bus is never blocked by slow analysis
        t = threading.Thread(target=_run, daemon=True, name=f"burrow-analysis-{event.session_id[:8]}")
        t.start()
