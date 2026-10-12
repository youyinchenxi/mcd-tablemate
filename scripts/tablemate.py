"""Run the bundled Skill from any current directory."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from maimai_team.cli import main

raise SystemExit(main())
