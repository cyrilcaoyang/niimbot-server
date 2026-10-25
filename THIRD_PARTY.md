# Provenance and license

This project is GPL-3.0-only. Its Niimbot framing, raster packet layout and D110_M
print sequence are derived/adapted from these GPL-3.0 NiimPrintX sources:

- labbots/NiimPrintX, revision `b987d3748784a4f001951e2cbcdba708cae2a719`:
  <https://github.com/labbots/NiimPrintX>
- Eamo5/NiimPrintX, revision `14e71f80b02483f3ee8dd9a849310eff354f4217`:
  <https://github.com/Eamo5/NiimPrintX>
- D110_M issue and original V2 protocol contribution:
  <https://github.com/labbots/NiimPrintX/issues/32>
  <https://github.com/labbots/NiimPrintX/pull/6>
  (LorisPolenz, PR head `18ffd0c60819e291843c80f1cf5f66f8fc4ee472`).

Credit to Labbots, Eamo5, LorisPolenz and the NiimPrintX contributors. LICENSE
preserves the upstream GPL text. The gateway, journal, claims, validation and tests
are new; the protocol adaptation remains a GPL derivative, not a claim of clean-room work.

Protocol facts were cross-checked against MultiMote's niimbluelib:
<https://github.com/MultiMote/niimbluelib>, particularly `commands.ts`,
`packet_generator.ts`, `packet_parser.ts`, `B1PrintTask.ts`, `D110MV4PrintTask.ts`.
No TypeScript code is bundled. These are reverse-engineered protocols, not manufacturer
certification. All profiles need validation with the user's actual firmware.

Differences from the fork: explicit byte order, model-specific raster padding, modern Bleak
connection handling, fixed configured identity, persistent notifications with checked
frames, bounded operations, checked acknowledgments and truthful uncertain outcomes.
The fork's fixed two-second delay is not treated as confirmation of printing.

RFID/paper layouts and B1 metadata/sequence were cross-checked against niimbluelib
revision `a78fd7a1bf4585b81add27bc16b57e5f2ee3d50d`: `src/packets/packet_parser.ts`,
`src/packets/packet_generator.ts`, `src/printer_models.ts`,
`src/print_tasks/B1PrintTask.ts` and `AbstractPrintTask.ts`.
Only the known 18-byte paper-info layout is decoded; other layouts remain unknown.

Power configuration uses the documented PrinterInfo keys 7/8/9 and SetAutoShutdownTime
command from niimbluelib's `src/packets/enumerations.ts` and `packet_generator.ts`,
reviewed 2026-10-25. Index semantics remain explicitly model-dependent.
