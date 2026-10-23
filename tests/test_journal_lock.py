import subprocess
import sys

from niimbot_server.journal import Journal


def test_second_process_cannot_open_journal_until_owner_closes(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    journal = Journal(path)
    script = (
        "from pathlib import Path; from niimbot_server.journal import Journal; "
        "import sys; j=Journal(Path(sys.argv[1])); j.close()"
    )

    def child():
        return subprocess.run(
            [sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=15
        )

    try:
        result = child()
        assert result.returncode != 0
        assert "Another niimbot-server process owns this journal" in result.stderr
    finally:
        journal.close()
    assert child().returncode == 0
