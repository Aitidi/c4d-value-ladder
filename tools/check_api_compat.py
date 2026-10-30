"""Check that a Cinema 4D build has every c4d/maxon name ValueLadder uses.

Run with that build's c4dpy:  c4dpy.exe tools/check_api_compat.py
It imports the plugin module (nothing is registered outside __main__) and
resolves each dotted c4d.* / maxon.* reference found in its source.
"""
import importlib.machinery
import importlib.util
import os
import re
import sys

import c4d
import maxon

PYP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "ValueLadder", "ValueLadder.pyp")


def main():
    print("Cinema 4D", c4d.GetC4DVersion(), "Python", sys.version.split()[0])
    loader = importlib.machinery.SourceFileLoader("valueladder_compat", os.path.normpath(PYP))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    print("module loaded, version", module.__version__)
    source = open(PYP, encoding="utf-8").read()
    missing = []
    for ref in sorted(set(re.findall(r"(?<![\w./])(?:c4d|maxon)(?:\.[A-Za-z_][A-Za-z0-9_]*)+", source))):
        obj = {"c4d": c4d, "maxon": maxon}[ref.split(".")[0]]
        for part in ref.split(".")[1:]:
            try:
                obj = getattr(obj, part)
            except Exception:
                missing.append(ref)
                break
    print("missing:", missing or "none")


main()
