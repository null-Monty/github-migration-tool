"""Turn blocked and failed repositories into short, concrete next steps."""

import os
from dataclasses import dataclass

from .migrate import ALREADY_TAKEN, MOVED, NAME_TAKEN, NO_ADMIN, NOT_FOUND, REFUSED, same

# Body lines starting with "$ " are commands to run; anything else is prose.


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
        items.append(Advice(f"Not found: {_list(names[NOT_FOUND])}", [
            "Check the spelling, or list what exists:",
            f"$ gh repo list {ctx.source} --limit 200",
        ]))
    if NAME_TAKEN in names:
        items.append(Advice(f"{ctx.target} already has: {_list(names[NAME_TAKEN])}", [
            "Rename or delete them there, then re-run.",
        ]))
    if MOVED in names:
        items.append(Advice(f"Renamed or moved: {_list(names[MOVED])}", [
            "Use the current names shown above.",
        ]))
    if ALREADY_TAKEN in names:
        items.append(_already_taken(ctx, names[ALREADY_TAKEN]))
    if REFUSED in names:
        items.append(_refused(ctx, names[REFUSED]))
    return items


def _list(names):
    return ", ".join(names)


def _set_token(ctx):
    if ctx.windows:
        return '$ $env:GH_MIGRATE_TOKEN = "ghp_..."'
    return "$ export GH_MIGRATE_TOKEN=ghp_..."


def _sign_in_as_source(ctx):
    heading = f"Sign in as {ctx.source}, not {ctx.me}"
    if ctx.token_origin and ctx.token_origin.startswith("$"):
        return Advice(heading, [
            f"Your {ctx.token_origin[1:]} variable is {ctx.me}'s token. Replace it:",
            _set_token(ctx),
        ])
    return Advice(heading, [
        "$ gh auth login",
        f"Use a private window if your browser is signed in as {ctx.me}. Or set a token for {ctx.source}:",
        _set_token(ctx),
    ])


def _grant_admin(ctx, names):
    if ctx.source_is_org:
        return Advice(f"Ask an owner of {ctx.source} for admin access: {_list(names)}", [
            "Owners and repo admins can transfer. If SAML SSO is enforced, authorize your token for it.",
        ])
    return Advice(f"Use a token with admin access: {_list(names)}", [
        "Classic 'repo' scope, or fine-grained 'Administration: Read and write':",
        _set_token(ctx),
    ])


def _already_taken(ctx, names):
    return Advice(f"Already pending, or name taken: {_list(names)}", [
        f"Look for a transfer request in {ctx.target}'s email (and spam) and accept it,",
        "or cancel it in the repo's Settings > Danger Zone and re-run. Also check for a deleted repo "
        f"with that name in {ctx.target}.",
    ])


def _refused(ctx, names):
    body = ["Reason shown above. Nothing was moved; fix it and re-run."]
    if ctx.target_is_org:
        body.append(f"You need permission to create repositories in {ctx.target}.")
    return Advice(f"GitHub refused: {_list(names)}", body)
