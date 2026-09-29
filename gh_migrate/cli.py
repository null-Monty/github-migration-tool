"""Command-line interface for gh-migrate."""

import argparse
import sys

from .advice import Context, advise
from .github import ApiError, GitHubClient, resolve_token
from .migrate import (
    ALREADY, BLOCKED, DONE, FAILED, PENDING, READY, UNVERIFIED, VERIFIED, preflight, preflight_via, same,
    transfer_all,
)
from .ui import Console

EXIT_OK, EXIT_FAILED, EXIT_PENDING = 0, 1, 3  # 2 is argparse's usage-error code

# status -> (glyph, colour)
LOOK = {
    READY: ("ok", "green"),
    ALREADY: ("ok", "dim"),
    VERIFIED: ("ok", "green"),
    UNVERIFIED: ("warn", "yellow"),
    PENDING: ("wait", "yellow"),
    BLOCKED: ("fail", "red"),
    FAILED: ("fail", "red"),
}
STATUS_WIDTH = max(len(status) for status in LOOK)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gh-migrate",
        description="Move GitHub repositories to another account using GitHub's native transfer, "
        "so issues, PRs, stars and settings move with them.",
    )
    parser.add_argument("--source", required=True, help="account that owns the repositories now")
    parser.add_argument("--target", required=True, help="account that should own them")
    parser.add_argument("repos", nargs="*", metavar="REPO", help="repository names to move")
    parser.add_argument("-f", "--repos-file", help="file with one repository name per line (# comments allowed)")
    parser.add_argument("--dry-run", action="store_true", help="check everything, transfer nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--wait", type=int, default=120, metavar="SECONDS",
                        help="how long to wait for transfers to complete (default: 120)")
    parser.add_argument("--via", metavar="ORG",
                        help="move through an organization the target owns, so a personal target "
                        "needs no emailed acceptance (needs a gh login for both accounts)")
    return parser


def collect_names(args, parser):
    raw = list(args.repos)
    if args.repos_file:
        try:
            with open(args.repos_file, encoding="utf-8") as handle:
                raw += [line.split("#")[0].strip() for line in handle]
        except OSError as err:
            parser.error(f"can't read {args.repos_file}: {err}")

    names = []
    for entry in filter(None, raw):
        owner, _, name = entry.rpartition("/")
        if owner and not same(owner, args.source):
            parser.error(f"{entry!r} is not in the source account {args.source!r}")
        if not any(same(name, seen) for seen in names):
            names.append(name)
    if not names:
        parser.error("no repositories given (pass names and/or --repos-file)")
    return names


def main(argv=None, *, client=None, target_client=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    names = collect_names(args, parser)
    if same(args.source, args.target):
        parser.error("--source and --target are the same account")
    if args.via and (same(args.via, args.source) or same(args.via, args.target)):
        parser.error("--via must be an organization other than --source and --target")

    origin = None
    if client is None:
        found = resolve_token(args.source)
        if not found:
            parser.error("no GitHub token found; run `gh auth login` or set GH_MIGRATE_TOKEN")
        token, origin = found
        client = GitHubClient(token)
    if args.via and target_client is None:
        found = resolve_token(args.target, only_gh_login=True)
        if not found:
            parser.error(f"--via also needs a gh login for {args.target}; run `gh auth login` as {args.target}")
        target_client = GitHubClient(found[0])

    out = Console()
    try:
        return run(client, args, names, out, origin, target_client)
    except ApiError as err:
        out.line()
        bullet(out, out.paint(out.glyph("fail"), "red"), f"GitHub API error: {err}", "red")
        return EXIT_FAILED
    except KeyboardInterrupt:
        out.line()
        bullet(out, out.paint(out.glyph("warn"), "yellow"),
               "Interrupted. Transfers already requested continue on GitHub's side.", "yellow")
        return EXIT_FAILED


def notice(out, kind, text):
    color = {"ok": "green", "warn": "yellow", "fail": "red"}[kind]
    bullet(out, out.paint(out.glyph(kind), color), text, color)


def show_task(out, task, width):
    symbol, color = LOOK[task.status]
    status = task.status.ljust(STATUS_WIDTH) if task.detail else task.status
    out.line("  " + out.paint(out.glyph(symbol), color) + " " + task.name.ljust(width) + "  "
             + out.paint(status, color) + (out.paint("  " + task.detail, "dim") if task.detail else ""))


def connect(gh, args, out, token_origin):
    """Look up who we are and both accounts. Returns a Context, or None after saying what's wrong."""
    me = gh.get("/user")["login"]
    accounts = {}
    for account in (args.source, args.target):
        accounts[account] = gh.get(f"/users/{account}")
        if accounts[account] is None:
            out.line()
            notice(out, "fail", f"Account '{account}' does not exist.")
            return None
    return Context(
        me=me, source=args.source, target=args.target,
        source_is_org=accounts[args.source]["type"] == "Organization",
        target_is_org=accounts[args.target]["type"] == "Organization",
        token_origin=token_origin,
    )


def print_header(out, args, me):
    paint = out.paint
    out.line()
    out.line("  " + paint("gh-migrate", "bold") + "  " + paint(args.source, "cyan", "bold")
             + f" {paint(out.glyph('arrow'), 'dim')} " + paint(args.target, "cyan", "bold")
             + (paint(f"  via {args.via}", "dim") if args.via else "")
             + (paint("  (dry run)", "yellow") if args.dry_run else ""))
    out.line("  " + paint(f"signed in as {me}", "dim"))
    out.line()


def via_problem(src, dst, args):
    """Why the route through `args.via` can't work, or None if it can."""
    if not same(dst.get("/user")["login"], args.target):
        return f"The second login must be {args.target}. Run `gh auth login` and sign in as {args.target}."
    org = src.get(f"/users/{args.via}")
    if org is None or org["type"] != "Organization":
        return f"{args.via} is not an organization. Create one at https://github.com/organizations/plan"
    for gh, account, need_owner in ((src, args.source, False), (dst, args.target, True)):
        membership = gh.get(f"/user/memberships/orgs/{args.via}")
        if not membership or membership["state"] != "active" or (need_owner and membership["role"] != "admin"):
            return (f"{account} must be {'an owner' if need_owner else 'a member'} of {args.via}. "
                    f"Invite it at https://github.com/orgs/{args.via}/people and accept the invitation.")
    return None


def run(gh, args, names, out, token_origin, target_gh=None):
    ctx = connect(gh, args, out, token_origin)
    if ctx is None:
        return EXIT_FAILED
    problem = via_problem(gh, target_gh, args) if args.via else None
    if problem:
        out.line()
        notice(out, "fail", problem + " Nothing was changed.")
        return EXIT_FAILED
    print_header(out, args, ctx.me)

    if args.via:
        plans = [preflight_via(gh, target_gh, args.source, args.via, args.target, name) for name in names]
        tasks = [task for task, _ in plans]
        first_hop = [task for task, needed in plans if needed]
    else:
        tasks = [preflight(gh, args.source, args.target, name) for name in names]
    if all(task.status == READY for task in tasks):
        notice(out, "ok", f"{len(tasks)} ready: {', '.join(task.name for task in tasks)}")
    else:
        for task in tasks:
            show_task(out, task, max(len(name) for name in names))

    blocked = [task for task in tasks if task.status == BLOCKED]
    if blocked:
        out.line()
        notice(out, "fail", f"{len(blocked)} blocked. Nothing was changed.")
        show_advice(out, advise(tasks, ctx))
        return EXIT_FAILED

    todo = [task for task in tasks if task.status == READY]
    if not todo:
        out.line()
        notice(out, "ok", "Nothing to do.")
        return EXIT_OK
    if args.dry_run:
        out.line("    " + out.paint("Dry run: nothing changed. Re-run without --dry-run to transfer.", "dim"))
        return EXIT_OK

    if not ctx.target_is_org and not args.via:
        notice(out, "warn", f"{args.target} is a personal account and must accept each transfer by email.")
    if not args.yes and not confirmed(out, args.target, len(todo)):
        out.line()
        notice(out, "fail", "Aborted. Nothing was changed.")
        return EXIT_FAILED

    out.line()
    width = max(len(name) for name in names)

    def show(task):
        show_task(out, task, width)

    if args.via:
        move_via(gh, target_gh, args, [task for task in first_hop if task.status == READY], todo, show)
    else:
        transfer_all(gh, args.source, args.target, todo, wait=args.wait, on_update=show)
    return summarize(out, tasks, ctx)


def move_via(src, dst, args, first_hop, todo, show):
    """source -> via as the source account, then via -> target as the target account.

    The second hop moves each repo into the account that asks for it, which GitHub completes
    without an emailed acceptance. Only repos that made it into `via` go on; the rest are shown
    where they stopped.
    """
    transfer_all(src, args.source, args.via, first_hop, wait=args.wait,
                 on_update=lambda task: None if task.status in DONE else show(task))
    for task in first_hop:
        if task.status in DONE:
            task.status, task.detail = READY, ""
    transfer_all(dst, args.via, args.target, [task for task in todo if task.status == READY],
                 wait=args.wait, on_update=show)


def summarize(out, tasks, ctx):
    """Print the outcome counts, any advice and next steps; return the exit code."""
    failed = [task for task in tasks if task.status == FAILED]
    pending = [task for task in tasks if task.status == PENDING]
    done = [task for task in tasks if task.status in DONE]
    counts = (
        (done, "done", "ok", "green"),
        (pending, "pending", "wait", "yellow"),
        (failed, "failed", "fail", "red"),
    )
    out.line()
    out.line("  " + "   ".join(
        out.paint(f"{out.glyph(symbol)} {len(group)} {label}", color)
        for group, label, symbol, color in counts if group
    ))
    if failed:
        show_advice(out, advise(tasks, ctx))
    if pending:
        out.line()
        out.wrapped(f"{ctx.target} must accept the emailed request(s); then re-run this command to confirm.", 2)
        if not ctx.target_is_org:
            out.wrapped("No email? Check spam and https://github.com/settings/emails", 2)
    if failed:
        return EXIT_FAILED
    return EXIT_PENDING if pending else EXIT_OK


def bullet(out, mark, text, *styles):
    """`  <mark> text`, wrapped with a hanging indent."""
    first, *rest = out.wrap(text, 4)
    out.line("  " + mark + " " + out.paint(first, *styles))
    for row in rest:
        out.line("    " + out.paint(row, *styles))


def confirmed(out, target, count):
    noun = "repository" if count == 1 else "repositories"
    prompt = f"  {out.paint('?', 'cyan', 'bold')} " + out.paint(f"Type {target} to transfer {count} {noun}: ", "bold")
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return same(answer.strip(), target)


def show_advice(out, items):
    for item in items:
        out.line()
        bullet(out, out.paint(out.glyph("arrow"), "cyan", "bold"), item.heading, "bold")
        for text in item.body:
            if text.startswith("$ "):
                out.line("      " + out.paint(text, "cyan"))
            else:
                out.wrapped(text, 4)


if __name__ == "__main__":
    sys.exit(main())
