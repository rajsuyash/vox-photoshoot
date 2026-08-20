"""Sign in with Google. Authorization-code flow, standard library only.

No google-auth, no authlib. The flow is two HTTP calls and a base64 decode, and the
dependency would exist to do the one thing we deliberately do not do — verify the
id_token signature.

That omission is deliberate and is Google's own guidance: a token received directly from
their token endpoint over TLS, in a request authenticated with the client secret, needs
no local validation. The channel is the proof. Verifying it anyway means fetching JWKS,
caching it, and handling key rotation — machinery whose only job is to re-establish a
guarantee already held. This would be wrong for a token arriving from a browser, where
the channel proves nothing; that path does not exist here.

    .venv/bin/python oauth_google.py        # self-check, no network and no Google account
"""

import base64
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'

# openid and email are what we need; profile is for a workspace name that reads like a
# company rather than a UUID. Nothing else is requested — a consent screen asking for
# Drive access to make jewellery photographs does not get consented to.
SCOPES = 'openid email profile'

# The CSRF cookie. Short-lived because it only has to survive a round trip to Google.
STATE_COOKIE = 'donna_oauth_state'
STATE_TTL_SECONDS = 600


def configured() -> bool:
    return bool(os.environ.get('GOOGLE_CLIENT_ID')
                and os.environ.get('GOOGLE_CLIENT_SECRET'))


def redirect_uri() -> str:
    """Must match a URI registered on the OAuth client, byte for byte.

    Derived from PUBLIC_ORIGIN rather than from the incoming request: Host is a header,
    and trusting it lets someone with a spoofed Host redirect the code somewhere else.
    """
    origin = os.environ.get('PUBLIC_ORIGIN', 'http://localhost:8000').rstrip('/')
    return f'{origin}/api/auth/google/callback'


def new_state() -> str:
    return secrets.token_urlsafe(24)


def auth_url(state: str) -> str:
    return AUTH_ENDPOINT + '?' + urllib.parse.urlencode({
        'client_id': os.environ['GOOGLE_CLIENT_ID'],
        'redirect_uri': redirect_uri(),
        'response_type': 'code',
        'scope': SCOPES,
        'state': state,
        # Consent every time is wrong for a returning user; select_account lets someone
        # with two Google accounts pick, rather than being silently signed into the
        # wrong one because Chrome remembered.
        'prompt': 'select_account',
    })


def _decode_segment(segment: str) -> dict:
    """One base64url JWT segment. Padding is stripped in JWTs and must be put back."""
    padded = segment + '=' * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def claims_from_id_token(id_token: str) -> dict:
    """The payload of a token that came straight from Google. See the module docstring."""
    parts = id_token.split('.')
    if len(parts) != 3:
        raise ValueError('not a JWT')
    return _decode_segment(parts[1])


def exchange(code: str) -> dict:
    """Trade the authorization code for the user's verified claims.

    Returns sub, email, email_verified and name. Raises on anything that is not a
    complete, verified identity — a caller that gets a dict back can trust it.
    """
    body = urllib.parse.urlencode({
        'code': code,
        'client_id': os.environ['GOOGLE_CLIENT_ID'],
        'client_secret': os.environ['GOOGLE_CLIENT_SECRET'],
        'redirect_uri': redirect_uri(),
        'grant_type': 'authorization_code',
    }).encode()

    request = urllib.request.Request(
        TOKEN_ENDPOINT, data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            token = json.load(response)
    except urllib.error.HTTPError as error:
        # Google puts the useful part in the body, not the status line.
        raise ValueError(f'google rejected the code: {error.read().decode()[:200]}')

    if 'id_token' not in token:
        raise ValueError('google returned no id_token')

    claims = claims_from_id_token(token['id_token'])
    return validate(claims)


def validate(claims: dict) -> dict:
    """Everything that must be true before these claims become a login."""
    if claims.get('aud') != os.environ.get('GOOGLE_CLIENT_ID'):
        # A token minted for a different OAuth client is not a login here. Without this,
        # a token from any other Google app would be accepted at face value.
        raise ValueError('id_token was issued for a different client')
    if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise ValueError(f'unexpected issuer {claims.get("iss")!r}')
    if not claims.get('sub'):
        raise ValueError('id_token carried no subject')
    if not claims.get('email'):
        raise ValueError('id_token carried no email')

    # Load-bearing. auth.upsert_google_user links a Google identity onto an existing
    # password account when the addresses match, so an unverified address would let
    # anyone who can register it at Google walk into that workspace.
    if claims.get('email_verified') not in (True, 'true'):
        raise ValueError('this Google account has an unverified email address')

    return {
        'sub': str(claims['sub']),
        'email': str(claims['email']).strip(),
        'name': str(claims.get('name') or '').strip(),
    }


def demo() -> None:
    """Self-check: the parts that do not need Google."""
    os.environ['GOOGLE_CLIENT_ID'] = 'test-client.apps.googleusercontent.com'
    os.environ['PUBLIC_ORIGIN'] = 'https://photo.voxdonna.com/'

    assert redirect_uri() == 'https://photo.voxdonna.com/api/auth/google/callback', \
        'a trailing slash on PUBLIC_ORIGIN must not double up'

    os.environ['GOOGLE_CLIENT_SECRET'] = 'shh'
    url = auth_url('abc123')
    assert url.startswith(AUTH_ENDPOINT)
    parsed = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert parsed['state'] == 'abc123'
    assert parsed['response_type'] == 'code'
    assert parsed['redirect_uri'] == redirect_uri()
    # Scope creep is a consent-screen problem long before it is a privacy problem.
    assert set(parsed['scope'].split()) == {'openid', 'email', 'profile'}

    # A JWT payload with stripped padding must still decode.
    def fake(payload: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b'=')
        return 'header.' + raw.decode() + '.signature'

    good = {'iss': 'https://accounts.google.com',
            'aud': 'test-client.apps.googleusercontent.com',
            'sub': '11223344', 'email': 'Someone@Example.com',
            'email_verified': True, 'name': 'Some One'}
    assert claims_from_id_token(fake(good))['sub'] == '11223344'
    assert validate(good) == {'sub': '11223344', 'email': 'Someone@Example.com',
                              'name': 'Some One'}

    # Each of these must be a refusal, not a login.
    for label, bad in (
        ('unverified email', {**good, 'email_verified': False}),
        ('missing email_verified', {k: v for k, v in good.items()
                                    if k != 'email_verified'}),
        ('another client', {**good, 'aud': 'someone-else.apps.googleusercontent.com'}),
        ('wrong issuer', {**good, 'iss': 'https://evil.example'}),
        ('no subject', {**good, 'sub': ''}),
        ('no email', {**good, 'email': ''}),
    ):
        try:
            validate(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} was accepted as a login')

    # Google sends email_verified as a JSON bool, but has historically sent the string.
    assert validate({**good, 'email_verified': 'true'})['sub'] == '11223344'

    for junk in ('', 'not-a-jwt', 'only.two'):
        try:
            claims_from_id_token(junk)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{junk!r} parsed as a JWT')

    for key in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'PUBLIC_ORIGIN'):
        os.environ.pop(key, None)
    assert not configured(), 'configured() must be false with no keys'
    print('google ok')


if __name__ == '__main__':
    demo()
