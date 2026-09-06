"""Start the bot as an unprivileged user.

The image runs this as root only long enough to make the Fly volume
(mounted root-owned at /data) writable by the `ezy` user, then drops
privileges permanently and execs the bot. If the container is already
started as a non-root user, it just execs.
"""
import grp
import os
import pwd
import sys

APP_USER = os.environ.get("EZYAI_USER", "ezy")


def _chown_tree(path, uid, gid):
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            try:
                os.chown(os.path.join(root, name), uid, gid)
            except OSError:
                pass
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass


def main():
    argv = sys.argv[1:] or ["python", "main.py"]
    if os.getuid() != 0:
        os.execvp(argv[0], argv)
    try:
        pw = pwd.getpwnam(APP_USER)
    except KeyError:
        os.execvp(argv[0], argv)  # no such user in this image: stay as is
    uid, gid = pw.pw_uid, pw.pw_gid
    state = os.environ.get("EZYAI_STATE_FILE", "")
    data_dir = os.path.dirname(state) if state else ""
    if data_dir and os.path.isdir(data_dir):
        _chown_tree(data_dir, uid, gid)
    os.setgroups([g.gr_gid for g in grp.getgrall() if APP_USER in g.gr_mem])
    os.setgid(gid)
    os.setuid(uid)
    os.environ.setdefault("HOME", pw.pw_dir)
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
