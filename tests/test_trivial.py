"""Stage 4 tests: trivial error-path detector — novelty + count spike (§2.1)."""

from logsig.config import default_config
from logsig.events import Event
from logsig.trivial import detect_trivial


def _alert(ts, tid, msg="boom"):
    return Event(ts, tid, "FATAL", msg, True, template=msg)


def test_novel_template_fires():
    cfg = default_config()
    cfg.bin_width_s = 1.0
    t0 = 1_000_000.0
    events = []
    # known template present throughout training
    for i in range(0, 2000, 50):
        events.append(_alert(t0 + i, "T_known", "known alert"))
    # novel template appears only late (well past training window)
    for i in range(1800, 2000, 5):
        events.append(_alert(t0 + i, "T_novel", "never seen before"))
    flags = detect_trivial(events, cfg)
    novel = [f for f in flags if f.anomaly_type == "novel_template"]
    assert any(f.template_id == "T_novel" for f in novel)


def test_count_spike_fires():
    cfg = default_config()
    cfg.bin_width_s = 1.0
    cfg.trivial.spike_min_count = 5
    t0 = 1_000_000.0
    events = []
    # low steady rate during training, then a spike in the live region
    for i in range(0, 1500):
        if i % 100 == 0:
            events.append(_alert(t0 + i, "T_spiky", "retry failed"))
    # spike: 30 events in one bin in the live region
    for _ in range(30):
        events.append(_alert(t0 + 1800, "T_spiky", "retry failed"))
    flags = detect_trivial(events, cfg)
    spikes = [f for f in flags if f.anomaly_type == "count_spike"]
    assert any(f.template_id == "T_spiky" for f in spikes)
