"""Hold an OS file lock so two live processes cannot share one action journal."""

import os


class InstanceLock:
    def __init__(self, directory):
        self.file = open(directory / ".instance.lock", "a+b")
        self.file.seek(0, 2)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Kshetrajna is already using this data directory. Open its dashboard or stop that instance.")

    def close(self):
        self.file.close()
