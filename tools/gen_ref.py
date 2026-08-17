"""Render docs/higgsfield-api.md from the Higgsfield OpenAPI spec."""

import json
import pathlib

SPEC = json.loads((pathlib.Path(__file__).resolve().parent / 'hf-openapi.json').read_text())
SCHEMAS = SPEC.get('components', {}).get('schemas', {})

# Live probe data: /estimate responses per endpoint, and real enum values where the
# published spec drifted from the deployed API.
PROBE = {r['path']: r for r in json.loads((pathlib.Path(__file__).resolve().parent / 'probe-results.json').read_text())}
ENUMS = json.loads((pathlib.Path(__file__).resolve().parent / 'enum-findings.json').read_text())

AVAILABILITY = {
    200: 'available',
    404: 'not on this account',
    423: 'blocked',
}


def deref(node):
    while isinstance(node, dict) and '$ref' in node:
        node = SCHEMAS[node['$ref'].rsplit('/', 1)[-1]]
    return node


def type_of(schema):
    schema = deref(schema)
    if 'enum' in schema:
        return ' \\| '.join(f'`{v}`' for v in schema['enum'])
    if 'anyOf' in schema or 'oneOf' in schema:
        parts = [type_of(s) for s in schema.get('anyOf') or schema.get('oneOf')]
        return ' \\| '.join(dict.fromkeys(p for p in parts if p != 'null'))
    kind = schema.get('type', 'any')
    if kind == 'array':
        return f'{type_of(schema.get("items", {}))}[]'
    if kind == 'object' and schema.get('properties'):
        return 'object{' + ', '.join(schema['properties']) + '}'
    return kind


def constraints(schema):
    schema = deref(schema)
    bits = []
    for key, label in (('minimum', 'min'), ('maximum', 'max'),
                       ('minLength', 'minLen'), ('maxLength', 'maxLen')):
        if key in schema:
            bits.append(f'{label} {schema[key]}')
    if 'default' in schema:
        bits.append(f'default `{schema["default"]}`')
    return ', '.join(bits)


def body_schema(operation):
    content = operation.get('requestBody', {}).get('content', {})
    return deref(content.get('application/json', {}).get('schema', {}))


lines = [
    '# Higgsfield API — endpoint reference',
    '',
    f'Generated from `https://docs.higgsfield.ai/docs/openapi.json` — spec version '
    f'{SPEC["info"].get("version")}.',
    '',
    f'**Base URL:** `{SPEC["servers"][0]["url"]}`',
    '**Auth:** `Authorization: Key <HF_KEY_ID>:<HF_KEY_SECRET>` on every call.',
    '',
    'Every generation POST is asynchronous: it returns `{status, request_id, status_url, '
    'cancel_url}` immediately. Poll `status_url` or pass `?hf_webhook=<https-url>`.',
    '',
    '## Request management',
    '',
    '| Method | Path | Purpose |',
    '| --- | --- | --- |',
    '| GET | `/requests/{request_id}/status` | Current state + output URLs |',
    '| POST | `/requests/{request_id}/cancel` | Cancel while still `queued` → `202`, empty body |',
    '| POST | `/files/generate-upload-url` | Presigned upload; body `{"content_type": "image/jpeg"}` |',
    '| POST | `/estimate/<model-path>` | Cost preview with the same body → `{"credits", "usd"}` |',
    '',
    'Terminal statuses: `completed`, `failed`, `nsfw`, `canceled`. '
    '`failed`/`nsfw`/`canceled` are not charged. Output URLs live at least 7 days.',
    '',
    '## Available on this account',
    '',
    'Verified by calling `/estimate/<path>` on the live account — endpoints absent from '
    'the account return `404 model_not_found`, disabled ones `423 model_blocked`. '
    'Credits below are the baseline (minimum required params, spec defaults); cost rises '
    'with duration, resolution, and batch size.',
    '',
    '| Endpoint | Credits |',
    '| --- | ---: |',
] + [
    f'| `{r["path"]}` | {r["credits"]} |'
    for r in sorted(
        (r for r in PROBE.values() if r['http'] == 200),
        key=lambda r: float(r['credits']),
    )
] + [
    '',
    'Unavailable: '
    + ', '.join(f'`{p}`' for p, r in sorted(PROBE.items()) if r['http'] == 404)
    + '.',
    '',
    'Blocked: '
    + ', '.join(f'`{p}`' for p, r in sorted(PROBE.items()) if r['http'] == 423)
    + '.',
    '',
    '## Generation endpoints',
    '',
]

by_tag: dict[str, list] = {}
for path, operations in SPEC['paths'].items():
    for method, operation in operations.items():
        if method != 'post' or path.startswith('/requests/'):
            continue
        by_tag.setdefault((operation.get('tags') or ['Other'])[0], []).append((path, operation))

for tag in sorted(by_tag):
    lines.append(f'### {tag}')
    lines.append('')
    for path, operation in sorted(by_tag[tag]):
        schema = body_schema(operation)
        required = set(schema.get('required', []))
        probe = PROBE.get(path, {})
        state = AVAILABILITY.get(probe.get('http'), f'http {probe.get("http")}')
        cost = f' — baseline **{probe["credits"]} credits**' if probe.get('credits') else ''
        lines.append(f'#### `POST {path}`')
        lines.append('')
        lines.append(f'`{state}`{cost}')
        lines.append('')
        properties = schema.get('properties', {})
        if not properties:
            lines.append('_No documented body parameters._')
            lines.append('')
            continue
        lines.append('| Param | Type | Req | Notes |')
        lines.append('| --- | --- | :-: | --- |')
        for name, prop in properties.items():
            finding = ENUMS.get(path, {}).get(name, {})
            if finding.get('drift'):
                # The spec's default belongs to the stale enum, so it is dropped rather
                # than shown alongside values it is not a member of.
                rendered = ' \\| '.join(f'`{v}`' for v in finding['live'])
                note = ('**spec is wrong** — live values shown, '
                        f'spec claimed {", ".join(f"`{v}`" for v in finding["spec"])}')
            else:
                rendered, note = type_of(prop), constraints(prop)
            lines.append(
                f'| `{name}` | {rendered} | {"yes" if name in required else ""} | {note} |'
            )
        lines.append('')

out = pathlib.Path(__file__).resolve().parent.parent / 'docs/higgsfield-api.md'
out.write_text('\n'.join(lines) + '\n')
print(f'{out} — {len(out.read_text().splitlines())} lines, '
      f'{sum(len(v) for v in by_tag.values())} generation endpoints')
