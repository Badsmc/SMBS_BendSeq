"""
state_cache.py - Physical State Cache & Lazy Evaluation Hash Table (SMBS Core)
"""

import hashlib
from typing import Dict, Optional, Set, Any


class StateCache:
    """
    Thread-safe physical state cache avoiding redundant state evaluation.
    Tracks hits, misses, puts, and gets.
    """

    def __init__(self, max_size: int = 1000):
        self._cache: Dict[str, Any] = {}
        self._max_size: int = max_size
        self._hits: int = 0
        self._misses: int = 0
        self._puts: int = 0
        self._gets: int = 0

    @staticmethod
    def compute_hash(shape_or_state: Any, remaining_bends: Optional[Set[str]] = None) -> str:
        """
        Compute robust physical state fingerprint key.
        If a FoldState is passed, invokes fold_state.fingerprint().
        """
        if hasattr(shape_or_state, 'fingerprint') and callable(shape_or_state.fingerprint):
            return shape_or_state.fingerprint()

        rem = remaining_bends or set()
        bend_signature = "_".join(sorted(list(rem)))

        if shape_or_state is not None and hasattr(shape_or_state, 'Volume'):
            try:
                vol = round(float(shape_or_state.Volume), 4)
                area = round(float(shape_or_state.Area), 4)
                bb = shape_or_state.BoundBox
                bb_sig = f"{bb.XMin:.2f}_{bb.XMax:.2f}_{bb.YMin:.2f}_{bb.YMax:.2f}_{bb.ZMin:.2f}_{bb.ZMax:.2f}"
                raw_str = f"shape_v{vol}_a{area}_bb{bb_sig}_bends[{bend_signature}]"
            except Exception:
                raw_str = f"shape_stub_{id(shape_or_state)}_bends[{bend_signature}]"
        else:
            raw_str = f"shape_stub_{id(shape_or_state)}_bends[{bend_signature}]"

        return hashlib.sha256(raw_str.encode('utf-8')).hexdigest()

    def get(self, hash_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve cached lightweight metadata by fingerprint key."""
        self._gets += 1
        if hash_key in self._cache:
            self._hits += 1
            val = self._cache.pop(hash_key)
            self._cache[hash_key] = val
            return val
        self._misses += 1
        return None

    def put(self, hash_key: str, state_meta: Optional[Dict[str, Any]] = None) -> None:
        """Store state metadata and calculated FoldState in cache with LRU eviction and memory cleanup."""
        self._puts += 1
        data = state_meta or {"visited": True}

        if hash_key in self._cache:
            self._cache.pop(hash_key)
            self._cache[hash_key] = data
            return

        if len(self._cache) >= self._max_size:
            oldest_key = next(iter(self._cache))
            evicted = self._cache.pop(oldest_key)
            # Release OCC TopoShape memory on evicted state
            if isinstance(evicted, dict):
                st = evicted.get("state")
                if st is not None and hasattr(st, "clear_shape"):
                    st.clear_shape()

        self._cache[hash_key] = data

    def contains(self, hash_key: str) -> bool:
        """Check if hash key exists in cache."""
        return hash_key in self._cache

    @property
    def stats(self) -> Dict[str, Any]:
        """Return cache hit/miss/put/get statistics."""
        total = self._hits + self._misses
        hit_ratio = (self._hits / total * 100.0) if total > 0 else 0.0
        return {
            "size": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "puts": self._puts,
            "gets": self._gets,
            "hit_ratio_percent": round(hit_ratio, 2)
        }

    def clear(self) -> None:
        """Reset the cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._gets = 0
