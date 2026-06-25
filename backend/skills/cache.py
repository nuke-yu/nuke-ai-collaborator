import copy
import threading
from typing import Callable, Dict, List, Tuple


class CachedScan:
    def __init__(self):
        self._cache: Dict[tuple, Tuple[tuple, list]] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, key: tuple, sig: tuple, compute: Callable[[], List[dict]]) -> List[dict]:
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None and entry[0] == sig:
                return copy.deepcopy(entry[1])
        result = compute()
        with self._lock:
            self._cache[key] = (sig, copy.deepcopy(result))
        return result

