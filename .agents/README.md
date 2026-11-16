# Development setup

Run `./.agents/setup` from any directory. Re-run after a Scotty session resumes:
dependencies and installed tools are not preserved by its workspace snapshot.

The script targets Scotty's Debian image (passwordless sudo or root) and existing
macOS development hosts. It installs missing Debian prerequisites, uv 0.11.10,
Bun 1.3.13, managed Python 3.13, and dependencies from `uv.lock` and the parity
`bun.lock`. The development lock includes Harrow 0.4.1 for the complexity gate;
no global Harrow installation is required. Python patch releases and Debian packages follow their repositories;
this is a repeatable setup, not a hermetic image. macOS requires git, curl, unzip,
Node and Java already installed. Formal verification requires Java 11 or newer;
Debian setup installs Java 17 when missing.

Only an absent vendor submodule is initialized. An existing checkout at a different
revision fails without resetting it. No source files or lockfiles are rewritten.
Linux links uv and Bun into `/usr/local/bin` for subsequent agent turns. On macOS,
newly installed tools live in `~/.local/share/peffect-tools`; add its `bin` and
`bun/bin` directories to PATH if they were not already available.

peffect is a library: setup starts no server and requires no hatch URL.
Run `.claude/skills/verify-peffect/run.sh` for application parity, tests, lint and
types; `.claude/skills/verify-formal-kernel/run.sh` for bounded kernel verification;
and `uv build` for wheel and source distributions. Setup itself only checks tools
and the editable package import; it does not claim those verification suites pass.
