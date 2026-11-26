"""Process-local AI concurrency and rate bounds. No content or request URLs retained."""
from collections import OrderedDict, deque
from threading import Lock
from time import monotonic


class UserOperations:
    def __init__(self):
        self._lock = Lock()
        self._active = set()

    def acquire(self, user):
        with self._lock:
            if user in self._active:
                return None
            self._active.add(user)
        released = False

        def release():
            nonlocal released
            with self._lock:
                if not released:
                    released = True
                    self._active.discard(user)
        return release


class UserRateLimit:
    def __init__(self, limit=20, window=60, max_users=512, clock=monotonic):
        self._lock = Lock()
        self._users = OrderedDict()
        self._limit, self._window, self._max_users = limit, window, max_users
        self._clock = clock

    def allow(self, user):
        now = self._clock()
        with self._lock:
            # Do not evict active buckets to allow an authenticated user to bypass
            # the rate cap; expired buckets are cheap to remove.
            for key in list(self._users):
                hits = self._users[key]
                while hits and hits[0] <= now - self._window:
                    hits.popleft()
                if not hits:
                    self._users.pop(key, None)
            if user not in self._users:
                if len(self._users) >= self._max_users:
                    return False
                self._users[user] = deque()
            hits = self._users[user]
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True
