"""Make installed pkg-config metadata independent of its build runner path."""

from pathlib import Path
import re
import sys


def relocate(prefix):
    files = list((prefix / "lib/pkgconfig").glob("*.pc"))
    if not files:
        raise ValueError(f"no pkg-config files installed under {prefix}")
    for path in files:
        text, count = re.subn(r"^prefix=.*$", "prefix=${pcfiledir}/../..",
                             path.read_text(), flags=re.MULTILINE)
        if count != 1:
            raise ValueError(f"expected one prefix in {path}")
        path.write_text(text)


if __name__ == "__main__":
    relocate(Path(sys.argv[1]))
