"""Command-line interface for gh-migrate."""

import argparse
import sys

from .advice import Context, advise, wrong_account
from .github import ApiError, GitHubClient, resolve_token
from .migrate import (
    ALREADY, BLOCKED, DONE, FAILED, PENDING, READY, UNVERIFIED, VERIFIED, preflight, same, transfer_all,
)
from .ui import Console

EXIT_OK, EXIT_FAILED, EXIT_PENDING = 0, 1, 2

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
VISIBILITY_WIDTH = len("private") + 2


def build_parser():
    parser = argparse.ArgumentParser(
        prog="gh-migrate",
        description="Move GitHub repositories from one account to another using GitHub's "
        "native repository transfer, so issues, PRs, stars, wikis, releases and settings "
        "move with them and nothing is left in the source account.",
    )
    parser.add_argument("--source", required=True, help="account (user or org) that owns the repositories now")
    parser.add_argument("--target", required=True, help="account (user or org) that should own them")
    parser.add_argument("repos", nargs="*", metavar="REPO", help="repository names to move")
    parser.add_argument("-f", "--repos-file", help="file with one repository name per line (# comments allowed)")
    parser.add_argument("--dry-run", action="store_true", help="run all checks but transfer nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--wait", type=int, default=120, metavar="SECONDS",
                        help="how long to wait for transfers to complete (default: 120)")
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


def main(argv=None, *, client=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    names = collect_names(args, parser)
    if same(args.source, args.target):
        parser.error("--source and --target are the same account")

    origin = None
    if client is None:
        found = resolve_token(args.source)
        if not found:
            parser.error(f"no GitHub token found; run `gh auth login` as {args.source}, "
                         "or set GH_MIGRATE_TOKEN")
        token, origin = found
        client = GitHubClient(token)

    out = Console()
    try:
        return run(client, args, names, out, origin)
    except ApiError as err:
        out.line()
        out.line("  " + out.paint(f"{out.glyph('fail')} GitHub API error: {err}", "red"))
        return EXIT_FAILED
    except KeyboardInterrupt:
        out.line()
        out.line("  " + out.paint("Interrupted. Any transfer already requested continues on GitHub's side.", "yellow"))
        return EXIT_FAILED


def run(gh, args, names, out, token_origin):
    source, target = args.source, args.target
    paint, glyph = out.paint, out.glyph

    def notice(kind, text):
        color = {"ok": "green", "warn": "yellow", "fail": "red"}[kind]
        first, *rest = out.wrap(text, 4)
        out.line("  " + paint(glyph(kind), color) + " " + paint(first, color))
        for row in rest:
            out.line("    " + paint(row, color))

    title = paint("gh-migrate", "bold") + "  " + paint(source, "cyan", "bold") \
        + f" {paint(glyph('arrow'), 'dim')} " + paint(target, "cyan", "bold")
    out.line()
    out.line("  " + title + (paint("  (dry run)", "yellow") if args.dry_run else ""))
    out.line()

    me = gh.get("/user")["login"]
    accounts = {}
    for account in (source, target):
        accounts[account] = gh.get(f"/users/{account}")
        if accounts[account] is None:
            notice("fail", f"Account '{account}' does not exist on GitHub.")
            return EXIT_FAILED
    ctx = Context(
        me=me, source=source, target=target,
        source_is_org=accounts[source]["type"] == "Organization",
        target_is_org=accounts[target]["type"] == "Organization",
        token_origin=token_origin,
    )

    origin_note = f"  {paint(f'({token_origin})', 'dim')}" if token_origin else ""
    out.line(f"  Signed in as  {paint(me, 'bold')}{origin_note}")
    if wrong_account(ctx):
        notice("warn", f"{source} is a different account, so its repositories can't be transferred with this login.")

    width = max(len(name) for name in names)

    def show(task):
        symbol, color = LOOK[task.status]
        visibility = {None: "-", True: "private", False: "public"}[task.private]
        out.line("  " + paint(glyph(symbol), color) + " " + task.name.ljust(width) + "  "
                 + paint(visibility.ljust(VISIBILITY_WIDTH), "dim")
                 + paint(task.status.ljust(STATUS_WIDTH + 2) if task.detail else task.status, color)
                 + paint(task.detail, "dim"))

    out.heading(f"Checking {len(names)} {'repository' if len(names) == 1 else 'repositories'}")
    tasks = [preflight(gh, source, target, name) for name in names]
    for task in tasks:
        show(task)

    blocked = [task for task in tasks if task.status == BLOCKED]
    if blocked:
        out.line()
        notice("fail", f"{len(blocked)} of {len(tasks)} can't be transferred. Nothing was changed.")
        show_advice(out, advise(tasks, ctx))
        return EXIT_FAILED

    todo = [task for task in tasks if task.status == READY]
    if not todo:
        out.line()
        notice("ok", "Nothing to do: everything is already at the target.")
        return EXIT_OK
    if args.dry_run:
        out.line()
        notice("ok", f"Dry run passed: {len(todo)} ready to transfer. Nothing was changed.")
        out.line(f"    Run the same command without {paint('--dry-run', 'cyan')} to transfer them.")
        return EXIT_OK

    if not ctx.target_is_org:
        out.line()
        notice("warn", f"{target} is a personal account: GitHub will email them one transfer request per "
                       "repository, which they must accept (requests expire after about a day).")
    if not args.yes and not confirmed(out, target, len(todo)):
        out.line()
        notice("fail", "Aborted. Nothing was changed.")
        return EXIT_FAILED

    out.heading("Transferring")
    transfer_all(gh, source, target, todo, wait=args.wait, on_update=show)

    failed = [task for task in tasks if task.status == FAILED]
    pending = [task for task in tasks if task.status == PENDING]
    done = [task for task in tasks if task.status in DONE]
    out.line()
    out.line("  " + "   ".join([
        paint(f"{len(done)} done", "green" if done else "dim"),
        paint(f"{len(pending)} pending", "yellow" if pending else "dim"),
        paint(f"{len(failed)} failed", "red" if failed else "dim"),
    ]))
    if failed:
        show_advice(out, advise(tasks, ctx))
    if pending:
        out.heading("Next step")
        out.wrapped(f"{target} needs to accept the transfer request(s) GitHub emailed them. Once they have, "
                    "re-run the same command to confirm; repositories that have arrived show as "
                    "'already migrated'.", 4)
        if not ctx.target_is_org:
            out.wrapped(f"No email? Check spam, and that {target} has a verified email address at "
                        "https://github.com/settings/emails.", 4)
    if failed:
        return EXIT_FAILED
    return EXIT_PENDING if pending else EXIT_OK


def confirmed(out, target, count):
    noun = "repository" if count == 1 else "repositories"
    out.line()
    out.line("  " + out.paint("Waiting for confirmation. Nothing has been transferred yet.", "yellow", "bold"))
    prompt = f"  {out.paint('?', 'cyan', 'bold')} Type {out.paint(target, 'cyan', 'bold')} and press Enter to transfer {count} {noun}: "
    try:
        answer = input(prompt)
    except EOFError:
        return False
    return same(answer.strip(), target)


def show_advice(out, items):
    if not items:
        return
    out.heading("How to unblock")
    for number, item in enumerate(items, 1):
        out.line()
        out.line("  " + out.paint(f"{number}.", "cyan", "bold") + " " + out.paint(item.heading, "bold"))
        for text in item.body:
            if text.startswith("$ "):
                out.line("       " + out.paint(text, "cyan"))
            elif text.startswith("## "):
                out.line("     " + out.paint(text[3:], "dim", "bold"))
            else:
                out.wrapped(text, 5)


if __name__ == "__main__":
    sys.exit(main())
