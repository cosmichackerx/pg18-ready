"""Download the real PostgreSQL server and client packages the oracle runs, check their SHA-256 against tests/oracle/pg-sha256.txt, and unpack them
(no root, no cluster, nothing installed system-wide).

    python tests/oracle/fetch_pg.py DEST [--versions 14,15,16,17,18] [--flavor debian13|ubuntu24.04] [--pin]

The packages are the PGDG builds from apt.postgresql.org (postgresql-N, postgresql-client-N, libpq5) plus liburing2 from the Debian/Ubuntu archive, which the
PostgreSQL 18 server links against. Layout afterwards: DEST/N/usr/lib/postgresql/N/bin/{postgres,initdb,pg_ctl,pg_upgrade,psql,...} and DEST/lib/ (shared libraries).
--pin prints the hash lines for the pinned file names instead of checking them (maintainer use when the pinned file names change).
"""
import hashlib
import os
import subprocess
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VERSIONS = {"14": "14.24", "15": "15.19", "16": "16.15", "17": "17.11", "18": "18.6"}   # latest minor of each major on 2026-10-03 (postgresql.org/support/versioning)
FLAVORS = {
    "debian13": {"tag": "pgdg13", "liburing": "https://deb.debian.org/debian/pool/main/libu/liburing/liburing2_2.9-1_amd64.deb"},
    "ubuntu24.04": {"tag": "pgdg24.04", "liburing": "https://archive.ubuntu.com/ubuntu/pool/main/libu/liburing/liburing2_2.5-1build1_amd64.deb"},
}
POOL = "https://apt.postgresql.org/pub/repos/apt/pool/main/p/postgresql-%s/%s"


def flavor_of_host() -> str:
    info = {}
    try:
        for line in open("/etc/os-release", encoding="utf-8"):
            if "=" in line:
                k, v = line.rstrip().split("=", 1)
                info[k] = v.strip('"')
    except OSError:
        pass
    key = f"{info.get('ID', '')}{info.get('VERSION_ID', '')}"
    if key not in FLAVORS:
        sys.exit(f"unsupported host {key!r}: the oracle packages are pinned for {', '.join(FLAVORS)}; pass --flavor")
    return key


def files(major: str, flavor: str):
    full, tag = VERSIONS[major], FLAVORS[flavor]["tag"]
    names = [f"postgresql-{major}_{full}-1.{tag}+2_amd64.deb", f"postgresql-client-{major}_{full}-1.{tag}+2_amd64.deb"]
    return [(POOL % (major, urllib.parse.quote(n)), n) for n in names]


def pins() -> dict:
    d = {}
    for line in open(os.path.join(HERE, "pg-sha256.txt"), encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#"):
            fl, name, digest = line.split()
            d[(fl, name)] = digest
    return d


def download(url: str, path: str) -> str:
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "pg18-ready-oracle"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as fh:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
            fh.write(chunk)
    return h.hexdigest()


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    dest = args[0]
    opt = lambda n, d=None: args[args.index(n) + 1] if n in args else d
    majors = opt("--versions", ",".join(VERSIONS)).split(",")
    flavor = opt("--flavor") or flavor_of_host()
    pin = "--pin" in args
    known = {} if pin else pins()
    os.makedirs(os.path.join(dest, "dl"), exist_ok=True)
    os.makedirs(os.path.join(dest, "lib"), exist_ok=True)
    jobs = []
    for m in majors:
        for url, name in files(m, flavor):
            jobs.append((m, url, name))
    libpq_tag = FLAVORS[flavor]["tag"]
    libpq = f"libpq5_18.6-1.{libpq_tag}+2_amd64.deb"
    jobs.append(("lib", POOL % ("18", urllib.parse.quote(libpq)), libpq))
    ur = FLAVORS[flavor]["liburing"]
    jobs.append(("lib", ur, ur.rsplit("/", 1)[1]))
    for target, url, name in jobs:
        path = os.path.join(dest, "dl", name)
        if os.path.exists(path):
            digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        else:
            digest = download(url, path)
        if pin:
            print(f"{flavor} {name} {digest}")
            continue
        want = known.get((flavor, name))
        if want is None:
            sys.exit(f"{name}: no pinned hash for {flavor} in pg-sha256.txt")
        if digest != want:
            os.remove(path)
            sys.exit(f"{name}: SHA-256 mismatch (got {digest}, pinned {want})")
        out = os.path.join(dest, target if target != "lib" else "libroot")
        os.makedirs(out, exist_ok=True)
        subprocess.run(["dpkg-deb", "-x", path, out], check=True)
    if not pin:
        # collect the shared libraries (libpq, liburing) in one directory
        libroot = os.path.join(dest, "libroot")
        for root, _, fs in os.walk(libroot):
            for f in fs:
                if f.startswith(("libpq.so", "liburing.so")):
                    target = os.path.join(dest, "lib", f)
                    if not os.path.lexists(target):
                        os.symlink(os.path.join(root, f), target)
        print("ok:", ", ".join(f"{m}={VERSIONS[m]}" for m in majors), "flavor", flavor)


if __name__ == "__main__":
    main()
