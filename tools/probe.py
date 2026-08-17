"""Probe every generation endpoint via /estimate to learn live availability + real enums.

Estimate does not generate anything, so this costs no credits.
"""

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

BASE = 'https://platform.higgsfield.ai'
SPEC = json.loads((pathlib.Path(__file__).resolve().parent / 'hf-openapi.json').read_text())
SCHEMAS = SPEC['components']['schemas']
SAMPLE_IMAGE = 'https://cdn.higgsfield.ai/sample.jpg'


def deref(node):
    while isinstance(node, dict) and '$ref' in node:
        node = SCHEMAS[node['$ref'].rsplit('/', 1)[-1]]
    return node


def sample_value(name, schema):
    schema = deref(schema)
    if 'default' in schema and schema['default'] is not None:
        return schema['default']
    if 'enum' in schema:
        return schema['enum'][0]
    kind = schema.get('type')
    if kind == 'string':
        return SAMPLE_IMAGE if name.endswith('_url') else 'a calm editorial portrait'
    if kind == 'integer':
        return schema.get('minimum', 1)
    if kind == 'number':
        return schema.get('minimum', 0.5)
    if kind == 'boolean':
        return False
    if kind == 'array':
        return [SAMPLE_IMAGE] if name.endswith('_urls') else []
    return None


def minimal_body(operation):
    schema = deref(operation['requestBody']['content']['application/json']['schema'])
    properties = schema.get('properties', {})
    return {
        name: sample_value(name, properties[name])
        for name in schema.get('required', [])
        if name in properties
    }


def post(path, body, key):
    request = urllib.request.Request(
        f'{BASE}/estimate{path}',
        data=json.dumps(body).encode(),
        headers={
            'Authorization': f'Key {key}',
            'Content-Type': 'application/json',
            # Cloudflare rejects urllib's default UA with "error code: 1010"
            'User-Agent': 'curl/8.7.1',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw or b'{}')
        except json.JSONDecodeError:
            return error.code, {'detail': raw.decode(errors='replace')[:120]}
    except Exception as error:  # network-level
        return 0, {'detail': str(error)}


def main() -> None:
    key = os.environ.get('HF_KEY')
    if not key:
        sys.exit('HF_KEY not set')

    paths = sorted(
        path for path, ops in SPEC['paths'].items()
        if 'post' in ops and not path.startswith('/requests/')
    )
    results = []
    for path in paths:
        operation = SPEC['paths'][path]['post']
        status, body = post(path, minimal_body(operation), key)
        detail = body.get('detail') if isinstance(body, dict) else body
        results.append({
            'path': path,
            'http': status,
            'credits': body.get('credits') if isinstance(body, dict) else None,
            'usd': body.get('usd') if isinstance(body, dict) else None,
            'detail': detail if not isinstance(detail, list) else json.dumps(detail)[:160],
        })
        print(f'{status:4} {path:60} {results[-1]["credits"] or ""} {results[-1]["detail"] or ""}'[:180])

    pathlib.Path(__file__).resolve().parent / 'probe-results.json'.write_text(json.dumps(results, indent=1))


if __name__ == '__main__':
    main()
