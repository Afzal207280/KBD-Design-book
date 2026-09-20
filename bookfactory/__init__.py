"""KDP Book Factory — autonomous, verified book production pipeline.

Architecture (spec §196): UI / orchestration / AI services / content /
design / render / validation / repair / storage / export / testing are kept
in separate modules. Deterministic code decides deterministic facts (§202).
"""

__version__ = "1.0.0"
APP_NAME = "KDP Book Factory"
