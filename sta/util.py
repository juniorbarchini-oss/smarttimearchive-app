"""Small helpers shared by the engine."""

import contextlib
import os


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


@contextlib.contextmanager
def as_invoking_user():
    """Run file operations with the identity of whoever ran `sudo`, so the operating system itself
    refuses anything that person could not do. Used wherever the engine (root) touches folders the
    user controls, such as the scan cache: a symlink planted there cannot make root overwrite a
    file the user has no right to. Does nothing when not running as root through sudo."""
    uid, gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if os.geteuid() != 0 or not (uid and gid):
        yield
        return
    groups = os.getgroups()
    os.setgroups([int(gid)])
    os.setegid(int(gid))
    os.seteuid(int(uid))
    try:
        yield
    finally:
        os.seteuid(0)
        os.setegid(0)
        os.setgroups(groups)
