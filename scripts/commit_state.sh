#!/usr/bin/env bash
# Commits state/ (and sources.yaml) back to the repo so the next run knows what happened.
# Clip and Post runs write different files, so a rebase almost never conflicts. If the
# encrypted vault conflicts (you re-connected an account while a run was refreshing a
# token), your version from the repo wins.
set -uo pipefail
msg="${1:-clipbot: update state}"
git config user.name "clipbot[bot]"
git config user.email "clipbot@users.noreply.github.com"
git add -A state config/sources.yaml reports 2>/dev/null
if git diff --cached --quiet; then
  echo "No state changes to commit."
  exit 0
fi
git commit -q -m "$msg [skip ci]"
for i in 1 2 3 4 5 6; do
  if git push -q; then
    echo "State saved."
    exit 0
  fi
  echo "Push rejected, rebasing (attempt $i)..."
  if ! git pull -q --rebase; then
    if git diff --name-only --diff-filter=U | grep -q "state/vault.enc"; then
      git checkout --ours state/vault.enc && git add state/vault.enc
    fi
    if git diff --name-only --diff-filter=U | grep -q .; then
      echo "Unexpected conflict:"; git diff --name-only --diff-filter=U
      git rebase --abort; exit 1
    fi
    GIT_EDITOR=true git rebase --continue || { git rebase --abort; exit 1; }
  fi
  sleep $((i * 3))
done
echo "Could not push state after several tries."
exit 1
