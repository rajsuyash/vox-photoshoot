"""Real API/DB/credit checks; only paid calls are stubbed. Requires a throwaway DB.

DATABASE_URL=postgresql://postgres@127.0.0.1:55439/donna_campaign_test?sslmode=disable \
    .venv/bin/python campaign_test.py
"""
import io
import os
import uuid
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

test_dsn = urlparse(os.environ.get('DATABASE_URL', ''))
if test_dsn.hostname not in {'localhost', '127.0.0.1'} or test_dsn.path != '/donna_campaign_test':
    raise RuntimeError('campaign checks require the throwaway local donna_campaign_test database')
os.environ.pop('S3_BUCKET', None)

from fastapi.testclient import TestClient
from PIL import Image

import app
import auth
import campaign
import credits
import db
import jobs
import storage
import talent


def main():
    db.migrate()
    uid = str(db.query('INSERT INTO users(email,password_hash) VALUES(%s,%s) RETURNING id',
                       (f'{uuid.uuid4()}@campaign.test', 'unused'), one=True)['id'])
    wid = str(db.query("INSERT INTO workspaces(name) VALUES('Campaign check') RETURNING id",
                       one=True)['id'])
    db.query("INSERT INTO memberships VALUES(%s,%s,'owner',now())", (uid, wid))
    token = auth.start_session(uid, wid)
    credits.grant(wid, 4)
    image = io.BytesIO()
    Image.new('RGB', (80, 100), 'gold').save(image, 'PNG')
    photo = image.getvalue()
    params = dict(occasion='Diwali', audience='Indian couples', cta='Shop the collection',
                  aspect='4:5', idempotency_key=str(uuid.uuid4()))
    calls = []

    def fake_generate(source, brief, directory, progress):
        calls.append(brief)
        progress()
        path = directory / 'campaign.png'
        path.write_bytes(photo)
        return path

    with TestClient(app.app) as client, patch.object(campaign, 'generate', fake_generate):
        assert client.get('/api/campaign-options').status_code == 401
        client.cookies.set(auth.COOKIE, token)
        assert client.get('/api/campaign-options').json()['model'] == 'GPT Image 2'

        def submit(data=params, content=photo, extra_files=None):
            return client.post('/api/marketing-campaigns', data=data,
                               files={'upload': ('ring.png', content, 'image/png'),
                                      **(extra_files or {})})

        invalid = submit({**params, 'occasion': ' '})
        assert invalid.status_code == 422, (invalid.status_code, invalid.text)
        assert submit(content=b'not an image').status_code == 422
        assert submit(content=b'x' * (app.MAX_UPLOAD_BYTES + 1)).status_code == 413
        assert submit({**params, 'aspect': '30:1'}).status_code == 422
        assert credits.balance(wid) == 4 and not calls
        response = submit()
        assert response.status_code == 200, response.text
        jid = response.json()['job_id']
        assert response.json()['created'] and credits.balance(wid) == 3
        assert submit().json() == {'job_id': jid, 'created': False}
        assert len(calls) == 1 and credits.balance(wid) == 3
        # Jobs made by the first deployed campaign version have no optional fields.
        db.query("UPDATE jobs SET params=params-'person_mode'-'person_key'-'person_digest'"
                 "-'logo_digest'-'brand_name'-'logo_mode' WHERE id=%s", (jid,))
        assert submit().json() == {'job_id': jid, 'created': False}
        assert submit({**params, 'cta': 'Another CTA'}).status_code == 409
        alternate = io.BytesIO()
        Image.new('RGB', (80, 100), 'red').save(alternate, 'PNG')
        assert submit(content=alternate.getvalue()).status_code == 409
        result = client.get(f'/api/marketing-campaigns/{jid}').json()
        assert result['status'] == 'succeeded' and len(result['images']) == 1
        assert client.get(result['images'][0]['download']).status_code == 200
        assert client.get('/api/marketing-campaigns').json()[0]['job_id'] == jid
        assert any(row['job_id'] == jid for row in client.get('/api/history?q=Diwali').json())
        assert client.get('/api/marketing-campaigns/not-a-uuid').status_code == 422

        other = str(db.query("INSERT INTO workspaces(name) VALUES('Other check') RETURNING id",
                             one=True)['id'])
        client.cookies.set(auth.COOKIE, auth.start_session(uid, other))
        assert client.get(f'/api/marketing-campaigns/{jid}').status_code == 404
        assert client.get(f'/api/images/{jid}/campaign').status_code == 404
        assert client.get('/api/marketing-campaigns').json() == []
        assert submit().status_code == 402
        assert not db.query('SELECT id FROM jobs WHERE workspace_id=%s', (other,))
        client.cookies.set(auth.COOKIE, token)
        with patch.object(campaign, 'generate', side_effect=RuntimeError('test failure')):
            failed_id = submit({**params, 'idempotency_key': str(uuid.uuid4())}).json()['job_id']
        failed = client.get(f'/api/marketing-campaigns/{failed_id}').json()
        assert failed['status'] == 'failed' and not failed['images']
        assert credits.balance(wid) == 3
        assert failed['settled_credits'] == 0

        # A worker reaped during generation cannot publish an image after refund.
        def reaped(source, brief, directory, progress):
            active = db.query("SELECT id FROM jobs WHERE workspace_id=%s AND status='running'",
                              (wid,), one=True)
            jobs.finish(str(active['id']), 'failed', settled_credits=0)
            credits.settle(str(active['id']), 0)
            return fake_generate(source, brief, directory, progress)

        with patch.object(campaign, 'generate', reaped):
            stale_id = submit({**params, 'idempotency_key': str(uuid.uuid4())}).json()['job_id']
        assert jobs.image_count(stale_id) == 0 and credits.balance(wid) == 3

        with patch.object(app.campaign, 'run'):
            queued_id = submit({**params, 'idempotency_key': str(uuid.uuid4())}).json()['job_id']
        assert credits.balance(wid) == 2
        db.query("UPDATE jobs SET created_at=now()-interval '11 minutes' WHERE id=%s", (queued_id,))
        with patch.object(credits, '_append', side_effect=RuntimeError('refund write failed')):
            try:
                campaign.recover()
                assert False, 'refund write failure was hidden'
            except RuntimeError:
                pass
        assert jobs.get(queued_id, wid)['status'] == 'queued' and credits.balance(wid) == 2
        campaign.recover()
        campaign.recover()
        assert jobs.get(queued_id, wid)['status'] == 'failed' and credits.balance(wid) == 3

        with patch.object(app.campaign, 'run'):
            running_id = submit({**params, 'idempotency_key': str(uuid.uuid4())}).json()['job_id']
        assert jobs.claim(running_id)
        db.query("UPDATE jobs SET heartbeat_at=now()-interval '11 minutes' WHERE id=%s", (running_id,))
        legacy = jobs.create(wid, uid, 'retouch', str(uuid.uuid4()), {}, reserved_credits=1)
        assert jobs.claim(str(legacy['id']))
        db.query("UPDATE jobs SET heartbeat_at=now()-interval '11 minutes' WHERE id=%s", (legacy['id'],))
        swept = jobs.sweep()
        assert any(str(row['id']) == str(legacy['id']) for row in swept)
        assert all(str(row['id']) != running_id for row in swept)
        assert jobs.get(running_id, wid)['status'] == 'running' and credits.balance(wid) == 2
        campaign.recover()
        assert jobs.get(running_id, wid)['status'] == 'failed' and credits.balance(wid) == 3

        # Optional references stay workspace-scoped and do not bypass validation,
        # change the one-image price, or collide with an earlier request key.
        def new_brief(**fields):
            return {**params, 'idempotency_key': str(uuid.uuid4()), **fields}

        assert submit(new_brief(person_mode='unknown')).status_code == 422
        assert submit(new_brief(person_mode='library')).status_code == 422
        assert submit(new_brief(person_mode='library', person_key='../../other')).status_code == 404
        assert submit(new_brief(person_mode='upload')).status_code == 422
        model_file = {'person_upload': ('model.png', photo, 'image/png')}
        uploaded = new_brief(person_mode='upload', person_consent='on', brand_name='Luke Diamond')
        assert submit({**uploaded, 'person_consent': ''}, extra_files=model_file).status_code == 422
        assert submit(new_brief(), extra_files=model_file).status_code == 422
        assert submit(uploaded, extra_files={'person_upload': ('bad.png', b'bad', 'image/png')}).status_code == 422
        assert submit(new_brief(brand_name='x' * 121)).status_code == 422
        assert submit(new_brief(use_brand_logo='on')).status_code == 422
        assert credits.balance(wid) == 3
        uploaded_id = submit(uploaded, extra_files=model_file).json()['job_id']
        uploaded_job = jobs.get(uploaded_id, wid)
        assert uploaded_job['params']['person_mode'] == 'upload'
        assert uploaded_job['params']['brand_name'] == 'Luke Diamond'
        assert Path('out', uploaded_job['params']['person_source_key']).exists()
        assert credits.balance(wid) == 2
        assert submit(uploaded, extra_files=model_file).json() == {'job_id': uploaded_id, 'created': False}
        assert submit(uploaded, extra_files={'person_upload': ('model.png', alternate.getvalue(), 'image/png')}).status_code == 409
        assert submit({**uploaded, 'brand_name': 'Another brand'}, extra_files=model_file).status_code == 409

        fixture = Path('out/campaigns/model-logo-fixture.png')
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_bytes(photo)
        own_model = talent.create(wid, uid, 'My model', 'a fashion model', fixture, 'generated')
        foreign_model = talent.create(other, uid, 'Private model', 'a fashion model', fixture, 'generated')
        assert submit(new_brief(person_mode='library', person_key=foreign_model['id'])).status_code == 404
        library_brief = new_brief(person_mode='library', person_key=own_model['id'])
        own_id = submit(library_brief).json()['job_id']
        assert jobs.get(own_id, wid)['params']['person_source_key'] != own_model['s3_key']
        assert submit(library_brief).json()['created'] is False
        talent.archive(own_model['id'], wid)
        assert submit(library_brief).json()['created'] is False
        assert credits.balance(wid) == 1

        # Force both submissions past the initial lookup before either creates a job.
        # Different product photos with the same key must not silently return one ad.
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        normalize = campaign.normalize

        def together(source, destination):
            normalize(source, destination)
            barrier.wait(timeout=10)

        race = new_brief()
        count = len(calls)
        before = credits.balance(wid)
        with patch.object(campaign, 'normalize', together), ThreadPoolExecutor(2) as pool:
            attempts = [pool.submit(submit, race, content) for content in (photo, alternate.getvalue())]
            assert sorted(attempt.result().status_code for attempt in attempts) == [200, 409]
        assert credits.balance(wid) == before - 1 and len(calls) == count + 1
        credits.grant(wid, 3)
        house_key = client.get('/api/models').json()['models'][-1]['key']
        house_id = submit(new_brief(person_mode='library', person_key=house_key)).json()['job_id']
        assert jobs.get(house_id, wid)['params']['person_key'] == house_key

        logo = io.BytesIO()
        Image.new('RGBA', (80, 40), (10, 220, 90, 255)).save(logo, 'PNG')
        logo_file = {'logo_upload': ('logo.png', logo.getvalue(), 'image/png')}
        logo_brief = new_brief(brand_name='Luke Diamond')
        assert submit(logo_brief, extra_files={'logo_upload': ('logo.png', b'bad', 'image/png')}).status_code == 422
        logo_id = submit(logo_brief, extra_files=logo_file).json()['job_id']
        assert jobs.get(logo_id, wid)['params']['logo_source_key']
        assert submit(logo_brief, extra_files=logo_file).json()['created'] is False
        assert submit(logo_brief, extra_files={'logo_upload': ('logo.png', alternate.getvalue(), 'image/png')}).status_code == 409
        saved_logo_key = storage.put(fixture, f'brand/test-{uuid.uuid4().hex}.png')
        db.query('UPDATE workspaces SET brand_logo_key=%s WHERE id=%s', (saved_logo_key, wid))
        saved_brief = new_brief(use_brand_logo='on', brand_name='Saved brand')
        saved_id = submit(saved_brief).json()['job_id']
        assert jobs.get(saved_id, wid)['params']['logo_source_key'] != saved_logo_key
        assert credits.balance(wid) == 0
        db.query('UPDATE workspaces SET brand_logo_key=NULL WHERE id=%s', (wid,))
        assert submit(saved_brief).json()['created'] is False

    import fal_client
    directory = Path('out/campaigns/provider-check')
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / 'output.png'
    output.write_bytes(photo)
    from types import SimpleNamespace
    with patch.object(campaign.providers, 'get', return_value=SimpleNamespace(upload=lambda _: 'https://reference')), \
            patch.object(fal_client, 'subscribe', return_value={'images': [{'url': 'https://output'}]}) as call, \
            patch.object(campaign.hf, 'download', return_value=[output]):
        campaign.generate(output, params, directory, lambda: None)
        assert call.call_args.args == ('openai/gpt-image-2/edit',)
        args = call.call_args.kwargs['arguments']
        assert args['quality'] == 'high' and args['image_urls'] == ['https://reference']
        assert args['image_size'] == {'width': 1600, 'height': 2000} and args['num_images'] == 1
        person_path = directory / 'person.png'
        person_path.write_bytes(photo)
        logo_path = directory / 'mark.png'
        logo_path.write_bytes(logo.getvalue())
        source_key = storage.put(person_path, f'campaigns/provider-check/{uuid.uuid4().hex}.png')
        logo_key = storage.put(logo_path, f'campaigns/provider-check/{uuid.uuid4().hex}.png')
        branded_params = {**params, 'person_mode': 'upload', 'person_source_key': source_key,
                          'logo_source_key': logo_key, 'brand_name': 'Luke Diamond'}
        with patch.object(campaign.providers, 'get', return_value=SimpleNamespace(upload=lambda p: 'https://' + p.name)), \
                patch.object(campaign.branding, 'apply', wraps=campaign.branding.apply) as stamp:
            final = campaign.generate(output, branded_params, directory, lambda: None)
        assert stamp.call_args.kwargs['text'] == 'Luke Diamond'
        assert stamp.call_args.kwargs['campaign'] is True
        assert len(call.call_args.kwargs['arguments']['image_urls']) == 2
        prompt = call.call_args.kwargs['arguments']['prompt']
        assert 'Image 2 is the model identity reference' in prompt and 'Luke Diamond' in prompt
        assert 'top-right corner clear' in prompt
        with Image.open(final) as im:
            assert (10, 220, 90) in {color for _, color in im.convert('RGB').getcolors(im.width * im.height)}, 'exact logo was not composited'
            assert im.getpixel((10, 90)) == (255, 215, 0), 'logo changed another part of the image'
        with patch.object(campaign.branding, 'apply', wraps=campaign.branding.apply) as stamp:
            text_only = campaign.generate(output, {**params, 'brand_name': 'Luke Diamond'}, directory, lambda: None)
        assert stamp.call_args.kwargs['text'] == 'Luke Diamond'
        assert stamp.call_args.kwargs['campaign'] is True
        assert text_only.read_bytes() != photo, 'brand name alone was not composited'
    db.close()
    print('campaign checks passed: validation, ownership, replay/races, credits/refunds, model references, exact logos, GPT Image 2 arguments')


if __name__ == '__main__':
    try:
        main()
    finally:
        db.close()
