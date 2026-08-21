# Agent Instructions

These instructions apply to all automated agents working in this repository, including Codex.

## 1. Protected Branch Policy

The repository's default production branch is treated as **protected**, even where GitHub does not technically enforce branch protection.

This will normally be:

- `main`, or
- `master` in repositories that still use `master`.

For the remainder of this document, **protected branch** means the repository's production/default branch.

### Agents must NEVER

- commit directly to the protected branch;
- push directly to the protected branch;
- force-push the protected branch;
- reset the protected branch;
- rebase the protected branch;
- rewrite the protected branch's history;
- delete or rename the protected branch;
- merge a pull request into the protected branch;
- bypass a pull-request workflow;
- change repository settings, permissions, rules, branch protections, Actions permissions, secrets, deploy keys, or similar controls in order to weaken these restrictions.

Only the human repository owner may approve and merge changes into the protected branch.

If a user request appears to require any prohibited operation, **stop and ask for explicit human direction rather than performing it**.

---

## 2. Mandatory Branch Check Before Writing

Before modifying repository files, verify the current Git branch.

Equivalent command:

```bash
git branch --show-current
```

If the current branch is the protected branch:

1. Do not edit files yet.
2. Ensure the branch is up to date where appropriate.
3. Create a new working branch from it.
4. Switch to that branch.
5. Only then begin modifying files.

Never make changes first and create the branch afterwards.

---

## 3. Working Branches

Agent work must take place on a dedicated non-protected branch.

Prefer these naming conventions:

```text
feature/<short-description>
fix/<short-description>
refactor/<short-description>
docs/<short-description>
experiment/<short-description>
agent/<short-description>
```

Use `agent/<short-description>` when none of the more specific categories is appropriate.

Examples:

```text
feature/collections-sharing
fix/incorrect-tune-number
refactor/event-metadata
docs/api-notes
agent/cleanup-build-files
```

Branch names should be concise, descriptive, and lowercase, with words separated by hyphens.

Do not reuse an unrelated existing branch merely because it already exists.

---

## 4. Normal Git Workflow

The expected workflow is:

```text
protected branch
      │
      ├── create working branch
      ▼
feature/fix/agent branch
      │
      ├── make changes
      ├── test changes
      ├── commit
      ├── push branch
      ▼
pull request
      │
      ▼
human review
      │
      ▼
human merge
```

Agents may:

- create working branches;
- modify files on working branches;
- commit to working branches;
- push working branches;
- update their own working branches;
- open pull requests;
- update existing pull requests;
- respond to review feedback;
- run tests and CI;
- inspect branches, commits, diffs, PRs, and repository history.

Agents must leave the final merge into the protected branch to the human owner.

---

## 5. Pull Requests

All changes intended for the protected branch must be delivered through a pull request.

A pull request should clearly state:

- what changed;
- why it changed;
- any important implementation decisions;
- testing or validation performed;
- known limitations or follow-up work;
- any migration, deployment, configuration, or compatibility implications.

Do not merge the PR.

If further work is requested after the PR is opened, update the existing working branch and PR rather than creating unnecessary replacement PRs.

---

## 6. Human Approval Boundary

A pull request created or updated by an agent is a **proposal**, not approval to deploy or merge.

The following always require human action or explicit human authorization:

- merging into the protected branch;
- production deployment;
- destructive repository operations;
- rewriting published Git history;
- deleting significant branches or tags;
- changing repository access or security controls;
- modifying production credentials or secrets;
- deleting production data;
- destructive database migrations.

Do not interpret a request to "finish", "complete", "publish", "ship", or similar language as authorization to bypass this boundary.

---

## 7. Destructive Git Commands

Avoid destructive Git commands unless they are clearly necessary and explicitly authorized.

In particular, do not casually use:

```bash
git reset --hard
git clean -fd
git clean -fdx
git push --force
git push --force-with-lease
git branch -D
git tag -d
```

Never use destructive commands on the protected branch.

Before any destructive operation on a working branch, inspect the affected state and ensure no unrelated or uncommitted work will be lost.

Prefer reversible operations wherever possible.

---

## 8. Existing User Work

Never overwrite, discard, stage, commit, or otherwise absorb unrelated user changes.

Before committing:

```bash
git status
git diff
git diff --staged
```

Review what is actually being committed.

Stage only files belonging to the current task.

Prefer explicit paths:

```bash
git add -- path/to/file1 path/to/file2
```

Do not indiscriminately stage the entire working tree when unrelated changes may be present.

Avoid:

```bash
git add .
git add -A
git add --all
```

unless the repository state has been explicitly verified and all changes belong to the same task.

---

## 9. Commits

Commits should:

- contain logically related changes;
- have concise, descriptive messages;
- avoid unrelated formatting or cleanup;
- not include generated junk, temporary diagnostics, local configuration, credentials, or build artifacts unless intentionally required.

Prefer meaningful commit messages such as:

```text
Add collection sharing permission checks
Fix positional zoom target overshoot
Refactor event metadata normalization
Update changelog for 1.7.0
```

Avoid vague messages such as:

```text
changes
update
fix stuff
wip
```

Multiple sensible commits are preferable to one enormous undifferentiated commit.

---

## 10. Scope Discipline

Make only the changes necessary for the requested task.

Do not opportunistically:

- reformat unrelated files;
- rename unrelated symbols;
- reorganize directories;
- upgrade dependencies;
- rewrite working code;
- change APIs;
- alter UI behaviour;
- remove compatibility code;
- change deployment configuration;

unless those changes are necessary for the task or specifically requested.

If a broader improvement is discovered, mention it separately rather than silently expanding scope.

---

## 11. Testing and Validation

Before presenting work as complete:

1. Inspect the final diff.
2. Run relevant tests.
3. Run syntax/compile/build checks where applicable.
4. Check for unintended generated files.
5. Verify that the requested behaviour is actually represented by the code.
6. Report anything that could not be tested.

Do not claim a test passed unless it was actually run successfully.

Where hardware, credentials, live services, cameras, embedded devices, or other unavailable infrastructure are required, clearly distinguish:

- what was tested;
- what was statically validated;
- what still requires real-world testing.

---

## 12. CHANGELOG.md and README.md

Use `CHANGELOG.md` for version history and descriptions of releases or version-specific changes.

Do **not** use `README.md` as a running update log.

`README.md` is reserved for stable repository-level documentation such as:

- project purpose;
- installation;
- architecture;
- usage;
- configuration;
- development setup;
- repository structure.

When a change belongs in release history, update `CHANGELOG.md` rather than adding update notes to `README.md`.

Do not add a changelog entry for every trivial intermediate edit unless appropriate to the repository's existing convention.

---

## 13. Version Numbers and Releases

Do not independently create releases, tags, or production version bumps unless the task explicitly includes doing so or existing repository workflow clearly requires it.

When changing a version:

- update all canonical version declarations consistently;
- update `CHANGELOG.md` where appropriate;
- check for duplicated version strings;
- do not create a Git tag or GitHub Release unless explicitly requested.

Never reuse or move an existing published release tag without explicit human approval.

---

## 14. Generated and Temporary Files

Do not commit temporary helper files, debug dumps, downloaded artifacts, generated bytecode, local caches, editor files, test scratch data, or one-off scripts unless they are intentionally part of the repository.

Before committing, check for artifacts such as:

```text
__pycache__/
*.pyc
.tmp/
.cache/
.vscode/
.env
*.log
build/
dist/
```

Respect the repository's `.gitignore`.

Never commit credentials, access tokens, API keys, passwords, private certificates, or production secrets.

---

## 15. Configuration and Secrets

Treat configuration affecting production systems with particular care.

Do not:

- expose secrets in code;
- commit `.env` files containing credentials;
- replace secret references with literal values;
- weaken authentication or authorization;
- disable security checks merely to make tests pass;
- alter production credentials without explicit authorization.

If a requested solution appears to require committing a secret, stop and propose a secure alternative.

---

## 16. Database and Data Safety

Database changes must preserve existing data unless destructive behaviour is explicitly required and approved.

Prefer:

- additive schema changes;
- migrations;
- backups;
- compatibility transitions;
- reversible operations.

Do not delete, truncate, overwrite, or recreate production data as an implementation shortcut.

Where a migration is required, document the migration and rollback implications in the PR.

---

## 17. Dependencies

Do not upgrade dependencies simply because newer versions exist.

When adding or upgrading a dependency:

- confirm it is necessary;
- minimize version churn;
- update lockfiles consistently;
- check compatibility;
- run relevant tests;
- describe meaningful dependency changes in the PR.

Avoid broad dependency refreshes during unrelated feature work.

---

## 18. Repository Administration

Agents should not modify repository administration unless the human owner explicitly requests a specific administrative change.

This includes:

- collaborators;
- organization membership;
- repository roles;
- branch protection;
- rulesets;
- Actions permissions;
- secrets;
- environments;
- deploy keys;
- webhooks;
- repository visibility;
- repository transfer;
- repository deletion;
- GitHub App permissions.

Administrative access must never be used to circumvent the protected-branch policy in this document.

---

## 19. Production Deployment

Code being merged and code being deployed are separate decisions.

Agents must not assume that approval of code changes also authorizes production deployment.

Unless explicitly instructed otherwise:

```text
working branch → PR → human review → human merge → deployment
```

Production deployment remains under human control.

Development or test deployments may be automated where the repository's documented workflow permits it.

---

## 20. When Unsure

If there is ambiguity between a reversible, branch-local action and an irreversible or production-affecting action, choose the reversible option.

If proceeding safely requires assumptions about:

- branch ownership;
- production state;
- credentials;
- deployment;
- destructive operations;
- data loss;
- repository administration;

stop and ask rather than guessing.

The overriding principle is:

> **Agents may create and propose changes freely on working branches, but the protected branch and production state remain under human control.**