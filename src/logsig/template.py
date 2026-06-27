"""Drain3 templating wrapper (stage 1).

§3.1 decision: over-decompose rather than collapse. We use Drain3's standard
granularity and do NOT post-merge templates. Fine templates → clean per-behavior
tones; the cost is just more FFTs over sparser series, which is cheap.
"""

from __future__ import annotations

from typing import Iterable, Iterator, List, Optional

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

from .events import Event
from .ingest.bgl import RawLine


def _make_miner(extra_masking: bool = True) -> TemplateMiner:
    config = TemplateMinerConfig()
    # Suppress the "config file not found" noise and pin sensible defaults.
    config.drain_sim_th = 0.4
    config.drain_depth = 4
    if extra_masking:
        # Mask common variable tokens up front so Drain's tree stays compact.
        # Order matters: more specific patterns first.
        config.masking_instructions = []  # default; Drain still wildcards params
    miner = TemplateMiner(config=config)
    return miner


class Templater:
    """Stateful Drain3 templater. Call :meth:`template` per raw line."""

    def __init__(self) -> None:
        self._miner = _make_miner()

    def template_one(self, raw: RawLine) -> Event:
        result = self._miner.add_log_message(raw.content)
        tid = f"T{result['cluster_id']}"
        return Event(
            timestamp=raw.timestamp,
            template_id=tid,
            level=raw.level,
            raw=raw.content,
            is_alert=raw.is_alert,
            template=result["template_mined"],
        )

    def template(self, raws: Iterable[RawLine]) -> Iterator[Event]:
        for raw in raws:
            yield self.template_one(raw)

    @property
    def num_templates(self) -> int:
        return len(self._miner.drain.clusters)

    def template_string(self, template_id: str) -> Optional[str]:
        """Return the current mined template for a Tnn id, if known."""
        try:
            cid = int(template_id.lstrip("T"))
        except ValueError:
            return None
        cluster = self._miner.drain.id_to_cluster.get(cid)
        return cluster.get_template() if cluster else None


def template_all(raws: Iterable[RawLine]) -> List[Event]:
    """Convenience: template a whole iterable, return a list of Events."""
    t = Templater()
    return list(t.template(raws))
