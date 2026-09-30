# AWS SES production-access follow-up

Recipient: AWS Support, existing SES case `179079869200146`, account `085193942944`, region `ap-south-1`.

The response below is prepared for approval. It has not been submitted.

---

Hello AWS SES review team,

Please continue review of production access in ap-south-1 for Donna Photoshoot.

Application: https://photo.voxdonna.com — a product photography and campaign-image application.

Email type: transactional only: welcome after verified Google sign-in, administrator-authorized workspace invitations, user-requested password recovery, and receipts for verified paid image-credit purchases, renewals and refunds. The product generates marketing images for users; it does not email marketing campaigns to lists.

Expected volume (estimate, not measured traffic): fewer than 50 messages/day and approximately 1,000/month initially. No bulk historical customer mailing.

Recipient source: verified Google account emails, explicitly invited workspace colleagues, and customer-provided billing contacts. Recovery requests have consistent responses and persistent rate limits. We do not buy, scrape or import recipient lists.

Verified sender: voxdonna.com in ap-south-1; Easy DKIM and dedicated MAIL FROM photoshoot-mail.voxdonna.com are SUCCESS. SPF is configured and the existing DMARC policy is preserved. Visible sender: Donna Photoshoot <notifications@voxdonna.com>. Support reply-to: suyash@voxdonna.com.

Bounce/complaint controls: the implementation uses a PostgreSQL outbox with stable event keys, expiring leases and bounded retry/backoff. SES delivery/bounce/complaint events are consumed through an IAM-authenticated SNS/Lambda path. Permanent bounces and complaints suppress subsequent sends. Accepted messages remain distinct from confirmed delivery. Queue age, worker errors and failed messages are monitored. Customer dispatch stays disabled until production approval.

Sample welcome: “Welcome to Donna Photoshoot. Your account is ready with 6 credits. Create a product photoshoot or marketing campaign from your product photo. Start a photoshoot: https://photo.voxdonna.com/index.html. Reply to this email for help.” The actual message uses the credits granted, including zero when the trial cap applies.

Sample receipt (illustrative amounts only): “Plan: Starter. Payment received: INR 1,239.00, including applicable tax. 30 credits added. Balance after this purchase: 30 credits.” The message includes the verified payment reference and invoice link. Mandate authorization does not trigger a purchase receipt; an already reversed purchase does not claim credits were added.

On 2026-09-30, the deployed worker delivered a welcome and receipt to our owned test inbox using an isolated restored database. Both delivery events were recorded. The receipt was a clearly labeled zero-value delivery check; no real payment or customer mailing occurred. Please let us know if further information is needed.
