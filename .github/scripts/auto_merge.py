"""Merge small list contributions using trusted code and explicit mechanical rules."""

import argparse
import difflib
import ipaddress
import json
import os
import re
import subprocess
from urllib.parse import urlsplit

REPO = "kairichard/awesome-nocode-lowcode"
CHECK = "Auto-merge eligibility"
LABEL = "needs-human-review"
ENTRY = re.compile(r"^- \[([^\[\]]+)\]\((https?://[^\s)]+)\) - (.+)$")


class NeedsReview(Exception):
    pass


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kwargs).stdout.strip()


def api(path, method="GET", data=None, paginate=False):
    args = ["gh", "api", f"repos/{REPO}/{path}", "--method", method]
    if paginate:
        args += ["--paginate", "--slurp"]
    if data is not None:
        args += ["--input", "-"]
    result = json.loads(run(*args, input=json.dumps(data) if data is not None else None))
    return [item for page in result for item in page] if paginate else result


def canonical(url):
    value = urlsplit(url)
    return (value.hostname or "").lower().removeprefix("www.") + value.path.rstrip("/")


def entry_at(lines, index):
    match = ENTRY.fullmatch(lines[index])
    if not match:
        raise NeedsReview("Changed text must be a single Markdown product entry.")
    category = next((line[3:] for line in reversed(lines[:index]) if line.startswith("## ")), None)
    if not category:
        raise NeedsReview("Entries must belong to an existing category.")
    name, url, description = match.groups()
    return {"name": name, "url": url, "description": description, "category": category, "index": index}


def public_url(url):
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment or "." not in host
            or host.endswith((".local", ".localhost", ".internal"))):
        raise NeedsReview("Use a public HTTPS product URL without credentials, tracking parameters or fragments.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return
    raise NeedsReview("IP-address links need human review.")


def eligible_entries(before, after):
    old, new = before.splitlines(), after.splitlines()
    removed, added = [], []
    changed_lines = 0
    for tag, i, j, k, l in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        changed_lines += j - i + l - k
        removed += [entry_at(old, n) for n in range(i, j) if old[n].strip()]
        added += [entry_at(new, n) for n in range(k, l) if new[n].strip()]
    if changed_lines > 40 or not 1 <= len(added) <= 3:
        raise NeedsReview("Automatic merging is limited to one to three entries and 40 changed lines.")
    identity = lambda entry: (entry["category"], entry["name"], entry["url"])
    if any(identity(entry) not in {identity(item) for item in added} for entry in removed):
        raise NeedsReview("Removing, renaming, moving, or changing an existing product URL needs human review.")
    all_entries = [entry_at(new, n) for n, line in enumerate(new) if ENTRY.fullmatch(line)]
    for entry in added:
        public_url(entry["url"])
        if (len(entry["name"]) > 80 or len(entry["description"]) > 400
                or re.search(r"[<>\[\]`]|https?://|www\.", entry["description"])
                or re.search(r"[<>`]|[\x00-\x1f\x7f]", entry["name"] + entry["description"])):
            raise NeedsReview("Use a short plain-text name and description with one product link.")
        name = entry["name"].casefold()
        if sum(item["name"].casefold() == name for item in all_entries) != 1:
            raise NeedsReview("Duplicate product name.")
        if sum(canonical(item["url"]) == canonical(entry["url"]) for item in all_entries) != 1:
            raise NeedsReview("Duplicate product URL.")
        # Existing disorder elsewhere in the list is not introduced by this PR.
        if identity(entry) not in {identity(item) for item in removed}:
            section = [item for item in all_entries if item["category"] == entry["category"]]
            position = section.index(entry)
            if ((position > 0 and section[position - 1]["name"].casefold() > name)
                    or (position + 1 < len(section) and section[position + 1]["name"].casefold() < name)):
                raise NeedsReview("Place the new entry alphabetically between its neighbours.")
    return added


def review_blocked(reviews):
    latest = {}
    for review in reviews:
        if review["state"] in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[review["user"]["login"]] = review["state"]
    return "CHANGES_REQUESTED" in latest.values()


def check_link(url):
    # No GitHub credentials, config file, response body, or website code is used.
    result = subprocess.run([
        "curl", "--disable", "--silent", "--show-error", "--fail", "--location",
        "--proto", "=https", "--proto-redir", "=https", "--max-redirs", "5",
        "--connect-timeout", "10", "--max-time", "30", "--max-filesize", "3000000",
        "--output", os.devnull, "--write-out", "%{http_code}", url,
    ], env={"PATH": os.environ["PATH"]}, text=True, capture_output=True)
    if result.returncode or not result.stdout.startswith("2"):
        raise NeedsReview(f"Product link did not return HTTP 2xx: {url} ({result.stdout or 'network error'}).")


def snapshot(pr):
    number, head = pr["number"], pr["head"]["sha"]
    run("git", "fetch", "--quiet", "--no-tags", "origin",
        "+refs/heads/master:refs/remotes/origin/master",
        f"+refs/pull/{number}/head:refs/remotes/origin/pr-{number}")
    if run("git", "rev-parse", f"refs/remotes/origin/pr-{number}") != head:
        raise NeedsReview("The PR changed during validation; it will be checked again.")
    base = run("git", "rev-parse", "refs/remotes/origin/master")
    try:
        tree = run("git", "merge-tree", "--write-tree", base, head).splitlines()[0]
    except subprocess.CalledProcessError as error:
        raise NeedsReview("The PR has merge conflicts.") from error
    if run("git", "diff", "--name-only", base, tree).splitlines() != ["README.md"]:
        raise NeedsReview("Only README.md changes can merge automatically.")
    if not run("git", "ls-tree", tree, "README.md").startswith("100644 blob "):
        raise NeedsReview("README.md must remain an ordinary text file.")
    entries = eligible_entries(run("git", "show", f"{base}:README.md"), run("git", "show", f"{tree}:README.md"))
    return base, entries


def other_checks(pr):
    # The old full-list audit is advisory: it includes broken, untouched links.
    # Identify it by its actual workflow path, not an attacker-selected check name.
    refs = {pr["head"]["sha"], pr.get("merge_commit_sha")}
    for ref in refs - {None}:
        checks = json.loads(run("gh", "api", f"repos/{REPO}/commits/{ref}/check-runs?per_page=100", "--paginate", "--slurp"))
        for check in [item for page in checks for item in page["check_runs"]]:
            if check["name"] == CHECK and check["app"]["slug"] == "github-actions":
                continue
            match = re.fullmatch(rf"https://github.com/{REPO}/actions/runs/(\d+)/job/\d+", check.get("details_url") or "")
            if check["app"]["slug"] == "github-actions" and match:
                workflow = api(f"actions/runs/{match[1]}")
                if workflow["path"] == ".github/workflows/validate.yml":
                    continue
            if check["status"] != "completed" or check["conclusion"] != "success":
                raise NeedsReview(f"Another check is not successful: {check['name']}.")
        status = api(f"commits/{ref}/status")
        if status["total_count"] and status["state"] != "success":
            raise NeedsReview("A commit status is pending or failing.")


def report(pr, reason, accepted):
    checks = api(f"commits/{pr['head']['sha']}/check-runs?check_name=Auto-merge%20eligibility&per_page=100")["check_runs"]
    existing = next((item for item in checks if item["app"]["slug"] == "github-actions" and item.get("external_id") == f"list-policy:{pr['number']}"), None)
    data = {"name": CHECK, "status": "completed", "conclusion": "success" if accepted else "action_required",
            "output": {"title": "Eligible for automatic merge" if accepted else "Needs human review", "summary": reason}}
    if existing:
        api(f"check-runs/{existing['id']}", "PATCH", data)
    else:
        api("check-runs", "POST", dict(data, head_sha=pr["head"]["sha"], external_id=f"list-policy:{pr['number']}"))
    labels = [item["name"] for item in api(f"issues/{pr['number']}/labels", paginate=True)]
    if not accepted and LABEL not in labels:
        api(f"issues/{pr['number']}/labels", "POST", {"labels": [LABEL]})
    elif accepted and LABEL in labels:
        run("gh", "api", f"repos/{REPO}/issues/{pr['number']}/labels/{LABEL}", "--method", "DELETE")


def process(number, merge):
    pr = api(f"pulls/{number}")
    if pr["state"] != "open":
        return "Already closed."
    try:
        if pr["draft"] or pr["base"]["ref"] != "master":
            raise NeedsReview("Drafts and PRs targeting other branches need human review.")
        if "do-not-merge" in [item["name"] for item in pr["labels"]]:
            raise NeedsReview("The do-not-merge label prevents automatic merging.")
        files = api(f"pulls/{number}/files", paginate=True)
        if len(files) != 1 or files[0]["filename"] != "README.md" or files[0]["status"] != "modified":
            raise NeedsReview("Only a modification to README.md can merge automatically.")
        if review_blocked(api(f"pulls/{number}/reviews", paginate=True)):
            raise NeedsReview("A reviewer has requested changes.")
        base, entries = snapshot(pr)
        for entry in entries:
            check_link(entry["url"])
        other_checks(pr)
        if not merge:
            return "PASS: " + ", ".join(entry["name"] for entry in entries)
        fresh = api(f"pulls/{number}")
        if (fresh["head"]["sha"] != pr["head"]["sha"] or fresh["state"] != "open"
                or fresh["draft"] or fresh["base"]["ref"] != "master"
                or "do-not-merge" in [item["name"] for item in fresh["labels"]]
                or api("git/ref/heads/master")["object"]["sha"] != base):
            raise NeedsReview("The PR or base branch changed; it will be checked again.")
        if review_blocked(api(f"pulls/{number}/reviews", paginate=True)):
            raise NeedsReview("A reviewer has requested changes.")
        other_checks(fresh)
        report(fresh, "README scope, entry structure, duplicate detection, placement, affected links and other checks passed. Product quality and description claims are not evaluated.", True)
        result = api(f"pulls/{number}/merge", "PUT", {"sha": fresh["head"]["sha"], "merge_method": "squash"})
        if not result["merged"]:
            raise NeedsReview(result["message"])
        return "MERGED: " + result["sha"]
    except NeedsReview as error:
        if merge:
            report(pr, str(error), False)
        return "NEEDS REVIEW: " + str(error)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge", action="store_true", help="Write checks/labels and merge passing PRs; default is read-only.")
    parser.add_argument("--pr", type=int, action="append")
    args = parser.parse_args()
    if args.merge and os.environ.get("GITHUB_ACTIONS") != "true":
        parser.error("Merging is only enabled inside the trusted GitHub Actions workflow.")
    numbers = args.pr or [item["number"] for item in api("pulls?state=open&sort=created&direction=asc&per_page=100", paginate=True)]
    failed = False
    for number in numbers:
        try:
            result = process(number, args.merge)
        except (subprocess.CalledProcessError, ValueError, KeyError) as error:
            failed = True
            result = f"ERROR: {error}"
        line = f"PR #{number}: {result}"
        print(line, flush=True)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(line + "\n\n")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
