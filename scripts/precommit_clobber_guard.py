#!/usr/bin/env python3
"""Pre-commit guard against stale-write clobbers / accidental reversions.

Born from an incident where a subagent overwrote 5 files with older in-context
copies (dropping whole functions), and several were committed before anyone
noticed. This blocks a commit when a staged change looks like a clobber:

  * a file with a large NET DELETION (lost far more than it gained), or
  * a Python file that removes >=2 top-level def/class (lost functions), or
  * a staged Python file that no longer compiles.

Intentional large refactors/deletions are fine — just re-run with
`git commit --no-verify` to bypass.

Installed as .git/hooks/pre-commit (calls this). Kept in-repo so it's recoverable.
"""
import subprocess
import sys

NET_DELETE_FLOOR = 40      # ignore small diffs
NET_DELETE_RATIO = 1.5     # deletions must exceed additions by this factor
DEF_LOSS_FLOOR   = 2       # removing this many top-level defs/classes is suspicious


def _run(args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def main():
    numstat = _run(['git', 'diff', '--cached', '--numstat'])
    problems = []
    for line in numstat.splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        add_s, del_s, path = parts
        if add_s == '-' or del_s == '-':   # binary
            continue
        add, dele = int(add_s), int(del_s)
        if dele > NET_DELETE_FLOOR and dele > add * NET_DELETE_RATIO:
            problems.append(f"{path}: +{add} -{dele} (net -{dele - add}) — large net deletion, possible clobber/revert")
        if path.endswith('.py'):
            diff = _run(['git', 'diff', '--cached', '--', path])
            removed = sum(1 for l in diff.splitlines() if l.startswith('-def ') or l.startswith('-class '))
            added = sum(1 for l in diff.splitlines() if l.startswith('+def ') or l.startswith('+class '))
            if removed - added >= DEF_LOSS_FLOOR:
                problems.append(f"{path}: removes {removed - added} top-level def/class — possible clobber")
            comp = subprocess.run([sys.executable, '-m', 'py_compile', path])
            if comp.returncode != 0:
                problems.append(f"{path}: SYNTAX ERROR — does not compile")

    if problems:
        sys.stderr.write("\n⛔ commit blocked — possible stale-write clobber / regression:\n")
        for p in problems:
            sys.stderr.write("   - " + p + "\n")
        sys.stderr.write("\nIf this is intentional (real refactor/deletion), re-run with:\n"
                         "   git commit --no-verify\n\n")
        sys.exit(1)
    sys.exit(0)


if __name__ == '__main__':
    main()
