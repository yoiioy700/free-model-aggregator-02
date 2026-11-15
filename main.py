"""Command-line entrypoint for the free model aggregator."""

import json
import os
import sys
import traceback

# The trial's -P flag keeps the script directory off the import path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aggregator.brief import BRIEF, CATALOG
from aggregator import server
from aggregator.server import log


def start() -> None:
    if BRIEF['error'] is not None:
        log(BRIEF['error'])
    if '--list' in sys.argv[1:]:
        # The same document the register body carries, byte for byte: print the one built object and stop
        # before any server starts. A process whose stdout is not flushed loses the tail, so flush it.
        sys.stdout.write(json.dumps(CATALOG, separators=(',', ':'), ensure_ascii=False) + '\n')
        sys.stdout.flush()
        return
    server.start()


def main() -> int:
    try:
        start()
        return 0
    except SystemExit:
        raise
    except BaseException:  # Node's top-level catch: log the stack and exit 1
        log(traceback.format_exc())
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
