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
