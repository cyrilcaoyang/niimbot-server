# Niimbot D110_M / B1 service

The binding [lab contract, Part I](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/docs/AGENTIC_LAB_DESIGN.md)
and [STATUS_SPEC](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/docs/STATUS_SPEC.md)
apply. API documentation and claim possession do not authorize hardware execution.
Clients use lab-skills under validated, human-approved execution contexts.
This service does not implement the device-run draft/approval executor; `/plans/actions`
is discovery only. No print button, direct HTTP printing CLI, or automatic startup print.

## Operation

Primary operation means physically printing/feeding the requested labels.
Simulation is the default; it never imports Bleak or accesses Bluetooth.
Startup and GET requests do not scan, connect, initialize, or print.
An explicit claimed `printer.connect` selects one exact configured printer of the selected model.
On macOS the address is a CoreBluetooth UUID, not a Bluetooth MAC address.
No reconnect, radio enabling, pairing, reset, feed, calibration or firmware update is automatic.

Print jobs accept a base64 PNG, UUID job ID, copies and density. Prepare final orientation
before submission. Maximum width is 96 pixels for D110_M or 384 pixels for B1 at 203 dpi; shorter
widths are padded white on the right, not scaled. Maximum height 2400 pixels, copies 1–20,
density 1–3 for D110_M or 1–5 for B1. Transparent pixels become white. Single-frame PNG only, maximum 1 MB decoded.
Physical label dimensions must be checked by the operator; reported paper dimensions are not an independent measurement.
The printer protocol remains experimental until the specific unit is validated.

`202` means accepted, not completed. Poll `/jobs/{job_id}`. An identical job ID and payload
never retransmits; changed content under that ID returns 409. Do not change the ID merely
to get around an uncertain result. Jobs are journaled before device writes. Intent and
operational transitions are local recovery data, not experimental evidence: approved lab
workflows must append real print outcomes and identity to BitacoraDB. No automatic replay.

Completion means the printer reported the requested page count; it is not optical proof
of label content. The fork's two-second sleep is not used as evidence of completion.
Activity is unknown during transmission, running only after observed progress, idle after
completion. Simulator outcomes remain explicitly simulated and must not enter real run records.

## Label information

`printer.label_info` is an explicit, claimed, serialized query via NiimbotClient.
It requires an idle, connected, fault-free printer with no unreconciled jobs. It reads
RFID (0x1A) and paper info (0x59); it never feeds, prints or writes RFID. Results include
a timestamp and simulation flag. Unknown layouts remain unknown; unsupported queries
and absent tags are distinct. Counters do not have verified units. Neither a missing
tag nor missing dimensions proves the printer is empty.

GET `/status` returns the cached snapshot without Bluetooth I/O. Manual stock changes
are not detected automatically. Query again to refresh. New connections and accepted
prints invalidate the snapshot. Optional paper timeout preserves RFID data but closes
the link to isolate late replies. Other query failures close the link and return 503
`label_query_failed`. Reconnect explicitly; no automatic retry or print.

## Claims, access and faults

Claim/heartbeat/release implement STATUS_SPEC v1.1 with hard token enforcement on all
device controls. Claims are cooperative locks, not authentication or human approval.
The default bind is loopback; network deployment requires the lab's reviewed Tailnet/edge
access controls. Owner/session are supplied by the client and are not verified identities.
There is no automatic claim stealing. Lease expiry stops further writes but cannot guarantee
the printer stops already buffered work. A single service process owns each journal/printer.

HTTP refusals are `{detail, code, required_actions}`; 423 missing/invalid control token,
401 lost heartbeat claim, 409 claim/job conflict, 412 precondition refusal, 400 invalid image,
413 body too large, 422 schema validation, 503 connection/scan/label-query failure.
`allowed_actions` uses the same operational preconditions as control endpoints; claims
remain an additional caller-specific requirement. A refusal never sets `last_error`.

Errors have stable codes `connection_failed` and `outcome_unknown`. Successful connection
or printing clears historical errors. An unreachable/disconnected printer is not asserted
to have a hardware fault. Startup reports requires_init; ambiguous outcomes report unknown
and withhold printing, even after restart. `/health` only describes service liveness.

An interrupted job remains unknown. A human must inspect the actual labels and confirm
the printer is idle before `printer.reconcile`, recording printed/not_printed and a note.
This appends an observation without erasing the original uncertain event. Reconciliation
does not print, count an observed label as an automated success, or resend the job.
Disconnect/shutdown are not physical abort commands. The service has no verified emergency
stop command for this printer. No alternate protocol is tried after a failed print.

## Integration

`/docs`, `/openapi.json`, `/agent-docs`, `/llms.txt`, `/docs/agent`, `/plans/actions`
provide documentation/discovery. See README for the optional NiimbotClient wrapper and
skill definitions. Existing production registry/catalog remain unchanged until reviewed.
