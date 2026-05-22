import time
import pytest
import sys
import threading
from unittest.mock import Mock, patch
from burrow.runtime.models import RuntimeEvent
from burrow.runtime.bus import EventBus
from burrow.runtime.watcher import SubprocessWatcher

def test_watcher_massive_log_stream(tmp_path):
    # Create a script that prints 20,000 lines
    script = tmp_path / "stream.py"
    script.write_text(
        "import sys\n"
        "for i in range(20000):\n"
        "    print(f'line {i}')\n"
        "sys.stderr.write('Traceback (most recent call last):\\n  File \"app.py\", line 10\\nValueError: bad\\n')\n",
        encoding="utf-8"
    )
    
    mock_engine = Mock()
    bus = EventBus(mock_engine, suppress_window=1.0, debounce_delay=0.05)
    
    # Track published event content
    invocations = []
    def listener(event):
        invocations.append(event)
    bus.subscribe(listener)
    
    watcher = SubprocessWatcher(bus, f"{sys.executable} {script}", source="python")
    exit_code = watcher.run()
    
    assert exit_code == 0
    # Wait for debounce and daemon threads to flush
    time.sleep(0.3)
    
    # We should have captured the traceback
    assert len(invocations) >= 1
    assert "ValueError: bad" in invocations[0].content

def test_watcher_subprocess_killed(tmp_path):
    # Script that exits with an error code
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\n"
        "print('starting')\n"
        "sys.exit(137)\n", # Emulating SIGKILL exit code
        encoding="utf-8"
    )
    
    mock_engine = Mock()
    bus = EventBus(mock_engine)
    
    watcher = SubprocessWatcher(bus, f"{sys.executable} {script}", source="python")
    exit_code = watcher.run()
    
    assert exit_code == 137

def test_event_bus_lru_eviction():
    mock_engine = Mock()
    # Setup bus with custom window
    bus = EventBus(mock_engine, suppress_window=5.0)
    
    # Publish distinct events up to cap (_MAX_SEEN_EVENTS is 200)
    # Let's publish 250 distinct events
    for i in range(250):
        event = RuntimeEvent(
            session_id=f"sess_{i}",
            source="python",
            content=f"Traceback:\n  File \"app_{i}.py\", line 1\nZeroDivisionError"
        )
        bus.publish(event)
        
    # Verify that the seen_events map is capped at _MAX_SEEN_EVENTS (200)
    assert len(bus.seen_events) <= 200
    
    # Oldest events should have been evicted.
    # Event 0's hash should not be in seen_events anymore.
    # Let's check:
    import hashlib
    h_old = hashlib.md5(b"Traceback:\n  File \"app_0.py\", line 1\nZeroDivisionError").hexdigest()
    assert h_old not in bus.seen_events

def test_event_bus_concurrency():
    mock_engine = Mock()
    bus = EventBus(mock_engine, suppress_window=0.01)
    
    # Publish many events from multiple threads simultaneously
    errors = []
    
    def worker(worker_id):
        try:
            for j in range(50):
                event = RuntimeEvent(
                    session_id=f"thread_{worker_id}_sess_{j}",
                    source="python",
                    content=f"Traceback:\n  File \"app.py\", line {j}\nError_{worker_id}_{j}"
                )
                bus.publish(event)
        except Exception as e:
            errors.append(e)
            
    threads = []
    for t_id in range(10):
        t = threading.Thread(target=worker, args=(t_id,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    assert not errors, f"Concurrency errors occurred: {errors}"
