"""Ingestion sources.

Two sources share one job: turn lines into ``(timestamp, level, alert, content)``
tuples that the templating stage can consume.

- ``bgl``:        parse the real Loghub BGL log format.
- ``synthetic``:  generate BGL-format logs with *known* periodic tones, so the
                  Stage-0 survey and the eval harness can run end-to-end without
                  the 708 MB Zenodo download (which the spectral premise needs a
                  long multi-cycle span for anyway). The synthetic path is the
                  CI/demo path; point the same pipeline at a real BGL.log for the
                  honest evaluation. See README "Data" section.
"""

from .bgl import parse_bgl_line, iter_bgl, RawLine  # noqa: F401
from .openstack import parse_openstack_line, iter_openstack  # noqa: F401
from .synthetic import SyntheticConfig, generate_synthetic_bgl  # noqa: F401
