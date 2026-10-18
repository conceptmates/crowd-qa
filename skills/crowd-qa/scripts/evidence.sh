#!/usr/bin/env bash
# Pushes a fix job's evidence to the evidence branch and prints the base URL for its images.
#   evidence.sh <run> push <tracker-key> <area> <dir>
# Files land in <RUN_LABEL>/fix-<area>/ on EVIDENCE_BRANCH of the tracker's repo. Pushes from several agents are
# serialised with a lock, rebased on what is already there, and never forced: on 2026-10-13 a fixer's push
# replaced the evidence branch and wiped the re-check screenshots another agent had pushed.
set -u
RUN="$1"; CMD="$2"; KEY="$3"; AREA="$4"; SRC="$5"
. "$RUN/config.env"
[ "$CMD" = push ] || { sed -n 2,3p "$0"; exit 2; }
REPO=$(for t in ${TRACKERS:-}; do [ "${t%%=*}" = "$KEY" ] && echo "${t#*=}"; done)
[ -n "$REPO" ] || { echo "no tracker $KEY in TRACKERS" >&2; exit 1; }
BR="${EVIDENCE_BRANCH:-qa-evidence}"; LABEL="${RUN_LABEL:-crowd-qa}"; D="$RUN/evidence-$KEY"; REL="$LABEL/fix-$AREA"
LOCK="$RUN/fix/evidence-$KEY.lock"; mkdir -p "$RUN/fix"
until mkdir "$LOCK" 2>/dev/null; do sleep 2; done
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
if [ ! -d "$D/.git" ]; then
  if ! git clone -q --single-branch -b "$BR" "https://github.com/$REPO.git" "$D" 2>/dev/null; then
    rm -rf "$D"; mkdir -p "$D"; git -C "$D" init -q; git -C "$D" checkout -q --orphan "$BR"
    echo "Evidence for crowd-QA issues and fixes." > "$D/README.md"
    git -C "$D" add README.md; git -C "$D" commit -q -m qa-evidence
    git -C "$D" remote add origin "https://github.com/$REPO.git"
  fi
fi
mkdir -p "$D/$REL"
find "$SRC" -maxdepth 1 -type f \( -name '*.png' -o -name '*.jpg' -o -name '*.txt' -o -name '*.gif' \) -exec cp {} "$D/$REL/" \;
git -C "$D" add "$REL"
git -C "$D" diff --cached --quiet || git -C "$D" commit -q -m "qa-evidence: fix-$AREA"
for try in 1 2 3; do
  git -C "$D" pull -q --rebase origin "$BR" 2>/dev/null
  git -C "$D" push -q -u origin "$BR" && { echo "https://github.com/$REPO/blob/$BR/$REL"; exit 0; }
  sleep $((try * 3))
done
echo "push of the evidence branch failed three times" >&2; exit 1
