import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def done(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeRunner:
    """Stands in for subprocess.run: records every command and answers from
    rules, first match wins. A rule is (predicate on args, result), where
    the result is a CompletedProcess or a function of args returning one."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.calls = []

    def add(self, predicate, result):
        self.rules.insert(0, (predicate, result))

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        for predicate, result in self.rules:
            if predicate(args):
                return result(args) if callable(result) else result
        return done()

    def calls_with(self, *words):
        return [call for call in self.calls if all(word in call for word in words)]
