"""For endpoints live on this account, discover the REAL enum values.

Sends a deliberately invalid value for each enum param; the 400 response names the
accepted set. Estimate-only, so no credits are spent.
"""

import json
import pathlib
import re

import probe  # reuse post(), minimal_body(), deref(), SPEC

SENTINEL = '__invalid__'
ENUM_ERROR = re.compile(r"^(\w+): '.*?' is not one of \[(.*)\]$")


def enum_params(operation):
    schema = probe.deref(operation['requestBody']['content']['application/json']['schema'])
    return {
        name: probe.deref(prop)['enum']
        for name, prop in schema.get('properties', {}).items()
        if 'enum' in probe.deref(prop)
    }


def main() -> None:
    import os

    key = os.environ['HF_KEY']
    live = [
        r['path'] for r in json.loads((pathlib.Path(__file__).resolve().parent / 'probe-results.json').read_text())
        if r['http'] == 200
    ]

    findings = {}
    for path in live:
        operation = probe.SPEC['paths'][path]['post']
        base = probe.minimal_body(operation)
        for name, spec_values in enum_params(operation).items():
            status, body = probe.post(path, {**base, name: SENTINEL}, key)
            detail = body.get('detail')
            match = ENUM_ERROR.match(detail) if isinstance(detail, str) else None
            if not match:
                findings.setdefault(path, {})[name] = {'spec': spec_values, 'live': None,
                                                       'raw': str(detail)[:120]}
                continue
            actual = [v.strip().strip("'\"") for v in match.group(2).split(',')]
            spec_normalized = [str(v) for v in spec_values]
            findings.setdefault(path, {})[name] = {
                'spec': spec_normalized,
                'live': actual,
                'drift': spec_normalized != actual,
            }
            flag = 'DRIFT' if spec_normalized != actual else 'ok   '
            print(f'{flag} {path} :: {name}')
            if spec_normalized != actual:
                print(f'        spec: {spec_normalized}')
                print(f'        live: {actual}')

    pathlib.Path(__file__).resolve().parent / 'enum-findings.json'.write_text(json.dumps(findings, indent=1))
    drifted = sum(1 for params in findings.values()
                  for f in params.values() if f.get('drift'))
    print(f'\n{drifted} drifted params across {len(findings)} endpoints')


if __name__ == '__main__':
    main()
