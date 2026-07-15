import sys
from pathlib import Path

# Make repo-root scrape_scores.py importable from tests
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
