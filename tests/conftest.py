"""Hypothesis profiles. `default` keeps the suite fast; `deep` is for a long search:

uv run pytest tests/test_kernel_props.py --hypothesis-profile=deep
"""

from hypothesis import settings

settings.register_profile("default", max_examples=600, deadline=None)
settings.register_profile("deep", max_examples=20_000, deadline=None)
