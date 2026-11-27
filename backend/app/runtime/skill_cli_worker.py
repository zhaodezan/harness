"""Run a verified Skill entry point in the supervised local Python process."""
import json
import os
from pathlib import Path
import pkgutil  # Preload runpy's lazy dependency before adding the Skill directory.
import runpy
import sys


ERROR_PREFIX = 'HARNESS_SKILL_CLI_ERROR:'


def main():
    payload = json.load(sys.stdin)
    script = Path(payload['script'])
    os.chdir(payload['directory'])
    sys.argv = [str(script), *payload['argv']]
    # Isolated mode excludes the working directory and user site. Add only the
    # installed entry point's directory, so its sibling modules can be imported.
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name='__main__')
    except ImportError:
        category = 'import'
    except SyntaxError:
        category = 'syntax'
    except PermissionError:
        category = 'permission'
    except Exception:
        category = 'exception'
    else:
        return 0
    print(f"\n{ERROR_PREFIX}{payload['_result_token']}:{category}", file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
