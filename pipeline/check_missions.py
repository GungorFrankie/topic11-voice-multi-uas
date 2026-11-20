#!/usr/bin/env python3
"""
check_missions.py - test the Mission JSON schema (Task 2).

It checks every example mission against mission_schema.json and compares the
result with what we expect:
  examples/valid/            must PASS the schema
  examples/schema_errors/    must FAIL the schema  (wrong form)
  examples/semantic_errors/  must PASS the schema  (form is right, meaning is wrong -
                             these are the cases the Task 4 validators have to catch)

Run:   python3 check_missions.py            (all examples)
       python3 check_missions.py my.json    (one mission of your own)
Needs: sudo apt install -y python3-jsonschema
"""
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
SCHEMA = json.loads((HERE / "mission_schema.json").read_text())
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)


def schema_errors(mission):
    """Return a list of short, readable error sentences (empty list = schema-valid)."""
    mission = {k: v for k, v in mission.items() if not k.startswith("_")}   # ignore our own notes
    out = []
    for e in sorted(VALIDATOR.iter_errors(mission), key=lambda e: list(e.absolute_path)):
        where = "mission"
        path = list(e.absolute_path)
        if len(path) >= 2 and path[0] == "tasks":
            task = mission["tasks"][path[1]]
            where = f"task {task.get('id', '#' + str(path[1] + 1))}" if isinstance(task, dict) else f"task #{path[1] + 1}"
            if len(path) >= 3:
                where += f", field '{path[2]}'"
        if e.validator == "not" and isinstance(e.instance, dict):     # a field that this action must not have
            extra = [k for sub in e.validator_value.get("anyOf", []) for k in sub.get("required", []) if k in e.instance]
            out.append(f"{where}: action '{e.instance.get('action')}' must not have the field(s) {', '.join(extra)}")
        else:
            out.append(f"{where}: {e.message[:110]}")
    return out


def main():
    if len(sys.argv) > 1:                                   # check one file given by the user
        errors = schema_errors(json.loads(Path(sys.argv[1]).read_text()))
        print("schema-valid" if not errors else "NOT schema-valid:\n  " + "\n  ".join(errors))
        return 0 if not errors else 1

    groups = [("valid", True), ("schema_errors", False), ("semantic_errors", True)]
    wrong = 0
    for folder, should_pass in groups:
        print(f"\n{folder}  (expected: {'pass' if should_pass else 'fail'} the schema)")
        for f in sorted((HERE / "examples" / folder).glob("*.json")):
            mission = json.loads(f.read_text())
            errors = schema_errors(mission)
            passed = not errors
            ok = passed == should_pass
            wrong += not ok
            note = mission.get("_expect", "")
            print(f"  [{'ok' if ok else 'WRONG'}] {f.name:38s} {'pass' if passed else 'fail'}"
                  + (f"   <- {errors[0]}" if errors else "") + (f"   (meaning error: {note})" if passed and note else ""))
    print(f"\n{'All examples behave as expected.' if not wrong else str(wrong) + ' example(s) did NOT behave as expected.'}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
