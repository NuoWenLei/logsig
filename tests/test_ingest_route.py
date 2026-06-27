"""Stage 1 tests: BGL parsing, templating sanity, alert-tag routing (§2.2)."""

import os

import pytest

from logsig.ingest.bgl import parse_bgl_line, iter_bgl
from logsig.ingest.openstack import parse_openstack_line, iter_openstack
from logsig.template import Templater
from logsig.route import route

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data", "samples",
                      "BGL_2k.log")
OPENSTACK_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "data",
                                "samples", "OpenStack_2k.log")


def test_parse_bgl_line_fields():
    line = ("- 1117838570 2005.06.03 R02-M1-N0-C:J12-U11 "
            "2005-06-03-15.42.50.675872 R02-M1-N0-C:J12-U11 RAS KERNEL INFO "
            "instruction cache parity error corrected")
    r = parse_bgl_line(line)
    assert r is not None
    assert r.timestamp == 1117838570.0
    assert r.level == "INFO"
    assert r.is_alert is False
    assert "parity error" in r.content


def test_parse_bgl_alert_tag_routes_to_trivial():
    line = ("KERNDTLB 1117869872 2005.06.04 R04-M1-N4-C:J05-U01 "
            "2005-06-04-00.24.32.000000 R04-M1-N4-C:J05-U01 RAS KERNEL FATAL "
            "data TLB error interrupt")
    r = parse_bgl_line(line)
    assert r.is_alert is True
    assert r.level == "FATAL"


def test_parse_malformed_returns_none():
    assert parse_bgl_line("") is None
    assert parse_bgl_line("not enough fields") is None


@pytest.mark.skipif(not os.path.exists(SAMPLE), reason="BGL sample not present")
def test_real_sample_template_count_and_routing():
    raws = list(iter_bgl(SAMPLE))
    assert len(raws) > 1900  # ~2000 lines parse
    t = Templater()
    events = list(t.template(raws))
    # Template count is "sane" vs Loghub-2.0 labeling (~120 for the 2k sample):
    # default Drain granularity should land in a reasonable band.
    assert 40 <= t.num_templates <= 300
    routed = route(events)
    # routing must match the alert tag exactly
    assert routed.counts["trivial"] == sum(1 for r in raws if r.is_alert)
    assert routed.counts["spectral"] == sum(1 for r in raws if not r.is_alert)
    assert routed.counts["trivial"] > 0 and routed.counts["spectral"] > 0


def test_parse_openstack_line_fields_and_routing():
    line = ("nova-api.log.1.2017-05-16_13:53:08 2017-05-16 00:00:00.008 25746 "
            "INFO nova.osapi_compute.wsgi.server [req-abc] \"GET /v2 HTTP/1.1\" "
            "status: 200")
    r = parse_openstack_line(line)
    assert r is not None
    assert r.level == "INFO"
    assert r.is_alert is False              # INFO -> spectral path
    assert r.content.startswith("nova.osapi_compute.wsgi.server")
    # ERROR-level routes to the trivial path (level is the router when there is
    # no alert tag, §2.2)
    err = line.replace(" INFO ", " ERROR ")
    assert parse_openstack_line(err).is_alert is True


@pytest.mark.skipif(not os.path.exists(OPENSTACK_SAMPLE),
                    reason="OpenStack sample not present")
def test_openstack_sample_templates_and_routes():
    raws = list(iter_openstack(OPENSTACK_SAMPLE))
    assert len(raws) > 1900
    t = Templater()
    events = list(t.template(raws))
    assert t.num_templates >= 5
    routed = route(events)
    # the normal sample is overwhelmingly INFO -> spectral path
    assert routed.counts["spectral"] > 0
