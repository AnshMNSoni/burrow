import time
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from burrow.core.engine import BurrowEngine
from burrow.workspace.models import WorkspaceContext
from burrow.symbol.models import SymbolGraphData, CodeSmell

# Define a set of benchmark test cases
BENCHMARK_SCENARIOS = [
    {
        "name": "Python Zero Division",
        "trace": (
            "Traceback (most recent call last):\n"
            "  File \"src/math.py\", line 12, in divide\n"
            "    return a / b\n"
            "ZeroDivisionError: division by zero\n"
        ),
        "files": {
            "src/math.py": "def divide(a, b):\n    return a / b\n"
        },
        "expected_origin_file": "src/math.py",
        "expected_error_type": "ZeroDivisionError",
        "expected_hypothesis": "bad_state_propagation"
    },
    {
        "name": "JS Null Property Reference",
        "trace": (
            "TypeError: Cannot read properties of undefined (reading 'name')\n"
            "    at getUserName (src/auth.js:5:25)\n"
            "    at login (src/auth.js:10:5)\n"
        ),
        "files": {
            "src/auth.js": (
                "function getUserName(user) {\n"
                "    return user.name;\n"
                "}\n"
                "function login() {\n"
                "    getUserName(undefined);\n"
                "}\n"
            )
        },
        "expected_origin_file": "src/auth.js",
        "expected_error_type": "TypeError",
        "expected_hypothesis": "null_reference"
    },
    {
        "name": "Missing Configuration .env",
        "trace": (
            "Traceback (most recent call last):\n"
            "  File \"src/config.py\", line 5, in load\n"
            "    db_url = os.environ[\"DATABASE_URL\"]\n"
            "KeyError: 'DATABASE_URL'\n"
        ),
        "files": {
            "src/config.py": (
                "import os\n"
                "def load():\n"
                "    db_url = os.environ['DATABASE_URL']\n"
            ),
            ".env.example": "DATABASE_URL=postgres://localhost:5432/db\n"
        },
        "expected_origin_file": "src/config.py",
        "expected_error_type": "KeyError",
        "expected_hypothesis": "config_issue"
    },
    {
        "name": "Env Variable Mismatch",
        "trace": (
            "Traceback (most recent call last):\n"
            "  File \"src/app.py\", line 15, in connect\n"
            "    key = os.environ.get(\"API_KEY\")\n"
            "  File \"src/app.py\", line 16, in connect\n"
            "    raise ValueError(\"API key missing\")\n"
            "ValueError: API key missing\n"
        ),
        "files": {
            "src/app.py": (
                "import os\n"
                "def connect():\n"
                "    key = os.environ.get('API_KEY')\n"
                "    raise ValueError('API key missing')\n"
            ),
            ".env": "PORT=8000\n",
            ".env.example": "PORT=8000\nAPI_KEY=your_key\n"
        },
        "expected_origin_file": "src/app.py",
        "expected_error_type": "ValueError",
        "expected_hypothesis": "env_mismatch"
    },
    {
        "name": "Null Guard Proximity Code Smell",
        "trace": (
            "Traceback (most recent call last):\n"
            "  File \"src/user.py\", line 4, in render\n"
            "    print(profile.avatar)\n"
            "AttributeError: 'NoneType' object has no attribute 'avatar'\n"
        ),
        "files": {
            "src/user.py": (
                "def render(profile):\n"
                "    print(profile.avatar)\n"
            )
        },
        "smells": [
            CodeSmell(
                smell_type="null_dereference",
                message="profile parameter may be null",
                file_path="src/user.py",
                line_number=4,
                severity="warning"
            )
        ],
        "expected_origin_file": "src/user.py",
        "expected_error_type": "AttributeError",
        "expected_hypothesis": "null_reference"
    },
    {
        "name": "JS Array Index Out of Bounds",
        "trace": (
            "RangeError: Invalid array length\n"
            "    at processItems (src/items.js:3:12)\n"
        ),
        "files": {
            "src/items.js": (
                "function processItems(n) {\n"
                "    let a = new Array(n);\n"
                "}\n"
            )
        },
        "expected_origin_file": "src/items.js",
        "expected_error_type": "RangeError",
        "expected_hypothesis": "bad_state_propagation"
    },
    {
        "name": "Python KeyError",
        "trace": (
            "Traceback (most recent call last):\n"
            "  File \"src/data.py\", line 8, in get_val\n"
            "    return records[key]\n"
            "KeyError: 'age'\n"
        ),
        "files": {
            "src/data.py": (
                "def get_val(records, key):\n"
                "    return records[key]\n"
            )
        },
        "expected_origin_file": "src/data.py",
        "expected_error_type": "KeyError",
        "expected_hypothesis": "bad_state_propagation"
    },
    {
        "name": "Webpack App Error",
        "trace": (
            "TypeError: Cannot read properties of undefined (reading 'match')\n"
            "    at App (webpack:///src/App.js:15:24)\n"
        ),
        "files": {
            "src/App.js": (
                "function App() {\n"
                "    let val = undefined;\n"
                "    return val.match('test');\n"
                "}\n"
            )
        },
        "expected_origin_file": "src/App.js",
        "expected_error_type": "TypeError",
        "expected_hypothesis": "null_reference"
    },
    {
        "name": "Go Generic CLI Error",
        "trace": "src/main.go:25: undefined: fmt.Printlnn\n",
        "files": {
            "src/main.go": (
                "package main\n"
                "import \"fmt\"\n"
                "func main() {\n"
                "    fmt.Printlnn(\"Hello\")\n"
                "}\n"
            )
        },
        "expected_origin_file": "src/main.go",
        "expected_error_type": "CLIError",
        "expected_hypothesis": "bad_state_propagation"
    },
    {
        "name": "GCC C Error",
        "trace": "src/main.c:10:5: error: expected ';' before 'return'\n",
        "files": {
            "src/main.c": (
                "int main() {\n"
                "    int x = 10\n"
                "    return 0;\n"
                "}\n"
            )
        },
        "expected_origin_file": "src/main.c",
        "expected_error_type": "error",
        "expected_hypothesis": "bad_state_propagation"
    }
]

def run_benchmark():
    results = []
    passed_count = 0
    total_latency = 0.0

    for scenario in BENCHMARK_SCENARIOS:
        with TemporaryDirectory() as tmpdir:
            root_path = Path(tmpdir)
            # Create files
            for rel_path, content in scenario["files"].items():
                dest = root_path / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")
                
            # Create engine
            engine = BurrowEngine(project_root=root_path)
            
            # Setup custom symbol graph if smells are specified
            smells = scenario.get("smells")
            sym_data = SymbolGraphData(smells=smells) if smells else None
            
            # Run analysis
            start_time = time.time()
            if sym_data:
                # Mock analyze_content or inject smell data
                with patch("burrow.core.engine.WorkspaceScanner._extract_git_context", return_value=None):
                    result = engine.analyze_content(scenario["trace"])
                    # Inject smells directly into hypotheses calculation
                    result = engine.analyze_content(scenario["trace"])
                    from burrow.rca.engine import RootCauseAnalyzer
                    analyzer = RootCauseAnalyzer(root_path)
                    rca_res = analyzer.analyze(result.error, symbol_graph_data=sym_data)
                    result.rca_result = rca_res
            else:
                with patch("burrow.core.engine.WorkspaceScanner._extract_git_context", return_value=None):
                    result = engine.analyze_content(scenario["trace"])
            
            latency_ms = (time.time() - start_time) * 1000
            total_latency += latency_ms
            
            # Extract detected origin file and error type
            detected_file = result.error.root_origin.file_path if result.error.root_origin else "None"
            # Normalize detected file path (remove base dir)
            try:
                detected_file = Path(detected_file).relative_to(root_path).as_posix()
            except ValueError:
                detected_file = Path(detected_file).as_posix()
                
            detected_error = result.error.error_type
            
            # Match validation
            file_match = (detected_file == scenario["expected_origin_file"])
            error_match = (detected_error == scenario["expected_error_type"])
            
            # Hypothesis type check
            hypotheses = result.rca_result.hypotheses if result.rca_result else []
            primary_hyp = hypotheses[0].type if hypotheses else "None"
            hyp_match = (primary_hyp == scenario["expected_hypothesis"])
            
            # Status
            status = file_match and error_match and hyp_match
            if status:
                passed_count += 1
                
            results.append({
                "name": scenario["name"],
                "expected_file": scenario["expected_origin_file"],
                "detected_file": detected_file,
                "expected_error": scenario["expected_error_type"],
                "detected_error": detected_error,
                "expected_hyp": scenario["expected_hypothesis"],
                "detected_hyp": primary_hyp,
                "latency_ms": latency_ms,
                "status": "PASS" if status else "FAIL"
            })

    # Print results table
    print("\n" + "=" * 50)
    print("      BURROW RCA ACCURACY BENCHMARK REPORT      ")
    print("=" * 50)
    print(f"| {'Scenario':<25} | {'Expected File':<15} | {'Detected File':<15} | {'Expected Hyp':<15} | {'Detected Hyp':<15} | {'Latency':<8} | {'Status':<5} |")
    print(f"|{'-'*27}|{'-'*17}|{'-'*17}|{'-'*17}|{'-'*17}|{'-'*10}|{'-'*7}|")
    for r in results:
        print(f"| {r['name']:<25} | {r['expected_file']:<15} | {r['detected_file']:<15} | {r['expected_hyp']:<15} | {r['detected_hyp']:<15} | {r['latency_ms']:>6.1f}ms | {r['status']:<5} |")
    print("=" * 50)
    
    accuracy = (passed_count / len(BENCHMARK_SCENARIOS)) * 100
    avg_latency = total_latency / len(BENCHMARK_SCENARIOS)
    print(f"Total Scenarios : {len(BENCHMARK_SCENARIOS)}")
    print(f"Passed          : {passed_count}")
    print(f"Accuracy        : {accuracy:.1f}%")
    print(f"Average Latency : {avg_latency:.1f}ms")
    print("=" * 50 + "\n")
    
    return passed_count, len(BENCHMARK_SCENARIOS), accuracy, avg_latency

def test_rca_intelligence_benchmark():
    passed, total, accuracy, avg_latency = run_benchmark()
    # Require at least 90% benchmark accuracy to prevent intelligence regression
    assert accuracy >= 90.0, f"RCA Accuracy benchmark failed: only {accuracy:.1f}% scored"

if __name__ == "__main__":
    run_benchmark()
