# github-migration-tool

This is a tool for migrating GitHub repositories from one account to another.

It uses GitHub's native repository transfer rather than clone-and-push, so each repository is *moved*, not copied: git history, issues, pull requests, wiki, stars and watchers come along, and nothing is left in the source account except the redirect GitHub keeps so old URLs and git remotes keep working. Nothing is cloned locally either, so there are no temp files to clean up.

Python 3.10+, standard library only.

## Usage

```sh
python -m gh_migrate --source OLD_ACCOUNT --target NEW_ACCOUNT repo-one repo-two ...
python -m gh_migrate --source OLD_ACCOUNT --target NEW_ACCOUNT -f repos.txt
```

(or `pip install .` and run `gh-migrate ...`). `repos.txt` holds one repository name per line;
blank lines and `# comments` are ignored.

| Option | |
| --- | --- |
| `--dry-run` | Run every check, transfer nothing. **Do this first.** |
| `-y`, `--yes` | Skip the "type the target account name" confirmation. |
| `--wait SECONDS` | How long to watch for transfers to finish (default 120). |

Every repository is checked before anything moves (exists, you have admin on it, the target has
no repository of the same name). If any check fails, nothing is transferred.

### Authentication

The token must belong to a user with **admin** rights on the source repositories (classic token with the `repo` scope, or fine-grained with repository Administration: write). It is taken from `GH_MIGRATE_TOKEN`, `GITHUB_TOKEN` or `GH_TOKEN`, otherwise from the `gh` CLI (`gh auth token --user <source>`, then the active account). The tool prints who it is signed in as and where the token came from, and warns if that isn't the account that owns the repositories.

The most common blocker is being signed in as the wrong account, typically the *target*. Only the source account's owner can transfer its repositories, so `gh auth login` as the source account (the tool then finds that login by itself), or create a token for it and set `GH_MIGRATE_TOKEN`.

### Personal target accounts need to accept

- **Target is an organization** and your user is an owner (or may create repos there): transfers complete within seconds.
- **Target is a personal account:** GitHub emails the owner a request per repository, and they have to accept it (it expires after about a day). There is no API for accepting, so the tool reports these as `pending`. Once they've accepted, re-run the same command to check on them.

### When something is blocked

The tool doesn't just say "blocked": it ends with a **How to unblock** section listing what to do for each cause: signing in as the right account, getting admin rights or SSO-authorizing a token, correcting a repository name, clearing a name clash in the target, or resolving GitHub's own objection to a transfer. Nothing is changed while any repository is blocked.

Output is colour-coded (green done/ready, yellow pending/warning, red blocked/failed) on a terminal, and plain ASCII when piped or when `NO_COLOR` is set.

### Results and exit codes

| Status | Meaning |
| --- | --- |
| `transferred` | Confirmed at the target (same repository id). |
| `transferred (unverified)` | Gone from the source, but not visible to your token at the target, typically a private repo moved into an account you can't read. Confirm in the target account. |
| `already migrated` | Already owned by the target; skipped. |
| `pending` | Requested, waiting on the new owner to accept. |
| `failed` / `blocked` | See the detail column. |

Exit code `0` = everything done, `1` = something failed or was blocked, `2` = nothing failed but some transfers are still pending.

## Things to know

- Transfers are hard to undo (you'd need to transfer back) and the old `OLD/repo` redirect stops working if that name is later reused in the source account. Use `--dry-run` first.
- Webhooks, secrets and deploy keys stay attached to the repository, but check anything that refers to the old owner name: CI configs, package names, badges, submodule URLs, and any clones you have locally (`git remote set-url origin ...`).
- See GitHub's ["Transferring a repository"](https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository)
  docs for edge cases (forks, Pages, Packages, teams and access).

## Development

```sh
python -m unittest discover
```

The tests run against an in-memory fake of the GitHub API; no real repositories are touched.
