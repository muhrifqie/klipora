"""Run every engine test (engine/tests/test_*.py + legacy engine/test_*.py), each in its own process.

  python engine/tests/run_all.py            all tests, offline (no GPU job, no Grok quota)
  python engine/tests/run_all.py media ai   only tests whose name contains one of the words
  extra flags are passed through (e.g. --gpu, --live)
Exit code 1 when any test fails. Prints one line per test with its time.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent


def main():
    words = [a for a in sys.argv[1:] if not a.startswith("-")]
    flags = [a for a in sys.argv[1:] if a.startswith("-")]
    tests = sorted(HERE.glob("test_*.py")) + sorted(ENGINE.glob("test_*.py"))
    if words:
        tests = [t for t in tests if any(w in t.stem for w in words)]
    failed = []
    t_all = time.time()
    for t in tests:
        t0 = time.time()
        try:
            r = subprocess.run([sys.executable, "-X", "utf8", str(t), *flags], cwd=str(t.parent), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=900)
            ok = r.returncode == 0
            tail = (r.stdout.strip().splitlines() or [""])[-1]
            err = (r.stderr or "").strip().splitlines()[-6:]
        except subprocess.TimeoutExpired:
            ok, tail, err = False, "", ["TIMEOUT after 900 s"]
        rel = t.relative_to(ENGINE)
        print(f"{'PASS' if ok else 'FAIL'}  {time.time() - t0:6.1f}s  {rel}  {tail[:110]}", flush=True)
        if not ok:
            failed.append(rel)
            for ln in err:
                print("      " + ln)
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed in {time.time() - t_all:.1f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
