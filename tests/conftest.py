import os
import sys
from pathlib import Path

# Make the muzikoo package importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Settings are read (and cached) on the first get_settings() call, so the test
# key has to be in the environment before any muzikoo module is imported.
TEST_API_KEY = "test-api-key"
os.environ["API_KEYS"] = f"{TEST_API_KEY},second-valid-key"
