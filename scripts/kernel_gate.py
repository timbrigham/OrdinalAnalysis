#!/usr/bin/env python3
"""Independent-kernel gate.

Exports named declarations, together with their whole dependency closure, with
lean4export, and checks each export with nanoda, an independent implementation of
Lean's kernel, under an explicit list of permitted axioms.

Usage:
    python scripts/kernel_gate.py scripts/kernel_gate.json

The binaries are taken from the environment:
    LEAN4EXPORT   path to the lean4export executable (built at this project's toolchain)
    NANODA        path to nanoda_bin

Each group in the config names a module, the declarations to export, the axioms
nanoda may admit, and the expected outcome.  A group with "expect": "fail" is a
control: it must be rejected, which shows the gate can fail.  The gate fails closed:
a group passes only on exit code 0 together with nanoda's own success line, and any
outcome other than the expected one fails the run.

Exit code 0 = every group met its expectation, 1 = otherwise.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

SUCCESS_MARK = "with no typechecker errors"


def run_group(group, lean4export, nanoda, out_dir):
    name = group["name"]
    export_path = out_dir / f"{name}.ndjson"
    t0 = time.monotonic()
    with open(export_path, "wb") as fh:
        exp = subprocess.run(
            ["lake", "env", lean4export, group["module"], "--", *group["declarations"]],
            stdout=fh, stderr=subprocess.PIPE)
    t_export = time.monotonic() - t0
    if exp.returncode != 0:
        return {"name": name, "outcome": "error",
                "detail": "lean4export failed: " + exp.stderr.decode(errors="replace")[-2000:]}

    config = {
        "export_file_path": export_path.resolve().as_posix(),
        "use_stdin": False,
        "permitted_axioms": group["permitted_axioms"],
        "unpermitted_axiom_hard_error": False,
        "nat_extension": True,
        "string_extension": True,
        "print_success_message": True,
    }
    config_path = out_dir / f"{name}.nanoda.json"
    config_path.write_text(json.dumps(config, indent=2))

    t1 = time.monotonic()
    chk = subprocess.run([nanoda, str(config_path.resolve())],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    t_check = time.monotonic() - t1
    log = chk.stdout.decode(errors="replace")
    passed = chk.returncode == 0 and SUCCESS_MARK in log
    outcome = "pass" if passed else "fail"
    summary = next((ln for ln in log.splitlines() if ln.startswith("Checked")), "")
    if not passed:
        lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
        cause = [ln for ln in lines if "panicked" in ln or "rror" in ln or "not found" in ln]
        summary = f"exit {chk.returncode}: " + (" / ".join(cause[:2]) if cause
                                                 else (lines[-1] if lines else "no output"))
    return {"name": name, "outcome": outcome, "exit": chk.returncode,
            "export_mb": export_path.stat().st_size / 1e6,
            "export_s": t_export, "check_s": t_check, "detail": summary}


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    lean4export = os.environ.get("LEAN4EXPORT")
    nanoda = os.environ.get("NANODA")
    if not lean4export or not nanoda:
        print("LEAN4EXPORT and NANODA must be set")
        return 2
    groups = json.loads(Path(sys.argv[1]).read_text())["groups"]
    out_dir = Path(os.environ.get("KERNEL_GATE_OUT", ".lake/kernel-gate"))
    out_dir.mkdir(parents=True, exist_ok=True)

    rows, ok = [], True
    for group in groups:
        res = run_group(group, lean4export, nanoda, out_dir)
        met = res["outcome"] == group["expect"]
        ok &= met
        rows.append((group, res, met))
        print(f"[{'OK' if met else 'FAILED'}] {group['name']}: expected {group['expect']}, "
              f"got {res['outcome']} -- {res['detail']}", flush=True)

    lines = ["## Independent kernel (nanoda)", "",
             "| group | declarations | permitted axioms | expected | got | export | check |",
             "|---|---|---|---|---|---|---|"]
    for group, res, met in rows:
        size = f"{res['export_mb']:.0f} MB, {res['export_s']:.0f} s" if "export_mb" in res else "-"
        check = f"{res['check_s']:.0f} s" if "check_s" in res else "-"
        lines.append(f"| {group['name']} | {len(group['declarations'])} | "
                     f"{', '.join(group['permitted_axioms']) or '(none)'} | {group['expect']} | "
                     f"{res['outcome']}{'' if met else ' **MISMATCH**'} | {size} | {check} |")
    lines += ["", "**Result: " + ("all groups met their expectation" if ok else "FAILED") + "**"]
    report = "\n".join(lines)
    print(report)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
