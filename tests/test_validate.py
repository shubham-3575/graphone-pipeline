import json, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_validator_flags_stale_job_and_passes_clean(tmp_path):
    (tmp_path / "output").mkdir()
    now = datetime.now(timezone.utc)
    mk = lambda d: {"schemaVersion": "1.0", "recordType": "JOB", "collectedAt": now.isoformat(),
                    "source": {"name": "x", "url": "https://x.com/1"},
                    "content": {"company": "A", "date": d.isoformat()}}
    (tmp_path / "output" / "jobs.jsonl").write_text(
        json.dumps(mk(now - timedelta(hours=2))) + "\n" + json.dumps(mk(now - timedelta(hours=40))) + "\n")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate_outputs.py")], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 1 and "jobs: 1 outside last 24h" in r.stdout
