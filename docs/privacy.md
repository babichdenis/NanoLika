# Privacy and data flow

This page describes the software, not a guarantee that every deployment complies with
local law. nanoLika's operator-specific document is prepared for
[the nanoLika site](https://nanolika.ru/privacy.html). The current website revision and
server hardening must be deployed separately. Self-hosting operators must describe their
actual infrastructure and processing rules rather than reuse another operator's claims.

## Local data and cloud data are different

A locally installed runtime can store conversations, memory, files, scheduled tasks,
skills and settings on the user's device. This does not mean everything stays there.
Messages, context, attachments and tool results needed for a request can leave the device
for the selected model endpoint. Cloud sign-in, conversation synchronization, remote
backups and connectors create additional flows. A Telegram runtime on a server stores
and processes data on that server, not on the person's phone.

## The relay

The relay can hold account identifiers, encrypted contact details, authentication keys
in hashed form, device information, profile and connectors metadata, usage and financial
records, events, network addresses and client metadata. Encryption does not anonymize
information recoverable by the operator. Where synchronization is used, conversation
content and the agent's profile also reach the relay. See [Cloud](cloud.md) for the API
and account controls. No fixed free allowance, nonprofit status or universal provider is
promised by this document; the particular operator sets and discloses those terms.

## Model providers and channels

A cloud provider processes the context actually sent to it, which can include previous
messages and tool outputs rather than only the latest sentence. nanoLika Cloud is the
service's name, not a claim that its operator developed the underlying model. Own-key
requests follow the configured provider's data rules. Telegram is a separate service
with its own infrastructure and terms. A foreign transport node still requires review
of TLS termination, buffers, logging and connection metadata. Hosting only a database
in a particular country does not localize a bot runtime or its workspace elsewhere.

## Optional conversation samples

New accounts start with additional sample collection off. `IMPROVE_DEFAULT` no longer
enables it. Explicit Data controls opt-in is required; a historical default-on event does
not authorize new capture. This purpose is separate from providing an answer, keeping
conversation history or synchronizing it. When enabled, samples can contain text written
by the person, model answers and tool-call arguments. The sample view filters system
prompts, tool-result messages and image/audio/video content, but the retained text can
still contain personal information. Removing an account id from an export does not
necessarily anonymize it. The operator can access retained samples in the admin console.

Turning the switch off prevents subsequent capture. Deleting samples and deleting an
account are separate controls. Existing samples are not automatically erased by this
code update. A lawful basis and appropriate information for the additional purpose are
still required; a software switch alone does not establish them.

## Preparation and retention

`REGISTRATION_HOLD=1` blocks new identifiers across sign-in, bot onboarding and linking
before an OTP is stored or sent. Existing users keep working. The default is `0` for
compatibility, so the operator must explicitly activate it and restart the relay. It
neither moves existing records nor establishes compliance. Before public onboarding,
verify collection locations, processors, international transfers, notices, retention,
access and deletion, including attachments, logs, audit files and backups. Account
removal and retention required for accounting need separate verification.

## Requests and current status

Use the operator's published contact for access, correction, deletion and withdrawal
requests. Do not include passwords, OTPs or full card details. The exact rights and
retention duties depend on applicable law. The current nanoLika infrastructure migration
is unfinished; do not describe Russian storage as already implemented. Read the operator
notice before submitting personal documents. This software documentation does not replace
a privacy policy, processing agreements or required notifications.
