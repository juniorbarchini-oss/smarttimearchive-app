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

