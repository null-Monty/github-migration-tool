"""Preflight checks, transfer and verification of repositories between two accounts."""

import time
from dataclasses import dataclass

from .github import ApiError

# Preflight outcomes
READY = "ready"
ALREADY = "already migrated"
BLOCKED = "blocked"

# Transfer outcomes
VERIFIED = "transferred"
UNVERIFIED = "transferred (unverified)"
PENDING = "pending"
FAILED = "failed"

DONE = {ALREADY, VERIFIED, UNVERIFIED}

# Why a repository is blocked or failed; gh_migrate.advice turns these into next steps
NOT_FOUND = "not-found"
NO_ADMIN = "no-admin"
NAME_TAKEN = "name-taken"
MOVED = "moved"
REFUSED = "refused"
ALREADY_TAKEN = "already-taken"


@dataclass
class RepoTask:
    name: str
    status: str = READY
    detail: str = ""
    cause: str = ""
    repo_id: int = 0
    private: bool | None = None  # unknown until the repository has been found


def same(a, b):
    return a.lower() == b.lower()


def preflight(gh, source, target, name):
    """Decide whether `source/name` can be transferred to `target`, without changing anything."""
    repo = gh.get(f"/repos/{source}/{name}")
    if repo is None:
        return RepoTask(name, BLOCKED, f"not found under {source}", cause=NOT_FOUND)

    task = RepoTask(repo["name"], repo_id=repo["id"], private=repo["private"])
    owner = repo["owner"]["login"]
    if same(owner, target):
        task.status, task.detail = ALREADY, f"already owned by {target}"
    elif not same(owner, source):
        task.status, task.detail, task.cause = BLOCKED, f"now lives at {repo['full_name']}", MOVED
    elif not repo.get("permissions", {}).get("admin"):
        task.status, task.detail, task.cause = BLOCKED, "token has no admin permission on it", NO_ADMIN
    elif gh.get(f"/repos/{target}/{task.name}") is not None:
        task.status, task.detail, task.cause = BLOCKED, f"{target} already has a repo with this name", NAME_TAKEN
    return task


def _check_progress(gh, source, target, task):
    """One look at where the repository is now: PENDING, VERIFIED or UNVERIFIED.

    Repository ids survive a transfer, so matching the id proves it's the same repo. While a
    transfer is pending the source path still serves it; afterwards GitHub redirects the old
    path to the new owner. If neither path is visible to the token (e.g. a private repo moved
    into an account the token can't see) it has at least left the source, but we can't confirm
    where it landed.
    """
    for owner in (source, target):
        repo = gh.get(f"/repos/{owner}/{task.name}")
        if repo and repo["id"] == task.repo_id:
            return PENDING if same(repo["owner"]["login"], source) else VERIFIED
    return UNVERIFIED


def transfer_all(gh, source, target, tasks, *, wait, interval=5.0, on_update=None,
                 sleep=time.sleep, clock=time.monotonic):
    """Start a transfer for every READY task, then watch them for up to `wait` seconds.

    Updates each task's status in place and calls `on_update(task)` as statuses settle.
    """
    notify = on_update or (lambda task: None)
    in_flight = []
    for task in tasks:
        if task.status != READY:
            continue
        try:
            gh.post(f"/repos/{source}/{task.name}/transfer", {"new_owner": target})
        except ApiError as err:
            cause = ALREADY_TAKEN if "already been taken" in err.message else REFUSED
            task.status, task.detail, task.cause = FAILED, err.message, cause
            notify(task)
        else:
            task.status = PENDING
            in_flight.append(task)

    deadline = clock() + wait
    while in_flight:
        still_pending = []
        for task in in_flight:
            try:
                status = _check_progress(gh, source, target, task)
            except ApiError:
                status = PENDING  # transient API trouble; keep watching
            if status == PENDING:
                still_pending.append(task)
                continue
            task.status = status
            if status == UNVERIFIED:
                task.detail = f"left {source}; not visible to this token, confirm in the {target} account"
            notify(task)
        in_flight = still_pending
        if not in_flight or clock() >= deadline:
            break
        sleep(interval)

    for task in in_flight:
        task.detail = "transfer requested; still waiting for the new owner to accept"
        notify(task)
