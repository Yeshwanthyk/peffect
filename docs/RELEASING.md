# Releasing peffect

Consumers install the published library with `uv add peffect` (Python 3.13+).
This repository's release CI only checks the version, builds the wheel and source
distribution, smoke-tests the installed wheel, and publishes to PyPI. It is not
the full correctness gate: run the repository verifier before merging a release.

## One-time setup

In [PyPI publishing settings](https://pypi.org/manage/account/publishing/), add a
pending GitHub publisher for a new project (or a publisher under the existing
project's Publishing page):

- PyPI project: `peffect`
- GitHub owner: `Yeshwanthyk`
- Repository: `peffect`
- Workflow filename: `release.yml`
- Environment: `pypi`

Create the matching `pypi` environment in GitHub repository Settings → Environments.
The workflow requests an OIDC token; no PyPI API token or repository secret is needed.
Pending publisher registration does not reserve a name; the first successful upload
creates the project. PyPI artifacts are public even if the GitHub repository is private.

## Every release

1. On a feature/release branch, use `uv version <version>` to update `pyproject.toml`
   and `uv.lock`. Include the changes in a PR. Do not bump again if the intended
   version is already set.
2. Run `.claude/skills/verify-peffect/run.sh`; run the formal verifier when required
   by AGENTS.md. Review and merge the PR, including `.github/workflows/release.yml`.
3. Fetch the merged main branch and tag that commit, not an unmerged feature head:

   ```sh
   git fetch origin main
   git tag -a v0.1.2 origin/main -m "peffect 0.1.2"
   git push origin v0.1.2
   ```

   Replace `0.1.2` with the version in that commit. The workflow rejects a mismatch.
4. Check the **Publish to PyPI** Actions run and the PyPI release, then verify from
   outside the source checkout:

   ```sh
   uv run --no-project --python 3.13 --with peffect==0.1.2 python -I -c 'import asyncio; from peffect import run, succeed; print(asyncio.run(run(succeed(42))))'
   ```

Existing consumers upgrade with `uv lock --upgrade-package peffect && uv sync`.
PyPI does not allow replacing uploaded release files. Fix a published release by
making a new version, not moving its tag. Do not push the first release tag until
the one-time publisher setup is complete. For publishing details see
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) and
[uv package publishing](https://docs.astral.sh/uv/guides/package/).
