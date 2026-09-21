"""Small helpers shared by the engine."""

import contextlib
import os
import shutil


def own(path):
    """When run through sudo, hand what we create back to the invoking user."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() == 0 and uid and gid:
        with contextlib.suppress(OSError):
            os.lchown(path, int(uid), int(gid))


def own_tree(path):
    own(path)
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            own(os.path.join(root, name))


def invoking_user():
    """(uid, gid) of whoever ran `sudo`, or None when we are not root through sudo."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() != 0 or not (uid and gid):
        return None
    return int(uid), int(gid)


def _drop_for_good(uid, gid):
    """Permanent: groups first, then gid, and uid last (after it there is no way back to root)."""
    os.setgroups([gid])
    os.setgid(gid)
    os.setuid(uid)


CHUNK = 1 << 20


def _in_child(work):
    """Run work() in a forked child that has already given up root; returns its exit status ok?"""
    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            work()
            code = 0
        finally:
            os._exit(code)
    _, status = os.waitpid(pid, 0)
    return status == 0


def makedirs_as_user(path):
    """mkdir -p with the rights of the invoking user (root would follow whatever they planted)."""
    who = invoking_user()
    if who is None:
        os.makedirs(path, exist_ok=True)
        return
    if not _in_child(lambda: (_drop_for_good(*who), os.makedirs(path, exist_ok=True))):
        raise OSError(f"could not create {path} as the invoking user")


def copy_from_user(src, dst):
    """Copy a file the user controls into dst (a place only root can write). The read happens in a
    child with the user's rights and travels through a pipe, so root never opens the user's path."""
    who = invoking_user()
    if who is None:
        shutil.copyfile(src, dst)
        return
    r, w = os.pipe()

    def child():
        os.close(r)
        _drop_for_good(*who)
        with open(src, "rb") as f:
            while chunk := f.read(CHUNK):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(w, view) :]

    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            child()
            code = 0
        finally:
            os._exit(code)
    os.close(w)
    try:
        with open(dst, "wb") as out:
            while data := os.read(r, CHUNK):
                out.write(data)
    finally:
        os.close(r)
    _, status = os.waitpid(pid, 0)
    if status != 0:
        raise OSError(f"could not read {src} as the invoking user")


def copy_to_user(src, dst):
    """Copy a root-side file to dst in a folder the user controls: the child (user's rights) creates
    the folder, writes a temp file that cannot follow a link, and renames it into place."""
    who = invoking_user()
    tmp = dst + ".tmp"
    if who is None:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        shutil.copyfile(src, tmp)
        os.rename(tmp, dst)
        return
    r, w = os.pipe()

    def child():
        os.close(w)
        _drop_for_good(*who)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as out:
            while data := os.read(r, CHUNK):
                out.write(data)
        os.rename(tmp, dst)

    pid = os.fork()
    if pid == 0:
        code = 1
        try:
            child()
            code = 0
        finally:
            os._exit(code)
    os.close(r)
    try:
        with open(src, "rb") as f:
            while chunk := f.read(CHUNK):
                view = memoryview(chunk)
                while view:
                    view = view[os.write(w, view) :]
    finally:
        os.close(w)
    _, status = os.waitpid(pid, 0)
    if status != 0:
        raise OSError(f"could not write {dst} as the invoking user")
