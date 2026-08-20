"""Provisioning: workspaces, accounts, and credit grants.

Sales-led means there is no signup page, so this is how a customer comes into existence.
The functions here are the same ones the /admin page calls — the CLI exists because the
very first admin account has to be created before anyone can log in to a page at all.

    .venv/bin/python admin.py workspace "Tanishq" --gstin 29ABCDE1234F1Z5
    .venv/bin/python admin.py user owner@tanishq.com --workspace <id> --role owner
    .venv/bin/python admin.py user you@voxdonna.com --admin
    .venv/bin/python admin.py grant <workspace-id> 500 --note "launch pack"
    .venv/bin/python admin.py list
"""

import argparse
import secrets
import sys

import auth
import db


def create_workspace(name: str, gstin: str = '', billing_email: str = '') -> dict:
    return db.query(
        """INSERT INTO workspaces (name, gstin, billing_email)
           VALUES (%s, %s, %s) RETURNING id, name""",
        (name.strip(), gstin.strip() or None, billing_email.strip() or None), one=True)


def create_account(email: str, workspace_id: str | None = None, role: str = 'member',
                   password: str = '', name: str = '', is_admin: bool = False) -> dict:
    """Create a user and optionally put them in a workspace. Returns the password so
    whoever ran this can pass it on — it is never recoverable afterwards."""
    password = password or secrets.token_urlsafe(12)
    user = auth.create_user(email, password, name, is_admin)
    if workspace_id:
        add_member(str(user['id']), workspace_id, role)
    return {**user, 'password': password}


def add_member(user_id: str, workspace_id: str, role: str = 'member') -> None:
    db.query(
        """INSERT INTO memberships (user_id, workspace_id, role) VALUES (%s, %s, %s)
           ON CONFLICT (user_id, workspace_id) DO UPDATE SET role = EXCLUDED.role""",
        (user_id, workspace_id, role))


def overview() -> list[dict]:
    return db.query("""
        SELECT w.id, w.name, w.gstin,
               (SELECT count(*) FROM memberships m WHERE m.workspace_id = w.id) AS members
          FROM workspaces w
         WHERE w.archived_at IS NULL
      ORDER BY w.created_at""")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('workspace', help='create a workspace')
    p.add_argument('name')
    p.add_argument('--gstin', default='')
    p.add_argument('--billing-email', default='')

    p = sub.add_parser('user', help='create an account')
    p.add_argument('email')
    p.add_argument('--workspace', default=None)
    p.add_argument('--role', default='member', choices=('owner', 'member'))
    p.add_argument('--name', default='')
    p.add_argument('--password', default='')
    p.add_argument('--admin', action='store_true')

    p = sub.add_parser('grant', help='add credits to a workspace')
    p.add_argument('workspace')
    p.add_argument('credits', type=int)
    p.add_argument('--note', default='manual grant')

    sub.add_parser('list', help='show workspaces')

    args = parser.parse_args()
    db.migrate()

    if args.command == 'workspace':
        row = create_workspace(args.name, args.gstin, args.billing_email)
        print(f"workspace {row['id']}  {row['name']}")

    elif args.command == 'user':
        row = create_account(args.email, args.workspace, args.role,
                             args.password, args.name, args.admin)
        print(f"user {row['id']}  {row['email']}"
              f"{'  [admin]' if row['is_admin'] else ''}")
        print(f"password: {row['password']}")
        print('This is the only time it is shown.')

    elif args.command == 'grant':
        import credits
        balance = credits.grant(args.workspace, args.credits, args.note)
        print(f'granted {args.credits}; balance now {balance}')

    elif args.command == 'list':
        rows = overview()
        if not rows:
            print('no workspaces yet')
        for row in rows:
            try:
                import credits
                balance = credits.balance(str(row['id']))
            except Exception:
                balance = '-'
            print(f"{row['id']}  {row['name']:<28} members={row['members']:<3} "
                  f"credits={balance}  gstin={row['gstin'] or '-'}")

    db.close()


if __name__ == '__main__':
    sys.exit(main())
