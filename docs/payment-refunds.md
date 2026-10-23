# T-Bank refund accounting

The current checkout remains DEMO-only. Test credits and refund reversals never modify the real account grant, usage ledger or real subscriptions. Having production terminal credentials does not enable live settlement or fiscal receipts.

## Authoritative state and amounts

Refund notifications are signature-checked and bound to the order, terminal and bank payment ID before requesting GetState. The notification's amount is never subtracted as an individual delta: a notification can repeat or arrive out of order. The bank's current GetState response is used to calculate cumulative refunded amount as original amount minus remaining amount. The integration test observed remaining amounts 15000, 10000 and 0 kopecks after successive refunds of a 20000-kopeck payment. A full refund must have remaining amount zero; a partial refund must have a remaining amount strictly between zero and the original amount.

A failed bank request, invalid signature, wrong terminal, mismatched payment ID or impossible amount cannot debit the wallet. A notice that is not yet corroborated by bank state is not acknowledged as complete, allowing retries. If a refund notification is missed, an authenticated order status request reconciles confirmed orders too, with a five-second throttle. This is request-driven reconciliation, not a continuous background bank poll.

## Atomic money movement

Additive migration introduces refunded (cumulative amount), terminal (order binding), and amount_delta in the isolated DEMO event ledger. Existing credits and wallets are preserved during migration. Each refund transaction reads the latest stored cumulative amount and reverses only an increase, atomically updating the wallet, order, event log and package rights. Duplicate or older responses cannot debit twice or restore money. Late CONFIRMED/NEW/AUTHORIZED events cannot reopen a fully refunded order. A full refund observed before an original confirmation produces no phantom credit. A verified partial refund can recover the remaining funded amount when the original confirmation was missed.

The historical credited flag stays as audit evidence. Public credited means there is a net funded amount. Responses expose was_credited, refunded_amount_kopecks and net_credited_amount_kopecks separately. A refund may leave a negative wallet if funds were already used; debt is not hidden by clamping to zero.

## Packages

Full refund of a package removes that purchase from the entitlement calculation. Remaining funded purchases are replayed in their original confirmation order and at their original timestamps, restoring a previously funded package when applicable without creating a fresh month. Unrelated top-ups are preserved. Partially refunded packages remain in that history until fully refunded; this is DEMO behavior and is not a published commercial refund policy.

## Checkout and deployment

The checkout shows full or partial refund before its paid branch, with refunded amount and the corrected test wallet. Code is mounted in both Python package locations used by the existing relay image. No native installer or container image was built. A consistent database backup and an isolated image canary preceded activation.

Verification: 54 targeted Cloud payment, refund, plan and gateway tests passed. The original refunded fixture was reconciled from fresh bank evidence, changing its test wallet from 20000 to 0 kopecks. A separate real DEMO bank-hosted card payment was partially refunded twice and then fully refunded; both bank and HTTP wallet showed 20000 -> 15000 -> 10000 -> 0. Duplicate refund notices and a late signed confirmation had no financial effect. Real grants did not change. Temporary verification identities and keys are disabled after checks.

Official API references: https://developer.tbank.ru/eacq/api/get-state , https://developer.tbank.ru/eacq/api/cancel , https://developer.tbank.ru/eacq/intro/developer/notification .
