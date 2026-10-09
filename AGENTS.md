# niimbot-server agent instructions

Read the canonical [ac-organic-lab AGENTS.md](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/AGENTS.md),
[lab contract Part I](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/docs/AGENTIC_LAB_DESIGN.md)
and [STATUS_SPEC](https://github.com/AccelerationConsortium/ac-organic-lab/blob/main/docs/STATUS_SPEC.md)
before proposing hardware or integration changes. Those rules are binding; this repo
adds no exceptions. Use a local sibling checkout when available.

- This is a separate device service. Clients use lab-skills, claims and approved plans.
- D110_M is not ordinary D110. Do not claim compatibility from packet tests alone.
- Development tests are hardware-free. No scans, connections, prints, radio changes,
  package installation on instrument hosts or deployment without task authorization.
- Default to simulation. Status and documentation must never cause hardware I/O.
- Preserve journaled uncertainty, stable job IDs and no automatic physical retry.
- Keep secrets, hardware identity, local settings and run data out of git.
- Tests: `uv run pytest`; lint: `uv run ruff check .`.
- Preserve GPL provenance and package the served operating guide.
