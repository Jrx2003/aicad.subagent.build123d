# Build123 Migration Repo Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the current Build123d migration work into `/Users/jerryx/code/aicad.subagent.build123`, restore `/Users/jerryx/code/aicad.subagent.iteration` back to the CadQuery baseline, publish the Build123d repo to GitHub, and then improve benchmark performance in the published Build123d repo.

**Architecture:** Treat `/Users/jerryx/code/aicad.subagent.iteration` as the current source of truth for the migrated Build123d worktree, clone its full filesystem state into `/Users/jerryx/code/aicad.subagent.build123`, then use git in `iteration` to reset tracked and untracked files back to the CadQuery repository baseline. Initialize an independent git repository in `build123`, create a remote repository, push `main`, then run fresh L1/L2 benchmark analysis and patch the Build123d repo in place.

**Tech Stack:** git, rsync, GitHub connector, pytest, benchmark harness, Build123d runtime

---

### Task 1: Snapshot The Migrated Build123d Tree

**Files:**
- Create: `/Users/jerryx/code/aicad.subagent.build123/**`
- Read: `/Users/jerryx/code/aicad.subagent.iteration/**`

- [ ] **Step 1: Mirror the current migrated tree into the target directory**

Run:

```bash
rm -rf /Users/jerryx/code/aicad.subagent.build123
mkdir -p /Users/jerryx/code/aicad.subagent.build123
rsync -a --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.pytest_cache' \
  --exclude '.uv-cache' \
  --exclude '.tmp' \
  /Users/jerryx/code/aicad.subagent.iteration/ \
  /Users/jerryx/code/aicad.subagent.build123/
```

- [ ] **Step 2: Verify the mirror contains the expected repository files**

Run:

```bash
test -f /Users/jerryx/code/aicad.subagent.build123/pyproject.toml
test -d /Users/jerryx/code/aicad.subagent.build123/src
test -d /Users/jerryx/code/aicad.subagent.build123/tests
```

Expected: all `test` commands exit `0`

### Task 2: Restore `iteration` To CadQuery Baseline

**Files:**
- Modify: `/Users/jerryx/code/aicad.subagent.iteration/**`

- [ ] **Step 1: Reset tracked files to repository `HEAD`**

Run:

```bash
git -C /Users/jerryx/code/aicad.subagent.iteration reset --hard HEAD
```

- [ ] **Step 2: Remove untracked migration artifacts from `iteration`**

Run:

```bash
git -C /Users/jerryx/code/aicad.subagent.iteration clean -fd
```

- [ ] **Step 3: Verify `iteration` is clean and back on the original remote**

Run:

```bash
git -C /Users/jerryx/code/aicad.subagent.iteration status --short
git -C /Users/jerryx/code/aicad.subagent.iteration remote -v
```

Expected: empty status output; existing `aicad.subagent.iteration` remotes remain intact

### Task 3: Initialize And Publish `build123`

**Files:**
- Create: `/Users/jerryx/code/aicad.subagent.build123/.git/**`
- Modify: `/Users/jerryx/code/aicad.subagent.build123/.gitignore` if needed

- [ ] **Step 1: Initialize a fresh git repository in `build123`**

Run:

```bash
git -C /Users/jerryx/code/aicad.subagent.build123 init -b main
git -C /Users/jerryx/code/aicad.subagent.build123 status --short
```

- [ ] **Step 2: Create the remote GitHub repository**

Run through the GitHub connector with repository name:

```text
aicad.subagent.build123
```

Default assumption: create it under the authenticated user account as a private repository unless an existing repository already matches.

- [ ] **Step 3: Commit and push the mirrored Build123d tree**

Run:

```bash
git -C /Users/jerryx/code/aicad.subagent.build123 add .
git -C /Users/jerryx/code/aicad.subagent.build123 commit -m "feat: initialize build123d migration repo"
git -C /Users/jerryx/code/aicad.subagent.build123 remote add origin <new-repo-url>
git -C /Users/jerryx/code/aicad.subagent.build123 push -u origin main
```

### Task 4: Re-Verify Build123d Repo Health

**Files:**
- Test: `/Users/jerryx/code/aicad.subagent.build123/tests/**`
- Test: `/Users/jerryx/code/aicad.subagent.build123/benchmark/**`

- [ ] **Step 1: Run the full unit test suite in `build123`**

Run:

```bash
uv run pytest -q
```

Expected: zero test failures

- [ ] **Step 2: Run fresh L1 and L2 benchmarks in `build123`**

Run:

```bash
./benchmark/run_prompt_benchmark.sh --levels L1,L2
```

Expected: a new run directory under `benchmark/runs/`

### Task 5: Optimize Benchmark Failures In `build123`

**Files:**
- Modify: `/Users/jerryx/code/aicad.subagent.build123/src/**`
- Modify: `/Users/jerryx/code/aicad.subagent.build123/tests/**`

- [ ] **Step 1: Inspect the latest benchmark report and isolate failing cases**

Run:

```bash
sed -n '1,120p' /Users/jerryx/code/aicad.subagent.build123/benchmark/runs/latest/brief_report.tsv
```

- [ ] **Step 2: Add failing or targeted regression tests before each fix**

Run targeted pytest commands for the touched modules after writing each regression test.

- [ ] **Step 3: Implement minimal fixes, rerun targeted tests, then rerun the relevant benchmark slice**

Preferred commands:

```bash
uv run pytest <targeted-tests> -q
./benchmark/run_prompt_benchmark.sh --levels L1
./benchmark/run_prompt_benchmark.sh --levels L2
```

- [ ] **Step 4: Re-run full verification in `build123`**

Run:

```bash
uv run pytest -q
./benchmark/run_prompt_benchmark.sh --levels L1,L2
```
