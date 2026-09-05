"""Launch a fuzz target from a fresh process so its RSS excludes the coordinator."""
import json
import os
import subprocess
import sys


def main() -> None:
    receipt_fd = int(sys.argv[1])
    # Fork only after this interpreter has exec'd: Linux otherwise carries the
    # coordinator's resident memory into the target's getrusage high-water mark.
    child = subprocess.Popen(sys.argv[2:])
    _, status, usage = os.wait4(child.pid, 0)
    child.returncode = os.waitstatus_to_exitcode(status)
    receipt = json.dumps({"status": status, "maxrss": usage.ru_maxrss}).encode()
    os.write(receipt_fd, receipt)


if __name__ == "__main__":
    main()
