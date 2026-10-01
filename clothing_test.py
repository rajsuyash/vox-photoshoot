"""Clothing reaches paid-shoot prompts and reshoots; only a new local database.

ADMIN_TEST_DSN=postgresql://postgres@127.0.0.1:55439/postgres?sslmode=disable \
  .venv/bin/python clothing_test.py
"""
import dataclasses
import os
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import psycopg
from psycopg import sql
from fastapi.testclient import TestClient

import composition
import db
import locations
import product
import shoot


def main():
    required = {'western', 'indian', 'formal', 'semi-formal', 'ethnic'}
    styles = {'conservative', 'classic-timeless', 'bold', 'edgy', 'bohemian'}
    assert required <= set(composition.CLOTHING_CATEGORIES)
    assert styles == set(composition.DRESSING_STYLES)
    for category in product.CATEGORIES.values():
        base = locations.compose('p', category, 'm', 'kyoto')
        assert f'She wears a {locations.ALL["kyoto"].wardrobe}.' in base
        for clothing in composition.CLOTHING_CATEGORIES:
            for style in styles:
                comp = composition.parse({'clothing_category': clothing,
                                          'dressing_style': style}, category.key)
                prompt, args = shoot.build(['product'], 'aditi', 'kyoto', 'p',
                                           category, comp=comp, face_url='face')
                assert composition.CLOTHING_CATEGORIES[clothing] in prompt
                assert composition.DRESSING_STYLES[style] in prompt
                assert locations.ALL['kyoto'].wardrobe not in prompt
                assert category.craft in prompt and category.negative in prompt
                assert args['image_urls'] == ['product', 'face']
                assert composition.parse(dataclasses.asdict(comp), category.key) == comp
        assert composition.parse({}, category.key).clothing_category == ''
        assert composition.parse({'dressing_style': 'untrusted prose'}, category.key).dressing_style == ''

    base = os.environ.get('ADMIN_TEST_DSN', '')
    url = urlsplit(base)
    assert url.hostname in {'localhost', '127.0.0.1', '::1'}, 'local database only'
    name = 'vox_clothing_test_' + uuid.uuid4().hex[:12]
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    os.environ['DATABASE_URL'] = urlunsplit(url._replace(path='/' + name))
    try:
        db.migrate()
        import auth
        import credits
        import jobs
        import pieces
        import app as server

        uid = str(auth.create_user('clothing@example.com', 'local fixture password')['id'])
        wid = str(db.query("INSERT INTO workspaces(name) VALUES('Clothing check') RETURNING id", one=True)['id'])
        db.query("INSERT INTO memberships(user_id,workspace_id,role) VALUES(%s,%s,'owner')", (uid, wid))
        credits.grant(wid, 30)
        pieces.create('clothing-fixture', wid, uid, 'uploads/fixture.jpg', 'ring')
        client = TestClient(server.app)
        client.cookies.set(auth.COOKIE, auth.start_session(uid, wid))
        catalog = client.get('/api/composition?category=ring').json()
        assert {row['key'] for row in catalog['clothing_categories']} == set(composition.CLOTHING_CATEGORIES)
        assert {row['key'] for row in catalog['dressing_styles']} == styles
        body = {'piece_id': 'clothing-fixture', 'model_key': 'aditi',
                'location_key': 'kyoto', 'category': 'ring', 'description': 'gold ring',
                'clothing_category': 'indian', 'dressing_style': 'classic-timeless',
                'idempotency_key': str(uuid.uuid4())}
        captured = []

        def capture(job_id, root_id, path, model, location, description,
                    category, framings, options, comp, face):
            assert jobs.claim(job_id)
            captured.append(shoot.build(['product'], model, location, description,
                                        category, comp=comp, framing=framings[0])[0])
            assert jobs.finish(job_id, 'succeeded')

        with patch.object(server, 'piece_path', return_value=Path('/unused-fixture.jpg')), \
                patch.object(server, 'run_shoot', side_effect=capture):
            for field in ('clothing_category', 'dressing_style'):
                bad = client.post('/api/shoots', data={**body, field: 'untrusted prose'})
                assert bad.status_code == 400, bad.text
            assert credits.balance(wid) == 30
            assert db.query('SELECT count(*) AS n FROM jobs', one=True)['n'] == 0
            response = client.post('/api/shoots', data=body)
            assert response.status_code == 200, response.text
            jid = response.json()['job_id']
            chosen = jobs.get(jid, wid)['params']['composition']
            assert chosen['clothing_category'] == 'indian'
            assert chosen['dressing_style'] == 'classic-timeless'
            assert client.post('/api/shoots', data=body).json()['job_id'] == jid
            assert credits.balance(wid) == 27 and len(captured) == 1
            response = client.post(f'/api/shoots/{jid}/reshoot',
                                   params={'framing': 'hero', 'idempotency_key': str(uuid.uuid4())})
            assert response.status_code == 200, response.text
            assert jobs.get(response.json()['reshoot_id'], wid)['params']['composition'] == chosen
            assert len(captured) == 2 and captured[0] == captured[1]
            assert composition.CLOTHING_CATEGORIES['indian'] in captured[1]
        print('clothing ok: all choices reach prompts, defaults unchanged, invalid input costs nothing, reshoots inherit')
    finally:
        db.close()
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
