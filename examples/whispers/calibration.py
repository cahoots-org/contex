"""Per-need baselines: how a need scores against ordinary village talk, so matching can use a margin."""

import asyncio
import hashlib
import json
import logging
import os
import tempfile

from examples.whispers.config import Config
from examples.whispers.contex_client import ContexClient
from examples.whispers.models import Utterance

log = logging.getLogger(__name__)

FALLBACK_BASELINE = 0.78
CONCURRENT_QUERIES = 8

CALIBRATION = (
    "It looks like rain again this afternoon.",
    "The market had cabbages at two coppers apiece today.",
    "Somebody's goat wandered off and has not come back.",
    "The bread is rising slowly in this cold.",
    "The gate hinge squeaks every time the wind pushes it.",
    "I swept the front step before breakfast.",
    "The well rope is getting frayed near the bucket.",
    "Eggs were cheaper last week than they are now.",
    "The road to the next village is muddy after the storm.",
    "A crow has been sitting on the fence all morning.",
    "I need to mend my boots before the weather turns.",
    "The hay cart came through a little late today.",
    "Wood is stacked high behind the shed.",
    "There is a stray cat sleeping on the church wall.",
    "The evening bells rang a bit early.",
    "Apples are ripening on the tree by the lane.",
    "The river is lower than it was at midsummer.",
    "My shutters need a fresh coat of paint.",
    "A peddler sold ribbons by the fountain this morning.",
    "The chickens will not stay out of the garden.",
)


def _calibration_data(i: int, sentence: str) -> dict:
    return Utterance(f"cal-{i}", "newcomer", "A newcomer", "square", sentence, None, "whispered to you").to_data()


# Baselines cached under other calibration keys or payloads are not reused.
FINGERPRINT = hashlib.sha256(
    json.dumps([(f"cal:{i}", _calibration_data(i, s)) for i, s in enumerate(CALIBRATION)]).encode()
).hexdigest()[:16]


# Baselines depend only on a need's words, so every village shares one cache.
BASELINES_PATH = "whispers_baselines.json"


class Baselines:
    """Mean similarity of each need text to CALIBRATION, cached on disk by exact text and calibration payload."""

    def __init__(self, contex: ContexClient, config: Config):
        self.contex = contex
        self.config = config
        self.path = config.calibration_path or os.path.join(config.data_dir, BASELINES_PATH)
        self.values: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._published = False
        self._cache_loaded = False
        self._fallback: set[str] = set()
        self._unsaved: set[str] = set()

    def set(self, text: str, baseline: float) -> None:
        self.values[text] = baseline

    def get(self, text: str) -> float:
        return self.values[text]

    def threshold(self, text: str) -> float:
        return self.get(text) + self.config.match_margin

    def matches(self, text: str, similarity: float) -> bool:
        return similarity >= self.threshold(text)

    async def load(self, texts: list[str], persist: bool = True) -> None:
        """Read the cache, then compute the baseline of every text it lacks and, unless persist is off, store it."""
        async with self._lock:
            unique = list(dict.fromkeys(texts))
            if not self._cache_loaded:
                self.values = {**self._read(), **self.values}
                self._cache_loaded = True
            missing = [t for t in unique if t not in self.values or t in self._fallback]
            if unique:
                log.info("calibration: %d computed, %d cached", len(missing), len(unique) - len(missing))
            if not missing:
                return
            await self._publish()
            slots = asyncio.Semaphore(CONCURRENT_QUERIES)

            async def baseline(text: str) -> float | None:
                async with slots:
                    return await self._baseline(text)

            computed = await asyncio.gather(*(baseline(t) for t in missing))
            fresh = {t: v for t, v in zip(missing, computed, strict=True) if v is not None}
            fallen = len(missing) - len(fresh)
            if fallen:
                log.warning("calibration fell back to a default baseline for %d needs", fallen)
            known = [v for t, v in self.values.items() if t not in self._fallback]
            fallback = sum(known) / len(known) if known else FALLBACK_BASELINE
            self.values.update(fresh)
            self._fallback -= fresh.keys()
            if not persist:
                self._unsaved |= fresh.keys()
            for text in missing:
                if text not in fresh:
                    self.values[text] = fallback
                    self._fallback.add(text)
            if persist:
                self._write({t: v for t, v in self.values.items() if t not in self._fallback | self._unsaved})

    async def measure(self, text: str) -> float:
        """The text's baseline, computed fresh and kept neither in values nor on disk."""
        async with self._lock:
            await self._publish()
        baseline = await self._baseline(text)
        if baseline is None:
            raise ValueError("no calibration scores")
        return baseline

    @property
    def _project(self) -> str:
        return f"whispers:{self.config.session}:calibration"

    async def _publish(self) -> None:
        """Publish the calibration sentences once per session; callers hold the lock."""
        if not self._published:
            for i, sentence in enumerate(CALIBRATION):
                await self.contex.publish(self._project, f"cal:{i}", _calibration_data(i, sentence))
            self._published = True

    async def _baseline(self, text: str) -> float | None:
        found = await self.contex.query(self._project, text, top_k=len(CALIBRATION), threshold=0)
        scores = [m.similarity for m in found if m.similarity is not None]
        return sum(scores) / len(scores) if scores else None

    def _read(self) -> dict[str, float]:
        try:
            with open(self.path) as f:
                data = json.load(f)
            if data.get("calibration") != FINGERPRINT:
                return {}
            return {k: float(v) for k, v in data["baselines"].items()}
        except (OSError, ValueError, AttributeError, KeyError, TypeError):
            return {}

    def _write(self, data: dict[str, float]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"calibration": FINGERPRINT, "baselines": data}, f)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
