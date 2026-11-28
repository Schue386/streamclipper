"""One shared, RAM-gated production slot per Linux instance.
Only waiting render heads are queued; ingest and analysis never take the slot.
The existing worker owns all rendering and its process group.
"""
import math
import os
import sqlite3
import stat
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path


class ProductionSlot:
    def __init__(self, root=None, budget=None, wait=60.0, interval=1.0):
        self.root = Path(root) if root is not None else Path('/tmp') / f'sc-production-{os.getuid()}'
        self.budget = budget
        self.wait = float(wait)
        self.interval = float(interval)

    def _prepare(self):
        self.root.mkdir(mode=0o700, exist_ok=True)
        s = self.root.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or s.st_mode & 0o077:
            raise OSError('Unsafe production directory')
        with closing(self._connect()) as db:
            db.execute('CREATE TABLE IF NOT EXISTS waiting (id TEXT PRIMARY KEY, score REAL NOT NULL, entered REAL NOT NULL, expires REAL NOT NULL)')
            db.commit()

    def _connect(self):
        return sqlite3.connect(self.root / 'queue.sqlite3', timeout=0.25)

    def _register(self, ticket, score, entered, expires):
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM waiting WHERE expires<=?', (entered,))
            if db.execute('SELECT COUNT(*) FROM waiting').fetchone()[0] >= 32:
                return False
            db.execute('INSERT INTO waiting VALUES (?,?,?,?)', (ticket,score,entered,expires))
            return True

    def _head(self, ticket):
        now = time.monotonic()
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM waiting WHERE expires<=?', (now,))
            # Aging adds one point per second; waiting time remains bounded.
            row = db.execute('SELECT id FROM waiting ORDER BY score + (? - entered) DESC,entered,id LIMIT 1', (now,)).fetchone()
            if not row or row[0] != ticket:
                return False
            db.execute('DELETE FROM waiting WHERE id=?', (ticket,))
            return True

    def _remove(self, ticket):
        try:
            with closing(self._connect()) as db, db:
                db.execute('DELETE FROM waiting WHERE id=?',(ticket,))
        except (OSError, sqlite3.Error):
            pass  # expires automatically; never remove a live lock file

    @contextmanager
    def acquire(self, score, cancelled=lambda: False):
        import fcntl
        budget = self.budget
        if budget is None:
            from streamclipper.util import render_budget_available
            budget = render_budget_available
        try:
            score = float(score)
            if not math.isfinite(score): score = 0.0
        except (ValueError,TypeError,OverflowError):
            score = 0.0
        score = max(0.0,min(score,100.0))
        ticket = uuid.uuid4().hex
        fd = None
        granted = False
        registered = False
        entered = time.monotonic()
        deadline = entered + self.wait
        try:
            try:
                self._prepare()
                registered = self._register(ticket,score,entered,deadline)
                if registered:
                    fd = os.open(self.root / 'render.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,0o600)
                    while not cancelled() and time.monotonic() < deadline:
                        if budget():
                            try:
                                fcntl.flock(fd,fcntl.LOCK_EX | fcntl.LOCK_NB)
                            except BlockingIOError:
                                pass
                            else:
                                try:
                                    granted = not cancelled() and budget() and self._head(ticket)
                                finally:
                                    if not granted: fcntl.flock(fd,fcntl.LOCK_UN)
                                if granted: break
                        time.sleep(min(self.interval,max(0,deadline-time.monotonic())))
            except (OSError,sqlite3.Error):
                granted = False  # fail closed, keep the exported window
            yield granted
        finally:
            if fd is not None:
                try:
                    if granted: fcntl.flock(fd,fcntl.LOCK_UN)
                finally:
                    os.close(fd)  # kernel releases flock when the worker dies (exec children do not inherit fd)
            if registered:
                self._remove(ticket)
