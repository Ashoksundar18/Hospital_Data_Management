import os
import pytest

# Default ALLOW_DEV_KEYS=1 for local pytest test suite runs
os.environ.setdefault("ALLOW_DEV_KEYS", "1")
