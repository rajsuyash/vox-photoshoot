# Razorpay provider acceptance — 2026-10-01

Executed against committed runtime `d00a9be` using real Razorpay **Test Mode**, a disposable local PostgreSQL database and an owned email inbox. Public simulator card details were used; no real money moved. The app sources match the deployed archive. A loopback-only harness maps the local browser Origin to the production Origin so the email renderer keeps its trusted production links; this adapter is not deployed.

| Pack | Test subscription | Captured initial payment | Credits posted | Simulated INR paid |
| --- | --- | --- | --- | --- |
| Starter | `sub_TifaWt85s7jGqc` | `pay_Tiff0zWestsfXU` | 30 | 1,050 |
| Studio | `sub_TifaZlrb9SZcvq` | `pay_Tifio8gwsNrzLa` | 100 | 3,500 |
| House | `sub_TifacZLAjqeohP` | `pay_Tifkbob0LZ8rrC` | 300 | 10,500 |

- Concurrent Starter checkout calls reused one subscription. No credit or invoice was posted before payment.
- Studio and House browser checkout callbacks posted credits, invoices and queued app receipts. Starter's first callback rolled back because the original localhost harness Origin was rejected by the email renderer; after correcting the harness, the same captured payment reconciled to 30 credits and one receipt.
- Starter renewal `pay_TifiEksQflRDRQ` / `inv_TifiCe3TMi7fbw` completed asynchronously after the dashboard's **Charge as Success** simulation. A signed HTTP event posted another 30 credits and one additional receipt; repeated deliveries posted zero.
- Actual processed partial refund `rfnd_TifkxyCYxDWtlL` (INR350) and remainder `rfnd_TifmpFdRpIH2dP` (INR700) reversed ten then twenty credits. Refund replay and a delayed original charge kept the refunded invoice and cumulative reversal intact. There are two cumulative refund notices, not duplicate notices for repeated deliveries.
- House cancellation completed through the real billing UI and provider API, retaining all 300 credits. All three disposable subscriptions were then cancelled; a stale pending event could not overwrite cancellation or change balances.
- Final balances were Starter30 (a second paid cycle after the first cycle was fully refunded), Studio100 and House300. Four app receipts and two app refund notices were queued; SES customer sending remained disabled.

Provider start emails reached the owned inbox for all three subscriptions. House cancellation delivery is confirmed by Gmail messages `1a0f7d06e363c1bb` and `1a0f7d0768c9b266` at 14:13:47Z/14:13:48Z. Two provider message variants with different subjects reached the same owned inbox; the cause of the two variants was not verified. These are real received messages, not notification flags.

Limitations: the Studio **Charge as failure** simulation remained payment `pay_TifkomJ31YlPwY` in `created` and invoice `inv_Tifkn8GKTH7667` in `issued` before cleanup. Failure-state/email delivery is not claimed. Webhook envelopes were locally signed with the disposable test secret and exercised the actual HTTP handler, which refetched real provider entities; no provider-to-host webhook delivery is claimed. Test invoices reported tax_amount0, so production GST configuration is not proven. No live subscriber mandate, production database or live API credential was changed.

Raw assertion evidence: `/private/tmp/vox-razorpay-acceptance-proof.json`; browser proof: `/private/tmp/vox-razorpay-cancel-proof.png`. The local server was stopped, the disposable database was dropped, and temporary credential/state caches were removed. The dashboard view was restored to Live Mode. The toolbar action labelled “View test API keys” unexpectedly created a sandbox key; its secret was used only in a protected temporary cache, never in the repository, production environment or memory vault. That sandbox key remains in the provider account; no live key was rotated. Customer sending still requires SES production access and activation for new events only.

## Live provider-to-host delivery — verified 2026-10-01

Read-only CloudWatch Logs Insights query over `/aws/apprunner/vox-photoshoot/*/application` (ap-south-1, last 30 days, 269,563 records scanned) found real Razorpay **Live Mode** webhook deliveries to `POST /api/webhooks/razorpay` on 2026-09-30 between 09:44:41 and 09:45:48 UTC. Production verifies signatures with the live webhook secret (`billing.py` `verify`) and the deployed key is a `rzp_live_` key, so these were accepted provider-signed deliveries, not locally signed envelopes:

- 09:44:41, 09:44:46, 09:44:51 — `200 OK`, handler processed cancellations for `sub_TiCfDzsvr14FQs`, `sub_TiCfIvlD7EAo2B`, `sub_TiCfNXI87I9wCV`.
- 09:45:48 — `200 OK`, `ignored: subscription belongs to another application` (correct isolation).
- 09:44:48 — one `400 Bad Request`; cause not determined from the access log.

This closes the provider-to-host delivery gap for subscription events. Still unproven: payment-failure state and failure email delivery (Test Mode events cannot reach production because production uses live keys and the live webhook secret; proving it needs a separate Test Mode environment or a real failed live payment). Unrelated `GET /stripe/webhook_secret.env` probes in the same logs were bot scans answered with `303`.

## Test Mode failure-path attempt — 2026-10-01 (not verified)

A local copy of the app ran against a throwaway PostgreSQL database with a regenerated Test Mode key (`rzp_test_TikX1biHzhKwlK`), behind a temporary cloudflared tunnel registered as a Test Mode webhook. Subscription `sub_TikfsUhny0iRAx` (Starter) was created through the real checkout with Razorpay's recurring test Mastercard; initial payment `pay_Tikm5KZYye1KUv` captured. Razorpay servers delivered webhooks to the tunnel (Test Mode provider-to-host delivery proven). Razorpay's own "Subscription Initialized" (Gmail `1a0f8e414190b108`, 19:14:49Z) and "Subscription Charged Successfully" (Gmail `1a0f8e8863f25630`, 19:19:41Z) emails reached the owned inbox.

Failure path remains **not verified**. Four dashboard **Charge as failure** simulations each captured a payment instead (`pay_TikrJLZjPjq5U8`, `pay_TiktcKw2ztoWim`, `pay_TikviGN42ig13v`, `pay_TikwmUJMQASwqG`); final provider state `active`, `paid_count 5`, never pending/halted, so no failure email could be produced. Hypothesis, unconfirmed: the simulator does not apply to card-token recurring charges; an e-mandate/UPI Autopay subscription or Razorpay support is the next path. Local credits did not post because `PUBLIC_ORIGIN=http://localhost:8000` fails the email-link allowlist in `notifications.render`, rolling back the crediting transaction; this is a harness configuration gap (prior runs mapped the origin to production for local rendering only), not a billing defect. Cleanup: subscription cancelled, Test Mode webhook deleted (three Live Mode webhooks untouched), throwaway database dropped, tunnel/server stopped, key file deleted, dashboard returned to Live Mode. All amounts were Test Mode; no real money moved.
