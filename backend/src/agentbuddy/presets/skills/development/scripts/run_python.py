import runpy
import sys
from pathlib import Path

root = Path.cwd().resolve()
target = (root / sys.argv[1]).resolve()
if not target.is_relative_to(root) or target.suffix != ".py" or not target.is_file():
    raise ValueError("Expected an existing Python file inside the project")
sys.argv = [str(target), *sys.argv[2:]]
sys.path.insert(0, str(target.parent))
runpy.run_path(str(target), run_name="__main__")
