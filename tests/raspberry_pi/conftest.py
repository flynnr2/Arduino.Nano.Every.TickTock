"""Keep the Pi receiver independent of the analysis package's installation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'Raspberry.Pi'))
