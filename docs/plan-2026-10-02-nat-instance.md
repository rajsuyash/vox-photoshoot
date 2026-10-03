# Plan — replace `vox-photoshoot-nat` with a NAT instance (2026-10-02)

Status: **proposed, not executed.** Production change; needs owner approval.

## Why

AWS budget alert 2026-10-02 (account 085193942944): forecast $187–212/month vs a $50
budget. `nat-092ef3b01ff293f14` (created 2026-09-30 by `tools/admin_network.py`) costs
~$40/month in hours alone and carried **18–28 MB/day** (CloudWatch
`BytesOutToDestination`, 2026-10-01/02).

## What the NAT is for (measured 2026-10-02, not assumed)

Created by commit `4748462` as part of the admin/security release: production RDS was
made private (public 5432 ingress removed — `docs/admin-operations.md:68`), so the app
and worker moved onto private subnets and need NAT for **all** internet egress.

Consumers — route table `rtb-03927d2197bb437fa` (`vox-photoshoot-private`), subnets
`subnet-0e700931da6b6bfcb`, `subnet-037d014312191b792`:

- App Runner `vox-photoshoot` via VPC connector `vox-photoshoot-private` (egress type
  VPC) — Higgsfield, Razorpay, Google OAuth, S3, etc. all go through NAT.
- Lambda `vox-photoshoot-notifications` (SES, Secrets Manager, SNS, CloudWatch).

Not consumers: RDS (`sg-01d5be2d119020e4f` admits only the app SG `sg-0720f81877b10330c`),
and `aisewak-app` EC2 — it sits in the **same default VPC** (`vpc-0de5f935e53b90183`)
but on the main route table → IGW. The main route table must not be touched.

No egress-IP allowlist found in the repo (grep for the NAT IP 65.0.243.0, "allowlist",
"static ip"); the only "allowlist" is the email-link origin check, unrelated.

## Options considered

| Option | $/mo | Verdict |
|---|---|---|
| Keep NAT Gateway | ~$40 | Managed, AZ-resilient. Status quo. |
| **NAT instance `t4g.nano`** | ~$3 + EIP | **Recommended.** Same privacy model; traffic is tiny. |
| Revert to App Runner default egress + public RDS | $0 | **Rejected** — undoes the 09-30 security decision. |
| Share vad-dev's NAT | — | Impossible: different VPC; peering does not transit internet. |
| IPv6 egress-only IGW | $0 | Rejected — depends on every provider being IPv6-reachable. |

## Risk of the recommended option

This is production with live Razorpay billing. A NAT instance is a single point of
failure the gateway is not: if it stops, generation, payments and email all lose egress.
Mitigations: EC2 auto-recovery alarm on `StatusCheckFailed_System`, reboot alarm on
`StatusCheckFailed_Instance`, both also notifying the existing alerts topic; keep the
NAT Gateway for 24 h after cutover as an instant rollback target.

## Steps (when approved)

1. Launch `t4g.nano` AL2023 ARM in public subnet `subnet-017e85a4160c887b3`, new SG
   `vox-photoshoot-nat-instance` (ingress: all from `sg-0720f81877b10330c` only; egress all),
   user data enabling `net.ipv4.ip_forward` + nftables masquerade (persisted), new EIP,
   `SourceDestCheck=false`, tags `Application=vox-photoshoot`.
2. Verify forwarding **before** switching: instance 2/2 checks, user-data log, iptables/nft
   rule present (SSM or console output).
3. `ReplaceRoute` 0.0.0.0/0 in `rtb-03927d2197bb437fa` → instance (atomic; in-flight
   connections through the gateway drop — do it at low traffic).
4. Prove egress: App Runner `/healthz` + an outbound-dependent path (e.g. Google sign-in
   page redirect or a Higgsfield status call), worker Lambda next run without timeout,
   instance `NetworkOut` > 0, VPC flow logs show forwarded traffic.
5. After 24 h clean: delete `nat-092ef3b01ff293f14`, release its EIP.
6. Record in `docs/admin-operations.md` (line 68 currently says "one NAT gateway").

Rollback (step 3–5 window): `ReplaceRoute` back to `nat-092ef3b01ff293f14`.
Implement as `tools/admin_nat_instance.py`, idempotent, same style as
`tools/admin_network.py`.

## Status

- 2026-10-02 ~17:12 UTC: `prepare` failed safely — `dnf install iptables-services` OOM-killed on t4g.nano (VOXNAT-FAILED on console). `cutover` refused; production route never changed (still `nat-092ef3b01ff293f14`). Idle instance `i-0b124b12eb993ce83` + its EIP/SG/role/alarms remain — tear down or reuse. Same failure took vad-dev egress down 2.5 h (see Sarvam dashboard `RESUME.md` §7). Retry only with a no-install user data (swap first, or `nft`) and a forwarding proof on a throwaway instance.
- Script gap: `prepare` waited out 600 s instead of stopping at VOXNAT-FAILED.
- 2026-10-03 ~06:05 UTC: **retry succeeded.** User data now adds a 1 GiB swapfile before dnf (also fixed: console_text double-base64-decoded GetConsoleOutput, so marker checks could never match). New instance `i-079aa45ecca07f165`: console `VOXNAT-READY forward=1 masq=1 swap=2`; SSM shows MASQUERADE rule + iptables enabled. Cutover: route → instance active; notifications Lambda 2 runs / 0 errors since cutover; healthz 200; MASQUERADE counter 92 pkts, FORWARD 129 pkts / 40 KB — translation proven. `nat-092ef3b01ff293f14` kept as rollback target until `cleanup` after a clean 24 h (not before 2026-10-04 ~06:05 UTC).
