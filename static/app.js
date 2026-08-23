// Shared by every page. No build step, no framework — a <script src> and these helpers.

const $ = (id) => document.getElementById(id);

// Every call to the API goes through here. The one job it does that a bare fetch()
// cannot: a 401 means the session went away (expired, or signed out in another tab),
// and the honest response is to send the person to the login page rather than let the
// UI sit there failing quietly.
async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 401) {
    location.href = `/login.html?next=${encodeURIComponent(location.pathname)}`;
    throw new Error('signed out');
  }
  if (!response.ok) {
    const detail = (await response.json().catch(() => ({}))).detail;
    throw new Error(detail || `${path} failed (${response.status})`);
  }
  return response.json();
}

const session = { me: null };

async function loadSession() {
  session.me = await api('/api/me');
  renderAccount();
  return session.me;
}

// Line icons, 18px, stroked in currentColor so they take the nav item's state without a
// second rule. Kept as path data rather than an icon font or a sprite sheet: five glyphs
// do not justify a request, and inline SVG inherits colour for free.
const ICONS = {
  camera: 'M3 8.5A2.5 2.5 0 0 1 5.5 6h1.2l1-1.6a1 1 0 0 1 .85-.4h4.9a1 1 0 0 1 .85.4'
        + 'l1 1.6h1.2A2.5 2.5 0 0 1 19 8.5v7A2.5 2.5 0 0 1 16.5 18h-11A2.5 2.5 0 0 1 3'
        + ' 15.5zM11 8.5a3.2 3.2 0 1 0 0 6.4 3.2 3.2 0 0 0 0-6.4z',
  clock: 'M11 3.2a7.8 7.8 0 1 0 0 15.6 7.8 7.8 0 0 0 0-15.6zM11 6.8V11l3 1.8',
  brand: 'M3.6 11.4 10 5h6.4v6.4L10 17.8zM13.4 8.6h.01',
  box: 'M11 3.1 4 6.7v8.6l7 3.6 7-3.6V6.7zM4.2 6.9 11 10.3l6.8-3.4M11 10.3v8.4',
  face: 'M11 3.4a3.6 3.6 0 1 0 0 7.2 3.6 3.6 0 0 0 0-7.2zM4 18.6a7 7 0 0 1 14 0',
  card: 'M3 7.5A2.5 2.5 0 0 1 5.5 5h11A2.5 2.5 0 0 1 19 7.5v7a2.5 2.5 0 0 1-2.5 2.5h-11'
      + 'A2.5 2.5 0 0 1 3 14.5zM3 9.5h16M6.5 13.8h3',
  shield: 'M11 3.4 4.8 6v4.9c0 3.4 2.5 6.5 6.2 7.7 3.7-1.2 6.2-4.3 6.2-7.7V6z',
  plus: 'M11 5.5v11M5.5 11h11',
};

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', '18');
  svg.setAttribute('height', '18');
  svg.setAttribute('viewBox', '0 0 22 22');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[name]);
  path.setAttribute('stroke', 'currentColor');
  path.setAttribute('stroke-width', '1.6');
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(path);
  return svg;
}

// The generator is served at both / and /index.html, so an equality test against one of
// them leaves the first nav item unhighlighted on whichever URL the person actually used.
function isCurrent(href) {
  const here = location.pathname;
  if (href === '/') return here === '/' || here === '/index.html';
  return here === href;
}

// The whole sidebar below the logo: navigation, then credits and the account pinned to
// the bottom. Rendered from one place so a new screen appears in every page's nav at
// once, and so no page carries its own copy of the route list.
function renderAccount() {
  const host = $('account');
  if (!host || !session.me) return;
  const { workspace, workspaces, email, is_admin: isAdmin } = session.me;

  const nav = document.createElement('nav');
  const routes = [
    ['/', 'New photoshoot', 'camera', true],
    ['/models.html', 'Models', 'face'],
    ['/products.html', 'Products', 'box'],
    ['/history.html', 'History', 'clock'],
    ['/settings.html', 'Branding', 'brand'],
    ['/billing.html', 'Billing', 'card'],
  ];
  if (isAdmin) routes.push(['/admin.html', 'Admin', 'shield']);

  routes.forEach(([href, label, glyph, primary]) => {
    const link = Object.assign(document.createElement('a'), { href });
    // The primary action reads as a button, so it takes the + rather than the camera —
    // and it never shows the current-page state, which would make a button look inert.
    link.append(icon(primary ? 'plus' : glyph), label);
    if (primary) link.className = 'new';
    else if (isCurrent(href)) link.setAttribute('aria-current', 'page');
    nav.appendChild(link);
  });

  const foot = document.createElement('div');
  foot.className = 'foot';

  if (session.balance !== undefined && session.balance !== null) {
    const card = document.createElement('div');
    card.className = 'credits' + (session.balance <= 0 ? ' empty' : '');
    card.append(
      Object.assign(document.createElement('b'), { textContent: session.balance }),
      Object.assign(document.createElement('span'),
                    { textContent: session.balance === 1 ? 'credit left' : 'credits left' }),
    );
    // No point linking to Billing from Billing.
    if (!isCurrent('/billing.html')) {
      card.appendChild(Object.assign(document.createElement('a'),
                                     { href: '/billing.html', textContent: 'Buy credits →' }));
    }
    card.title = 'One credit is one generated image';
    foot.appendChild(card);
  }

  if (workspaces.length > 1) {
    const select = document.createElement('select');
    select.className = 'ws';
    select.setAttribute('aria-label', 'Workspace');
    workspaces.forEach((w) => select.appendChild(Object.assign(
      document.createElement('option'),
      { value: w.id, textContent: w.name, selected: workspace && w.id === workspace.id })));
    select.addEventListener('change', async () => {
      const body = new FormData();
      body.append('workspace_id', select.value);
      await api('/api/me/workspace', { method: 'POST', body });
      location.reload();   // balances, history and jobs are all workspace-scoped
    });
    foot.appendChild(select);
  } else if (workspace) {
    foot.appendChild(Object.assign(document.createElement('span'),
                                   { className: 'ws-name', textContent: workspace.name }));
  }

  const who = document.createElement('div');
  who.className = 'who';
  who.append(
    Object.assign(document.createElement('span'),
                  { className: 'avatar', textContent: (email || '?').charAt(0) }),
    Object.assign(document.createElement('span'),
                  { className: 'email', textContent: email, title: email }),
  );
  const out = Object.assign(document.createElement('button'),
                            { type: 'button', textContent: 'Sign out' });
  out.addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' });
    location.href = '/login.html';
  });
  who.appendChild(out);
  foot.appendChild(who);

  host.replaceChildren(nav, foot);
}

// Credits live on the session object so any page can show them after one fetch.
async function loadBalance() {
  try {
    const { balance } = await api('/api/credits');
    session.balance = balance;
    renderAccount();
    return balance;
  } catch (error) {
    return null;   // pre-billing deploys have no endpoint; the chip just stays hidden
  }
}

// A first-sign-in banner. The Google callback lands on /?welcome=N, where N is the
// credits granted — 0 when the daily trial budget is spent, which needs saying out loud
// rather than leaving someone to interpret an empty balance as a broken signup.
function showWelcome() {
  const raw = new URLSearchParams(location.search).get('welcome');
  if (raw === null) return;
  const granted = Number(raw);
  history.replaceState({}, '', location.pathname);   // survive a refresh only once

  const banner = document.createElement('div');
  banner.className = granted > 0 ? 'notice' : 'error';
  banner.style.marginBottom = '24px';
  banner.textContent = granted > 0
    ? `Welcome — ${granted} free credits are in your account. `
      + `One credit is one image, so that is ${Math.floor(granted / 3)} full shoots.`
    : 'Welcome. Free trials are all claimed for today, so your account starts empty — '
      + 'buy credits from Billing, or email us and we will top you up.';

  const main = document.querySelector('main');
  if (main) main.prepend(banner);
}

// Shared by the generator and the models page. It lived in index.html, which is why
// models.html could not have a dropdown without a fourth copy of a thing this codebase
// already had three copies of.
function fillSelect(select, values, chosen) {
  select.replaceChildren(...values.map((value) => Object.assign(
    document.createElement('option'), { value, textContent: titleCase(value),
                                        selected: value === chosen })));
}

function titleCase(text) {
  return String(text).charAt(0).toUpperCase() + String(text).slice(1);
}
