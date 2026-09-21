"""Turn blocked and failed repositories into concrete steps the user can take."""

import os
from dataclasses import dataclass

from .migrate import ALREADY_TAKEN, MOVED, NAME_TAKEN, NO_ADMIN, NOT_FOUND, REFUSED, same

DOCS_URL = "https://docs.github.com/en/repositories/creating-and-managing-repositories/transferring-a-repository"
TOKENS_URL = "https://github.com/settings/tokens"

# Advice body lines: "## " starts a sub-heading, "$ " is a command to run, anything else is prose.


@dataclass
class Context:
    me: str
    source: str
    target: str
    source_is_org: bool
    target_is_org: bool
    token_origin: str | None = None
    windows: bool = os.name == "nt"


@dataclass
class Advice:
    heading: str
    body: list


def wrong_account(ctx):
    """Signed in as someone other than the personal account that owns the repositories."""
    return not ctx.source_is_org and not same(ctx.me, ctx.source)


def advise(tasks, ctx):
    names = {}
    for task in tasks:
        if task.cause:
            names.setdefault(task.cause, []).append(task.name)

    items = []
    if wrong_account(ctx) and (NO_ADMIN in names or NOT_FOUND in names):
        items.append(_sign_in_as_source(ctx))
        names.pop(NO_ADMIN, None)
        names.pop(NOT_FOUND, None)
    if NO_ADMIN in names:
        items.append(_grant_admin(ctx, names[NO_ADMIN]))
    if NOT_FOUND in names:
        items.append(_find_repos(ctx, names[NOT_FOUND]))
    if NAME_TAKEN in names:
        items.append(_name_taken(ctx, names[NAME_TAKEN]))
    if MOVED in names:
        items.append(Advice(
            f"Use the current name of {_list(names[MOVED])}",
            ["These repositories were renamed or moved; the detail above shows where they are now."],
        ))
    if ALREADY_TAKEN in names:
        items.append(_already_taken(ctx, names[ALREADY_TAKEN]))
    if REFUSED in names:
        items.append(_refused(ctx, names[REFUSED]))
    return items


def _list(names):
    return ", ".join(names)


def _sign_in_as_source(ctx):
    body = [
        f"You're authenticated as {ctx.me}, but these repositories belong to {ctx.source}. Only "
        f"{ctx.source} can transfer them; other accounts can at most read them."
    ]
    if ctx.token_origin and ctx.token_origin.startswith("$"):
        body += [
            f"The token came from the {ctx.token_origin[1:]} environment variable and belongs to {ctx.me}. "
            f"Replace it with a token created while signed in as {ctx.source}:",
            *_set_token_lines(ctx),
        ]
    else:
        body += [
            "## Option A: GitHub CLI",
            "$ gh auth login",
            f"Choose GitHub.com and sign in as {ctx.source} in the browser (sign out of {ctx.me} on "
            f"github.com first, or use a private window). The tool finds the {ctx.source} login "
            "automatically, there's nothing to switch.",
            "## Option B: personal access token",
            f"While signed in as {ctx.source}, create a classic token with the 'repo' scope at "
            f"{TOKENS_URL}, then:",
            *_set_token_lines(ctx),
        ]
    body.append("Then re-run the same command.")
    return Advice(f"Sign in as {ctx.source}", body)


def _set_token_lines(ctx):
    if ctx.windows:
        return ['$ $env:GH_MIGRATE_TOKEN = "ghp_..."']
    return ["$ export GH_MIGRATE_TOKEN=ghp_..."]


def _grant_admin(ctx, names):
    if ctx.source_is_org:
        body = [
            f"{ctx.source} is an organization: you must be an organization owner, or have the Admin "
            "role on the repository. Ask an owner to grant it (repository Settings > Collaborators "
            "and teams) or to run this tool themselves.",
            f"If {ctx.source} enforces SAML SSO, also authorize your token for it "
            f"({TOKENS_URL} > Configure SSO).",
        ]
    else:
        body = [
            f"You're signed in as {ctx.source}, but the token doesn't grant admin access. Use a "
            "classic token with the 'repo' scope, or a fine-grained token with Repository "
            "permission 'Administration: Read and write' on these repositories.",
            f"Create one at {TOKENS_URL}, then:",
            *_set_token_lines(ctx),
        ]
    return Advice(f"Get admin access to {_list(names)}", body)


def _find_repos(ctx, names):
    return Advice(
        f"Check the name of {_list(names)}",
        [
            f"No repository with that name was found under {ctx.source}. Check the spelling, that "
            "your token can see private repositories, and that it hasn't already been transferred. "
            "To list what exists:",
            f"$ gh repo list {ctx.source} --limit 200",
        ],
    )


def _name_taken(ctx, names):
    return Advice(
        f"Free up the name in {ctx.target}: {_list(names)}",
        [
            f"{ctx.target} already has a repository with that name. Rename or delete it (repository "
            "Settings > General) and re-run. The tool never overwrites anything.",
        ],
    )


def _already_taken(ctx, names):
    return Advice(
        f"Look for a transfer that's already in progress: {_list(names)}",
        [
            f"GitHub says the repository is 'already taken' at {ctx.target}. If the tool couldn't see "
            f"anything under that name in {ctx.target}, the most likely cause is a transfer request for "
            f"it that's already pending, for example one started earlier from the web UI. Nothing was "
            "moved by this run.",
            "## Check for a pending request",
            f"In the email inbox (and spam folder) of {ctx.target}, look for a GitHub email about a "
            f"repository transfer from {ctx.source}. Accepting it completes the migration.",
            f"Or, signed in as {ctx.source}, open the repository's Settings page and look in the Danger "
            "Zone for a pending transfer. Cancel it there, then re-run.",
            "## Rule out a name clash",
            f"Check that {ctx.target} has no repository with that name, including one deleted "
            "recently (Settings > Repositories > Deleted repositories).",
        ],
    )


def _refused(ctx, names):
    body = [
        f"GitHub rejected the transfer of {_list(names)}; its reason is shown above. Nothing was "
        "moved for these, so it's safe to fix the cause and re-run.",
    ]
    if ctx.target_is_org:
        body.append(f"{ctx.target} is an organization: you need permission to create repositories there.")
    body.append(f"Docs: {DOCS_URL}")
    return Advice("Resolve GitHub's objection", body)
