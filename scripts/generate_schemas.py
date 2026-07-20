"""Write or verify the committed Agent IQ JSON Schema publication set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_iq.contracts.schemas import generate_contract_schemas

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "schemas" / "v1"


def render(schema: dict[str, object]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def write_schemas(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for filename, schema in generate_contract_schemas().items():
        (output / filename).write_text(render(schema), encoding="utf-8")


def check_schemas(output: Path) -> int:
    expected = generate_contract_schemas()
    expected_names = set(expected)
    actual_names = (
        {path.name for path in output.glob("*.schema.json")} if output.exists() else set()
    )
    failures: list[str] = []

    for filename, schema in expected.items():
        path = output / filename
        if not path.exists():
            failures.append(f"missing {path.relative_to(ROOT)}")
        elif path.read_text(encoding="utf-8") != render(schema):
            failures.append(f"stale {path.relative_to(ROOT)}")

    for filename in sorted(actual_names - expected_names):
        failures.append(f"unexpected {(output / filename).relative_to(ROOT)}")

    if failures:
        print("JSON Schema publication check failed:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"JSON Schema publication: PASS ({len(expected)} schemas)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    output = args.output.resolve()
    if args.check:
        return check_schemas(output)
    write_schemas(output)
    print(f"Wrote {len(generate_contract_schemas())} schemas to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
