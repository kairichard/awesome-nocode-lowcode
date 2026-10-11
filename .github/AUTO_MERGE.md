# Automatic merging

Small list contributions are checked and squash-merged automatically. No AI
service, API key, paid integration, or approval label is required. These checks
do not assess product quality, category relevance, or factual marketing claims.

## Rules

- Target `master`, be ready for review, and modify only the regular `README.md` file.
- Add or update one to three single-line entries, with at most 40 changed lines.
- Keep headings and other text unchanged. Description updates must retain the
  product's name, category, and exact URL. Removals and URL replacements need review.
- Use `- [Name](https://example.com) - Description` with a name of up to 80
  characters and a plain-text description of up to 400 characters. Extra links,
  HTML, tracking parameters, credentials, and IP-address URLs need review.
- Avoid duplicate names and URLs (including `www` and trailing-slash variants).
- Place new entries alphabetically between their neighbours. Existing ordering
  problems elsewhere do not block a contribution; description edits keep their place.
- All affected product URLs must return HTTP 2xx with valid HTTPS. HTTPS redirects
  are allowed; timeouts, bot blocks, and other failures leave the PR for review.
- Have no merge conflicts, outstanding change requests, or `do-not-merge` label.
- Other checks and commit statuses must succeed. The existing `Validate Urls`
  workflow is an explicit exception: it audits the *entire* list and is currently
  failing on untouched links. This automation independently checks every affected
  entry's URL. The exception is matched to that workflow's actual path.

## Operation

`Auto-merge list entries` runs when `Validate Urls` completes, and hourly at
minute 17 as a fallback for first-time contributors waiting for workflow approval,
missed events, and the existing backlog. GitHub schedules can be delayed. It scans
all open PRs oldest first. Only one automation run executes at a time.

The workflow runs trusted code checked out from `master`. PR commits are fetched
and merged into an in-memory Git tree for inspection; PR code is never checked
out or executed. Public URL checks receive no GitHub credentials and discard
response bodies. The merge uses the exact checked head SHA; a changed head is
rejected by GitHub. The base branch, PR state, labels, checks and reviews are
checked again before merging. GitHub branch rules still apply.

Every evaluated PR receives an **Auto-merge eligibility** check with the outcome.
Failures receive `needs-human-review`; a later passing run clears it. Failures
never close a PR or modify its submitted content. Workflow/API failures stop the
affected merge and fail the workflow run. The Actions run summary records outcomes.

Add `do-not-merge` to hold a PR. To stop all automatic merging, disable
**Auto-merge list entries** in Actions. To retry now, run that workflow manually.
Select its `dry_run` input to inspect the backlog without writing or merging.

For a local read-only check (Git, Python 3.9+, curl and authenticated `gh` required):

```sh
python3 -m unittest discover -s .github/scripts -v
python3 .github/scripts/auto_merge.py
python3 .github/scripts/auto_merge.py --pr 170
```

The write mode is restricted to GitHub Actions. Disabling this workflow does not
disable the independent existing whole-list link audit.
