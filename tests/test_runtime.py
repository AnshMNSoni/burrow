import time
import pytest
import subprocess
from unittest.mock import Mock, patch
from burrow.runtime.models import RuntimeEvent
from burrow.runtime.bus import EventBus
from burrow.runtime.watcher import SubprocessWatcher, StdinWatcher
from burrow.cli.main import handle_run, handle_watch

def test_duplicate_suppression():
    # Setup
    mock_engine = Mock()
    bus = EventBus(mock_engine, suppress_window=1.0)
    
    # We will register a listener callback to count invocations
    invocations = []
    def callback(event):
        invocations.append(event)
    bus.subscribe(callback)
    
    # Create identical events
    event1 = RuntimeEvent(session_id="test_sess", source="generic", content="Traceback:\n  File 'app.py'\nZeroDivisionError")
    event2 = RuntimeEvent(session_id="test_sess", source="generic", content="Traceback:\n  File 'app.py'\nZeroDivisionError")
    
    # Publish first event
    bus.publish(event1)
    # Publish second event immediately
    bus.publish(event2)
    
    assert len(invocations) == 1
    
    # Sleep past suppress window and publish again
    time.sleep(1.1)
    bus.publish(event2)
    assert len(invocations) == 2

def test_debouncing():
    mock_engine = Mock()
    # Use very short debounce delay for faster tests
    bus = EventBus(mock_engine, debounce_delay=0.1)
    
    # Let's count published events
    invocations = []
    def callback(event):
        invocations.append(event)
    bus.subscribe(callback)
    
    session_id = "debounce_sess"
    
    # Stream lines of traceback
    bus.publish_log_line(session_id, "Traceback (most recent call last):\n", "python")
    bus.publish_log_line(session_id, "  File \"app.py\", line 1, in <module>\n", "python")
    # Wait slightly but less than debounce delay
    time.sleep(0.05)
    bus.publish_log_line(session_id, "ZeroDivisionError: division by zero\n", "python")
    
    # Assert nothing is published yet
    assert len(invocations) == 0
    
    # Wait for debounce to complete
    time.sleep(0.2)
    
    assert len(invocations) == 1
    assert "ZeroDivisionError" in invocations[0].content
    assert "Traceback" in invocations[0].content

def test_subprocess_failure_detection(tmp_path):
    # Create a crashing script
    script_file = tmp_path / "crash.py"
    script_file.write_text("import sys\nprint('starting')\nsys.stderr.write('Traceback (most recent call last):\\n  File \"crash.py\", line 3\\nZeroDivisionError\\n')\nsys.exit(1)\n", encoding="utf-8")
    
    mock_engine = Mock()
    bus = EventBus(mock_engine, debounce_delay=0.05, suppress_window=1.0)
    
    invocations = []
    def callback(event):
        invocations.append(event)
    bus.subscribe(callback)
    
    watcher = SubprocessWatcher(bus, f"python {script_file}", source="python")
    exit_code = watcher.run()
    
    assert exit_code == 1
    
    # Give some time for background reader threads and timers to flush
    time.sleep(0.2)
    
    assert len(invocations) >= 1
    assert any("ZeroDivisionError" in ev.content for ev in invocations)

def test_cli_command_integration():
    with patch("burrow.cli.main.BurrowEngine") as mock_engine_cls, \
         patch("burrow.runtime.watcher.SubprocessWatcher") as mock_watcher_cls:
        
        mock_watcher = Mock()
        mock_watcher.run.return_value = 0
        mock_watcher_cls.return_value = mock_watcher
        
        args = Mock()
        args.project_root = "."
        args.llm_provider = "mock"
        args.log_level = "info"
        args.run_command = "python -c 'print(1)'"
        args.source = "python"
        
        with pytest.raises(SystemExit) as exc_info:
            handle_run(args)
            
        assert exc_info.value.code == 0
        mock_watcher_cls.assert_called_once()
        # Verify it was instantiated with the correct command
        assert mock_watcher_cls.call_args[1]["command"] == "python -c 'print(1)'"
