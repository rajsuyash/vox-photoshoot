# NAT Gateway → NAT instance (2026-10-02/03) — what was done and why

Record of the cost change to Photoshoot's private egress. Plan:
`docs/plan-2026-10-02-nat-instance.md`. Tool: `tools/admin_nat_instance.py`.

## Why it was touched

AWS Budgets alerted on 2026-10-02 for account 085193942944: forecast **$187–212/month**
against a $50 budget. Cost Explorer: Aug $94.49, Sep $197.36 of usage, all paid by credits
on this same account (standalone, no Organization). The Oct jump came from
`nat-092ef3b01ff293f14`, created 2026-09-30 by `tools/admin_network.py` — ~$40/month in
hours alone, carrying **18–28 MB/day**.

## What the NAT is for — measured before changing anything

Commit `4748462` (admin/security release) made production RDS private and moved the app
and worker onto private subnets, so **all** their internet egress needs NAT:

- App Runner `vox-photoshoot` → VPC connector `vox-photoshoot-private` (egress type VPC):
  Higgsfield, Razorpay, Google OAuth, S3, SES…
- Lambda `vox-photoshoot-notifications` (runs every minute; SES, Secrets Manager, SNS,
  CloudWatch). The VPC has **no** interface endpoints, so it needs NAT too.

Not consumers: RDS (admits only app SG `sg-0720f81877b10330c`); `aisewak-app` EC2 used the
main route table → IGW (since deleted). Route table changed: `rtb-03927d2197bb437fa` only,
never the main table. No egress-IP allowlist was found for the old NAT IP 65.0.243.0.

## Options and the decision

| Option | $/mo | Verdict |
|---|---|---|
| Keep managed NAT Gateway | ~$40 | Safe status quo |
| **NAT instance t4g.nano** | ~$3 + EIP | **Chosen** — same privacy model, tiny traffic |
| App Runner default egress + public RDS | $0 | Rejected: undoes the 09-30 security decision |
| Share another VPC's NAT | — | Impossible (peering does not transit internet) |
| IPv6 egress-only IGW | $0 | Rejected: needs every provider on IPv6 |

Accepted trade-off: a single instance is a point of failure a managed gateway is not.
Mitigations: EC2 auto-recover alarm (`StatusCheckFailed_System`) and reboot alarm
(`StatusCheckFailed_Instance`) → `vox-photoshoot-alerts`; old gateway kept 24 h as an
instant rollback target.

## How the tool is guarded

- `prepare` builds instance/SG/EIP/alarms and **never touches a route**; waits for 2/2
  checks **and** a `VOXNAT-READY forward=1 masq=N swap=N` marker on the serial console;
  exits immediately on `VOXNAT-FAILED`.
- `cutover` refuses unless the instance is 2/2, `SourceDestCheck=false`, and the console
  marker shows forwarding + MASQUERADE; refuses the main route table; then `ReplaceRoute`
  and **proves egress** with the notifications Lambda (≥2 runs after cutover, 0 errors,
  duration < 80 s) — not `/healthz`, which makes no outbound call (`app.py:266`) and would
  pass with egress broken. Any failure → automatic route back to the gateway.
- `rollback`, `teardown` (refuses if the route targets the instance), `cleanup` (requires
  `--confirm-delete-nat nat-092ef3b01ff293f14`).

## What went wrong first, and the fixes

1. **2026-10-02, first `prepare`:** `dnf install iptables-services` was **OOM-killed** on the
   418 MB nano (`Out of memory: Killed process (dnf)` in `get-console-output`). The instance
   passed 2/2 status checks with no NAT rule. `cutover` refused — production untouched.
   The identical CDK-built NAT instance on the Sarvam dashboard (vad-dev) was **not** gated
   and took its egress down ~16:10–18:42 UTC before being rolled back.
2. **Script bug found during the fix:** `console_text()` base64-decoded `GetConsoleOutput`
   a second time (botocore already decodes it), so the marker checks could never match —
   `prepare` could not fail fast and `cutover` would have refused even a healthy instance.
   Verified on the live console: raw text contains `VOXNAT-FAILED`, double-decoded does not.
3. **Fix:** user data creates a **1 GiB swapfile before dnf** and installs with
   `--setopt=install_weak_deps=False`. AL2023 ships no nft/iptables userland (checked via
   SSM: `rpm -q` not installed), so the install cannot be skipped. Added `teardown`.

## Result (2026-10-03)

- Instance `i-079aa45ecca07f165` (EIP 3.108.172.181). Console:
  `VOXNAT-READY forward=1 masq=1 swap=2`. SSM: `-A POSTROUTING -o ens5 -j MASQUERADE`,
  `ip_forward=1`, `iptables` enabled, swapfile active (180 KB used).
- `cutover`: route `0.0.0.0/0 → i-079aa45ecca07f165` active; notifications Lambda 2 runs /
  0 errors / max 311 ms after cutover; `healthz` 200.
- **Proof of translation:** MASQUERADE counter **92 packets**, FORWARD **129 packets / 40 KB**.

## Remaining

- After a clean 24 h (not before 2026-10-04 ~06:05 UTC):
  `.venv/bin/python tools/admin_nat_instance.py cleanup --confirm-delete-nat nat-092ef3b01ff293f14`
  — this is the step that actually removes the ~$40/month. Until then both are billed.
- Then update `docs/admin-operations.md` (line 68 still says "one NAT gateway").
- Rollback any time before cleanup: `.venv/bin/python tools/admin_nat_instance.py rollback`.
- Run the tool with `.venv/bin/python` — system `python3` has no boto3.

## Lessons worth keeping

- A NAT instance passes routes, status checks and app health while not translating at all.
  The acceptance test is the MASQUERADE rule present **and** its packet counters rising
  under real private-subnet traffic.
- Health endpoints prove ingress, not egress. Use a workload that must call out.
- A check that has never been seen to fail may be unable to fail (the double-decode bug).
