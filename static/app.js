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

// The header chip: workspace, credit balance, and a way out. Rendered from one place so
// every page gets the same thing without duplicating markup.
function renderAccount() {
  const host = $('account');
  if (!host || !session.me) return;
  const { workspace, workspaces, email, is_admin: isAdmin } = session.me;

  const parts = [];

  if (workspaces.length > 1) {
    const select = document.createElement('select');
    select.className = 'ws';
    workspaces.forEach((w) => select.appendChild(Object.assign(
      document.createElement('option'),
      { value: w.id, textContent: w.name, selected: workspace && w.id === workspace.id })));
    select.addEventListener('change', async () => {
      const body = new FormData();
      body.append('workspace_id', select.value);
      await api('/api/me/workspace', { method: 'POST', body });
      location.reload();   // balances, history and jobs are all workspace-scoped
    });
    parts.push(select);
  } else if (workspace) {
    parts.push(Object.assign(document.createElement('span'),
                             { className: 'ws-name', textContent: workspace.name }));
  }

  if (session.balance !== undefined && session.balance !== null) {
    const chip = document.createElement('span');
    chip.className = 'credits' + (session.balance <= 0 ? ' empty' : '');
    chip.textContent = `${session.balance} credit${session.balance === 1 ? '' : 's'}`;
    chip.title = 'One credit is one generated image';
    parts.push(chip);
  }

  const menu = document.createElement('div');
  menu.className = 'acct';
  menu.title = email;
  const links = [['History', '/history.html'], ['Branding', '/settings.html'],
                 ['Billing', '/billing.html']];
  if (isAdmin) links.push(['Admin', '/admin.html']);
  links.forEach(([label, href]) => {
    if (location.pathname === href) return;
    menu.appendChild(Object.assign(document.createElement('a'),
                                   { href, textContent: label }));
  });
  const out = Object.assign(document.createElement('button'),
                            { type: 'button', textContent: 'Sign out' });
  out.addEventListener('click', async () => {
    await api('/api/auth/logout', { method: 'POST' });
    location.href = '/login.html';
  });
  menu.appendChild(out);
  parts.push(menu);

  host.replaceChildren(...parts);
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

function titleCase(text) {
  return String(text).charAt(0).toUpperCase() + String(text).slice(1);
}
