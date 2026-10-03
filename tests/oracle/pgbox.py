"""Run the real PostgreSQL binaries unpacked by fetch_pg.py: throw-away clusters in a temp directory, memory-light, always stopped again."""
import os
import re
import shutil
import subprocess
import tempfile

MAJORS = ["14", "15", "16", "17", "18"]
SMALL = ["-c", "shared_buffers=16MB", "-c", "max_connections=8", "-c", "max_wal_senders=2", "-c", "wal_level=replica", "-c", "fsync=off",
         "-c", "autovacuum=off", "-c", "listen_addresses=", "-c", "max_worker_processes=2"]


class Box:
    def __init__(self, root: str, work: str = None):
        self.root = os.path.abspath(root)
        self.work = work or tempfile.mkdtemp(prefix="pg18oracle-")
        self.env = dict(os.environ, LD_LIBRARY_PATH=os.path.join(self.root, "lib"), LC_ALL="C", LANG="C", PGCONNECT_TIMEOUT="10")
        self.env.pop("PGHOST", None)
        self.running = {}

    def bin(self, major: str, name: str) -> str:
        return os.path.join(self.root, major, "usr", "lib", "postgresql", major, "bin", name)

    def run(self, argv, timeout=60, env=None, input=None, cwd=None):
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env or self.env, input=input, cwd=cwd or self.work, errors="replace")
        return p.returncode, p.stdout, p.stderr

    def tool(self, major, name, *args, **kw):
        return self.run([self.bin(major, name), *args], **kw)

    def datadir(self, tag: str) -> str:
        return os.path.join(self.work, tag)

    def initdb(self, major: str, tag: str, *extra):
        d = self.datadir(tag)
        shutil.rmtree(d, ignore_errors=True)
        rc, out, err = self.tool(major, "initdb", "-D", d, "-U", "postgres", "-A", "trust", "--no-sync", *extra)
        if rc != 0:
            raise RuntimeError(f"initdb {major} failed: {err[-300:]}")
        return d

    def start(self, major: str, tag: str, *extra):
        d = self.datadir(tag)
        sock = self.work
        opts = " ".join(SMALL + ["-c", f"unix_socket_directories={sock}", "-p", self.port(major), *extra])
        rc, out, err = self.tool(major, "pg_ctl", "-D", d, "-o", opts, "-w", "-t", "40", "-l", os.path.join(d, "log"), "start")
        if rc != 0:
            log = open(os.path.join(d, "log"), encoding="utf-8", errors="replace").read()[-600:] if os.path.exists(os.path.join(d, "log")) else ""
            return False, (err + log).strip()
        self.running[tag] = (major, d)
        return True, ""

    def port(self, major):
        return str(55400 + int(major))

    def stop(self, tag):
        major, d = self.running.pop(tag, (None, None))
        if major:
            self.tool(major, "pg_ctl", "-D", d, "-m", "fast", "-w", "stop")

    def stop_all(self):
        for tag in list(self.running):
            self.stop(tag)

    def psql(self, major, sql, db="postgres", *extra):
        return self.tool(major, "psql", "-X", "-A", "-t", "-q", "-h", self.work, "-p", self.port(major), "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", *extra, "-c", sql)

    def cleanup(self):
        self.stop_all()
        shutil.rmtree(self.work, ignore_errors=True)


def first_error(text: str) -> str:
    for line in text.splitlines():
        m = re.search(r"(ERROR|FATAL|WARNING|LOG):\s+(.*)", line)
        if m and (m.group(1) != "LOG" or "configuration" in m.group(2) or "unrecognized" in m.group(2)):
            return f"{m.group(1)}: {m.group(2)}"[:160]
    return text.strip().splitlines()[0][:160] if text.strip() else ""
