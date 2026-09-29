<p align="center">
  <img src="assets/logo.svg" alt="gh-migrate: move repositories between GitHub accounts" width="640">
</p>

<h1 align="center">github-migration-tool</h1>

<p align="center">
  This is a tool for migrating GitHub repositories from one account to another.<br>
  Issues, PRs, stars and history move with them. Nothing is left behind.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#personal-accounts-must-accept">Personal accounts</a> ·
  <a href="#troubleshooting">Troubleshooting</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#for-automation-and-ai-agents">Automation</a>
</p>

<p align="center"><sub>Python 3.10+ · standard library only · nothing is cloned locally</sub></p>

---

## Why a transfer, not a copy

`gh-migrate` uses GitHub's built-in repository transfer, so each repo is **moved**, not re-created.

| | Transfer (this tool) | Clone and push |
| --- | :---: | :---: |
| Git history | ✅ | ✅ |
| Issues, PRs, wiki | ✅ moved | ❌ lost |
| Stars and watchers | ✅ | ❌ |
| Old URLs and remotes | ✅ redirect | ❌ break |
| Left in the source account | nothing but the redirect | the full original |

## Quick start

1. **Sign in as the account that owns the repos** (the *source*), not the one receiving them:

   ```sh
   gh auth login
   ```

2. **Dry run.** Checks everything, changes nothing:

   ```sh
   python -m gh_migrate --source OLD --target NEW --dry-run repo-one repo-two
   ```

3. **Transfer.** Same command without `--dry-run`, then type the target account name to confirm:

   ```sh
   python -m gh_migrate --source OLD --target NEW repo-one repo-two
   ```

For a dozen or more repos, put the names in a file (one per line, `#` comments allowed) and use `-f repos.txt`. Any `/*.txt` file in the project root is already gitignored.

```text
  gh-migrate  alice → acme
  signed in as alice

  ✓ 3 ready: api-server, website, dotfiles
  ? Type acme to transfer 3 repositories: acme

  ✓ api-server  transferred
  ✓ website     transferred
  ✓ dotfiles    transferred

  ✓ 3 done
```

Install as a command instead with `pip install .`, then run `gh-migrate ...`.

## Usage

```text
gh-migrate --source ACCOUNT --target ACCOUNT [REPO ...] [-f FILE] [--dry-run] [-y] [--wait SECONDS] [--via ORG]
```

| Option | Meaning |
| --- | --- |
| `--source ACCOUNT` | Account (user or org) that owns the repos now. **Required.** |
| `--target ACCOUNT` | Account that should own them. **Required.** |
| `REPO ...` | Repo names to move. `owner/name` is accepted if the owner is the source. |
| `-f`, `--repos-file FILE` | File with one repo name per line. Blank lines and `#` comments are ignored. |
| `--dry-run` | Run every check, transfer nothing. |
| `-y`, `--yes` | Skip the "type the target name" confirmation. |
| `--wait SECONDS` | How long to wait for transfers to complete. Default `120`. |
| `--via ORG` | Move through an organization the target owns, so a personal target needs no email. See [below](#skip-the-email-with---via). |

### Authentication

The token must belong to a user with **admin** rights on the repos: a classic token with the `repo` scope, or a fine-grained token with *Administration: Read and write*. It is looked up in this order:

1. `GH_MIGRATE_TOKEN`, `GITHUB_TOKEN`, then `GH_TOKEN` environment variables
2. the `gh` CLI login for the source account (`gh auth token --user SOURCE`)
3. the `gh` CLI's active account

```sh
$env:GH_MIGRATE_TOKEN = "ghp_..."     # PowerShell
export GH_MIGRATE_TOKEN=ghp_...       # bash / zsh
```

The tool prints who it is signed in as. If that isn't the source account, it tells you how to fix it.

### Results

| Status | Meaning |
| --- | --- |
| `transferred` | Confirmed at the target (same repository id). |
| `unverified` | Left the source, but your token can't see it at the target, e.g. a private repo. Check the target account. |
| `already migrated` | Already at the target. Skipped, so re-running is safe. |
| `pending` | Waiting for the target account to accept. |
| `blocked` | Failed a pre-check. Nothing was changed. The fix is printed below the list. |
| `failed` | GitHub rejected the transfer. Its reason is shown next to the repo. |

### Exit codes

| Code | Meaning |
| :---: | --- |
| `0` | Everything done, or nothing to do. |
| `1` | Something was blocked or failed, or you declined the prompt. |
| `2` | Bad command-line arguments. |
| `3` | No failures, but some transfers are still pending acceptance. |

## Personal accounts must accept

| Target is | What happens |
| --- | --- |
| An **organization** you own (or may create repos in) | Transfers complete within seconds. |
| A **personal account** | GitHub emails that account one request per repo. It must accept each one; requests expire after about a day, and there is no API to accept them. |

For a personal target the tool reports `pending` and exits with `3`. Once the requests are accepted, run the same command again to confirm. Repos that have arrived show as `already migrated`.

### Skip the email with `--via`

If the emails never arrive, route the move through an organization. Each repo goes `SOURCE -> ORG` as the source account, then `ORG -> TARGET` as the target account. Both steps finish without an email: the first because the source may create repos in the org, the second because the target is moving the repo into its own account.

1. Signed in as the target, create a free org at [github.com/organizations/plan](https://github.com/organizations/plan).
2. Invite the source account under the org's *People* tab, as an **owner** so it can still see private repos once they're in the org. Accept the invitation as the source.
3. Log `gh` in as both accounts (`gh auth login` twice; `gh auth status` lists both).
4. Run the usual command with `--via ORG`, dry run first:

   ```sh
   python -m gh_migrate --source OLD --target NEW --via ORG -f repos.txt --dry-run
   ```

The source account's token is found as usual; the target's comes only from its `gh` login. If a run stops halfway, re-run it: repos already in the org skip the first step. Delete the org once it's empty.

## Troubleshooting

The tool prints the fix for each problem it finds. The common ones:

| You see | Cause | Fix |
| --- | --- | --- |
| `Sign in as SOURCE, not OTHER` | The token belongs to a different account, often the target. | `gh auth login` as the source account, or set `GH_MIGRATE_TOKEN`. |
| `no admin access` (right account) | The token lacks admin rights. | Use a classic `repo` token or a fine-grained *Administration* token. For an org: be an owner or repo admin, and authorize the token for SAML SSO if enforced. |
| `not found` | Typo, or the repo isn't visible to this token. | `gh repo list SOURCE --limit 200` |
| `name taken in TARGET` | The target already has a repo with that name. | Rename or delete it there. The tool never overwrites. |
| `Repository has already been taken` | A transfer is already pending, or the name is taken. | Accept the emailed request, or cancel it under the repo's *Settings › Danger Zone*, then re-run. |
| `pending`, but no email arrives | The target account's email is unverified or unwatched. | Check spam and [github.com/settings/emails](https://github.com/settings/emails) on the target account, or use [`--via`](#skip-the-email-with---via). |

## How it works

1. Find a token, then read `GET /user`, `GET /users/{source}` and `GET /users/{target}`.
2. Pre-check each repo with `GET /repos/{source}/{repo}`: it exists, the source owns it, the token has `permissions.admin`, and `GET /repos/{target}/{repo}` is a 404.
3. If any repo is blocked, print the fixes and stop. Nothing has changed.
4. Ask for confirmation, unless `--yes`.
5. Request each transfer with `POST /repos/{source}/{repo}/transfer` and body `{"new_owner": target}`.
6. Poll until each repo shows up at the target, up to `--wait` seconds. A repo's numeric id survives a transfer, so a matching id proves it is the same repo.
7. Print a summary, any fixes and the exit code.

The tool never clones, deletes, renames or force-overwrites anything, and makes no write calls other than the transfer requests.

| File | Role |
| --- | --- |
| [`gh_migrate/cli.py`](gh_migrate/cli.py) | Arguments, the overall flow and the printed output. |
| [`gh_migrate/migrate.py`](gh_migrate/migrate.py) | Pre-checks, transfer requests and verification. |
| [`gh_migrate/advice.py`](gh_migrate/advice.py) | Turns each failure cause into a short fix. |
| [`gh_migrate/github.py`](gh_migrate/github.py) | Minimal GitHub REST client and token lookup. |
| [`gh_migrate/ui.py`](gh_migrate/ui.py) | Colour, glyphs and wrapping. |
| [`tests/test_migrate.py`](tests/test_migrate.py) | Tests against an in-memory fake of the GitHub API. |

## For automation and AI agents

```sh
gh-migrate --source OLD --target NEW -f repos.txt --dry-run   # 1. always safe; check the exit code
gh-migrate --source OLD --target NEW -f repos.txt --yes       # 2. non-interactive transfer
```

- **Always dry-run first.** Exit `0` means every repo can be transferred. Exit `1` means at least one is blocked, and the printed fix says why. A blocked run changes nothing.
- **`--yes` is required when there is no terminal.** Without it, the confirmation prompt reads end-of-input and aborts with exit `1`.
- **Output is plain text on stdout** (colour and glyphs are dropped when piped or when `NO_COLOR` is set). There is no JSON mode. Rely on the **exit code** rather than parsing the text.
- **Retrying is safe.** Repos already at the target are skipped as `already migrated`.
- **Some steps need a human.** Acceptance by a personal target account happens by email and cannot be automated. Exit `3` means "wait for acceptance, then re-run".
- **Credentials:** pass a token through `GH_MIGRATE_TOKEN`. It must belong to the *source* account.
- **Transfers are hard to undo.** Treat `--yes` as a destructive action and confirm the account names with the user first.

## Good to know

- The old-URL redirect stops working if that repo name is later reused in the source account.
- Webhooks, secrets and deploy keys stay attached. Update anything that names the old owner: CI configs, badges, submodules and local clones (`git remote set-url origin ...`).
- GitHub's [transfer docs](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository) cover edge cases such as forks, Pages and Packages.

## Development

```sh
python -m unittest discover
```

The tests use an in-memory fake of the GitHub API, so no real repositories are touched.
