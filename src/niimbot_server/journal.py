"""Local operational journal, not a substitute for BitacoraDB experiment records."""

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .models import Job


def now():
    return datetime.now(UTC).isoformat()


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file = path.with_suffix(".lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                # Windows locks a byte range, not the entire file. All owners
                # lock byte zero; closing the handle releases it after a crash.
                self.lock_file.seek(0, os.SEEK_END)
                if self.lock_file.tell() == 0:
                    self.lock_file.write(b"\0")
                    self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError("Another niimbot-server process owns this journal") from None
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS events
            (id INTEGER PRIMARY KEY, job_id TEXT, at TEXT, data TEXT NOT NULL)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS configuration_events
            (id INTEGER PRIMARY KEY, at TEXT, data TEXT NOT NULL)""")
        self.db.commit()
        for (data,) in self.db.execute("SELECT data FROM jobs").fetchall():
            job = Job.model_validate_json(data)
            if job.state == "running":
                self.update(job, state="unknown", message="Service interrupted; outcome unknown")

    def configuration_event(self, **data):
        with self.db:
            self.db.execute(
                "INSERT INTO configuration_events(at,data) VALUES (?,?)", (now(), json.dumps(data))
            )

    def get(self, job_id: str):
        row = self.db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def uncertain(self):
        return [
            job
            for (data,) in self.db.execute("SELECT data FROM jobs")
            if (job := Job.model_validate_json(data)).state == "unknown"
        ]

    def put(self, job: Job):
        with self.db:
            self.db.execute("INSERT INTO jobs VALUES (?,?)", (job.job_id, job.model_dump_json()))
            self.db.execute(
                "INSERT INTO events(job_id,at,data) VALUES (?,?,?)",
                (job.job_id, now(), job.model_dump_json()),
            )

    def update(self, job: Job, **changes):
        job = Job.model_validate({**job.model_dump(), **changes, "updated_at": now()})
        with self.db:
            self.db.execute(
                "UPDATE jobs SET data=? WHERE id=?", (job.model_dump_json(), job.job_id)
            )
            self.db.execute(
                "INSERT INTO events(job_id,at,data) VALUES (?,?,?)",
                (job.job_id, now(), json.dumps(changes)),
            )
        return job

    def close(self):
        self.db.close()
        self.lock_file.close()
