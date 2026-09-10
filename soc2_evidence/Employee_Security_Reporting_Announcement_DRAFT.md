# DRAFT — staff announcement: how to report a security concern

**[NOT FOR UPLOAD as evidence until it has actually been sent.]**

This is the one piece of the "Incident Response: Employee Responsibility" control (CC-025,
Tracker control 85, governed by IS-CIRQ-P-014-G) that no system can produce retroactively:
evidence that employees were *told* how to report. Send it, then keep the sent copy — and
ideally the acknowledgement records — as the evidence.

Suggested channel: company-wide email from the IT Manager, plus a mention at the next
all-hands. Keep it short — the goal is that people remember where to click.

*Revised 2026-09-10 (see the checklist at the end): the reporting mailbox is confirmed, and the policy library referenced
below is now real (66 published documents, up from 1). Both were open questions in the
previous version.*

---

**Subject:** If something looks wrong, here's how to tell us

Team,

We've added a simple way to report anything that looks like a security problem. You'll now
find **Report a Concern** in the left-hand menu of the Tracker.

**Please use it if you see anything like:**

- an email asking you to log in, pay something, or open an unexpected attachment
- a message that looks like it's from a colleague or an executive but feels off
- you clicked a link or entered a password somewhere you now doubt
- a lost or stolen laptop, phone or badge
- someone asking for information or access they shouldn't need
- a device behaving strangely — unexpected pop-ups, files you didn't change
- anything else that just doesn't look right

It takes about thirty seconds. Tell us what you saw and roughly when — you don't need to
work out whether it's serious, that's our job.

**Two things worth saying plainly:**

You will never be in trouble for reporting something that turns out to be harmless. We would
far rather look at ten false alarms than miss one real problem.

And if you clicked something or typed a password somewhere you shouldn't have, **please tell
us anyway, straight away**. That is exactly when speed matters most, and nobody gets blamed
for it. The only thing that makes it worse is us finding out late.

**If you think an account or company data is at immediate risk right now**, don't wait for the
form — email **security@cirque.com** and then call or message IT directly so someone picks it
up immediately.

Our full policies are under **Company Policies** in the same menu — including the Incident
Response Procedure, which sets out what's expected of everyone during an incident, and the
Acceptable Use and Remote Work policies.

Thanks,
[Name]
IT Manager

---

## Before sending — what's been resolved and what's still open

**RESOLVED — the reporting mailbox.** The previous draft flagged `security@cirq.com`, taken
from the Incident Report Form, and warned it looked like unlocalised template text. Confirmed
in Exchange Online on 2026-09-10: the real mailbox is **`security@cirque.com`** (`Security`,
UserMailbox). The address in the form is a typo — `cirq.com` is not a domain here. The
announcement above uses the correct address.

  * **Monitored:** confirmed by Chris Wren 2026-09-10 that he sees mail arriving at
    `security@cirque.com`. This mattered — an unmonitored reporting address is worse than
    none, because staff believe they have reported something when nobody is looking.
  * **Fixed at source 2026-09-10.** The typo was NOT previously corrected: version history
    showed only imports, so it had been live since the original import. Now corrected in both
    published documents that carried it — `isms-manual` v5 → v6 and `IS-CIRQ-PR-020-G`
    Incident Response Procedure v1 → v2 — each as a real version with a change summary and an
    audit record. Zero documents still contain it.
  * The copy in `archive/isms-manual-preimport-2026-09-10/IS-CIRQ-F-005-G` still has the typo
    and is deliberately left alone: an archive should show what was true then. Do not
    re-import that file.

**RESOLVED — the policy library.** The previous draft pointed staff at "Company Policies"
when `/policies` served exactly **one** document, which would have made the announcement look
careless. As of 2026-09-10 it serves **66** published documents (30 policies, 28 procedures,
7 registers, the ISMS Manual), including all three localisations of the Incident Response
Procedure. That sentence is now true.

**STILL OPEN — acknowledgement.** `soc2_policy_acknowledgement` has **0 rows**. Sending the
announcement evidences that staff were told; a recorded acknowledgement evidences that they
received and accepted it, which is the stronger form and also addresses the Security Awareness
risk scored Medium in the risk assessment. Worth pairing the two.

**PARTLY RESOLVED — where the Incident Report Form lives.** `IS-CIRQ-F-005` was not imported
as a standalone document (the `F-` forms were left out of the policy library, since blank
templates add noise), but its text is embedded inline in the published **ISMS Manual** — which
is where the manual's copy of the address came from. So the form's content IS in Tracker.

What is genuinely missing is the second half of it. Employee *reporting* is built (**Report a
Concern** creates a Security-category ticket with reporter and asset linkage). The IRT working
record is not: F-005 sections 4.1–4.7 — classification, severity, actions log, root cause,
**legal/regulatory considerations** and closure — have database tables (`incidents`,
`incident_timeline`, `incident_communications`, `incident_compliance`) but no application on
top of them. Section 4.6 is the external-notification decision, i.e. control CC-028.

So the honest position is: reporting is superseded by Tracker; the IRT record is not yet.
Claiming F-005 is fully replaced would not survive a reviewer opening the form and asking
where closure is recorded.

---

## Note — this is NOT the password/VPN announcement

An earlier note suggested folding the SSPR and VPN re-save steps into this message. That would
weaken both: this one has a single job, which is that people remember where to click when
something looks wrong.

The password guidance already goes out automatically and individually — `pwd_expiry_notice.py`
mails each affected person at 14/7/3/1 days and on expiry, with the `aka.ms/sspr` reset and the
two-step VPN fix (connect, then lock/unlock while connected). 27 notices have been sent. That
is better targeted than a company-wide email, because it reaches people at the moment it
matters to them.

If a general announcement is still wanted for the September expiry wave, it should be its own
short message — say the word and I'll draft it separately.
