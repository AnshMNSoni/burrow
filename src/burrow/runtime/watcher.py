import os
import sys
import subprocess
import threading
from typing import List, Optional
from burrow.runtime.models import RuntimeEvent, SessionState
from burrow.runtime.bus import EventBus
from burrow.utils.logging import logger

class SubprocessWatcher:
    """Wraps target command execution in a subprocess, streaming logs and capturing failures."""
    
    def __init__(self, bus: EventBus, command: str, source: str = "generic"):
        self.bus = bus
        self.command = command
        self.source = source
        self.session_state = SessionState()
        self.process: Optional[subprocess.Popen] = None
        self._threads: List[threading.Thread] = []
        self._output_lines: List[str] = []
        self._output_lock = threading.Lock()

    def run(self) -> int:
        """Executes the command, streams output, and blocks until completion."""
        logger.info(f"Starting subprocess execution: {self.command}")
        
        env = os.environ.copy()
        # Force Python to run unbuffered so we get real-time tracebacks
        env["PYTHONUNBUFFERED"] = "1"
        
        # On Windows, shell=True is useful for executing complex commands
        self.process = subprocess.Popen(
            self.command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
            encoding="utf-8",
            errors="ignore"
        )
        
        # Spawn threads to read stdout and stderr concurrently
        t_stdout = threading.Thread(
            target=self._read_stream,
            args=(self.process.stdout, "stdout"),
            name="burrow-stdout-reader"
        )
        t_stderr = threading.Thread(
            target=self._read_stream,
            args=(self.process.stderr, "stderr"),
            name="burrow-stderr-reader"
        )
        
        t_stdout.start()
        t_stderr.start()
        
        # Wait for reader threads to finish streaming
        t_stdout.join()
        t_stderr.join()
        
        # Wait for the process to exit
        exit_code = self.process.wait()
        logger.info(f"Subprocess finished with exit code: {exit_code}")
        
        # Flush any remaining logs
        self.bus.flush(self.session_state.session_id, self.source)
        
        # If the command failed and no error event was published yet, publish a fallback event
        session_id = self.session_state.session_id
        is_published = False
        with self.bus._lock:
            # Check if any event has been published under this session ID
            # Let's inspect the seen events or add published tracking
            if not hasattr(self.bus, "published_sessions"):
                self.bus.published_sessions = set()
            is_published = session_id in self.bus.published_sessions

        if exit_code != 0 and not is_published:
            with self._output_lock:
                fallback_content = "".join(self._output_lines).strip()
            
            if fallback_content:
                logger.info(f"Subprocess exited with {exit_code} and no traceback event was published. Emitting fallback event.")
                event = RuntimeEvent(
                    session_id=session_id,
                    source=self.source,
                    content=fallback_content,
                    exit_code=exit_code
                )
                self.bus.publish(event)
                
        return exit_code

    def _read_stream(self, stream, name: str):
        """Reads lines from a stream, writes them to standard output, and sends to the event bus."""
        for line in iter(stream.readline, ""):
            # Write to matching standard console stream
            if name == "stdout":
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                sys.stderr.write(line)
                sys.stderr.flush()
                
            # Keep a copy of all output lines in case we need a fallback event
            with self._output_lock:
                self._output_lines.append(line)
                
            # Publish line to the event bus
            self.bus.publish_log_line(self.session_state.session_id, line, self.source)


class StdinWatcher:
    """Streams lines from standard input in a loop, pushing tracebacks to the event bus."""
    
    def __init__(self, bus: EventBus, source: str = "generic"):
        self.bus = bus
        self.source = source
        self.session_state = SessionState()

    def watch(self):
        """Blocks on reading stdin line-by-line until EOF or interrupt."""
        logger.info("Initializing stdin log streaming watcher...")
        try:
            for line in sys.stdin:
                # Echo line to stdout
                sys.stdout.write(line)
                sys.stdout.flush()
                # Send to event bus
                self.bus.publish_log_line(self.session_state.session_id, line, self.source)
        except KeyboardInterrupt:
            logger.info("Stdin watching interrupted by user.")
        finally:
            self.bus.flush(self.session_state.session_id, self.source)
