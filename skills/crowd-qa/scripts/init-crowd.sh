#!/usr/bin/env bash
# usage: init-crowd.sh <run-dir>
# Creates a durable run dir: config.env, copies of the scripts and templates, and empty state.
# Characters live in lanes/<id>/ (card.md, scenarios.md, memory.md, round<day>/), the town square in square/,
# what characters make in world/, state for resuming in state/, device locks in .devices/.
set -eu
RUN="$1"
case "$RUN" in /tmp/*|/private/tmp/*) echo "refusing /tmp: a reboot wipes it; use a path on real disk" >&2; exit 1;; esac
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$RUN"/{lanes,scripts,templates,references,square,world,state,logs,archive,.runner,.devices,.active}
cp "$SKILL"/scripts/*.sh "$SKILL"/scripts/*.py "$RUN/scripts/"
mkdir -p "$RUN/scripts/world"; cp "$SKILL"/scripts/world/* "$RUN/scripts/world/"   # the 3D page square-view.py writes
cp "$SKILL"/templates/* "$RUN/templates/"
mkdir -p "$RUN/references"; cp "$SKILL"/references/*.md "$RUN/references/"
chmod +x "$RUN"/scripts/*.sh
touch "$RUN/square/feed.jsonl"
[ -f "$RUN/world/stores.json" ] || echo '{}' > "$RUN/world/stores.json"
[ -f "$RUN/HANDOFF.md" ] || printf '# Crowd QA run handoff\n\nResume: bash %s/scripts/resume.sh %s\n' "$RUN" "$RUN" > "$RUN/HANDOFF.md"
[ -f "$RUN/context.md" ] || cp "$SKILL/templates/context.example.md" "$RUN/context.md"
[ -f "$RUN/filed.txt" ] || : > "$RUN/filed.txt"
if [ ! -f "$RUN/config.env" ]; then
cat > "$RUN/config.env" <<EOF
# crowd-qa run config. Fill it in from the preflight answers; every script sources this file.
RUN_DIR="$RUN"

# --- what is tested -----------------------------------------------------------------------------------
TARGET="web"                    # default surface: web | ios | android | cli | api (a card's Surface wins)
APP_URL="http://localhost:3000" # web: where the app is served
API_URL=""                      # api: base URL characters call
CLI_CMD=""                      # cli: how characters run the tool, e.g. "node /path/cli.js"
APP_ID=""                       # ios/android: bundle id or package
APP_PATH=""                     # ios/android: built app installed on each device (build once)
DEVICE_POOL=""                  # ios/android: simulator UDIDs / emulator serials characters may use
DEV_CMD=""                      # optional: command that starts the app (restart-watchers.sh keeps it up)
DEV_DIR=""                      # directory to run DEV_CMD in

# --- testers (asked in preflight: references/engines.md) ---------------------------------------------
ENGINE_CONFIRMED=""             # yes once the user chose the engines below; preflight fails until then
TESTER="claude"                 # main tester: claude | codex | opencode
TESTER_MODEL="sonnet"           # empty = the CLI's default
TESTER_EFFORT="medium"          # codex: low|medium|high|max; claude: low|medium|high
CODEX_MODEL=""                  # for characters whose card says "- **Engine**: codex"
CODEX_EFFORT="high"
CLAUDE_MODEL="sonnet"           # for characters whose card says "- **Engine**: claude" when TESTER is not claude
CLAUDE_EFFORT="medium"
FALLBACK_TESTER=""              # takes over an engine that hit its usage limit; empty = pause instead
FALLBACK_MODEL=""
FALLBACK_EFFORT=""
# quota-guard.sh (caam) rotates subscription accounts before a limit; only the guard switches accounts during a run
CODEX_ACCOUNTS=""               # caam profile names in order, e.g. "work spare1 spare2"; empty = no rotation
CLAUDE_ACCOUNTS=""
PRIMARY_MAX=94; WEEKLY_MAX=95   # switch when the active account passes either % (5-hour window, week)
SWITCH_HEADROOM=10              # switch only to an account at least this many points under both bars
CODEX_NEAR_MAX=85               # with FALLBACK_TESTER: at this 5-hour % and no Codex account free, new days start on the fallback
PREFLIGHT_MODEL_PROBE=""        # switch = preflight activates each Codex account in turn to prove it accepts the model
RETRY_PRIMARY_MIN=60            # minutes before trying an engine again after a limit
ROUND_TIMEOUT_S=7200            # one character-day at most this long
CLAUDE_LEAN=1                   # claude testers and model steps start without user settings, hooks, MCP, skills
CHUNK_USD=5                     # claude testers: a fresh session every this many dollars (0 = one session a day)
MAX_CHUNKS=8
SQUARE_DIGEST_LIMIT=20          # square posts in each tester prompt; testers read more on demand
DAYS=2                          # simulated days per character

# --- model steps run by crowd.py (everything else is code) -------------------------------------------
LLM_CLI="claude"
# Judgement steps run on Opus: a Sonnet bug check refused a real seeded bug in a dry run
PLANNER_MODEL="opus";   PLANNER_EFFORT="medium"; MAP_EFFORT="medium"   # a high-effort map cost \$41 of \$62 once
PRODUCT_MAP_FROM=""             # a saved product-map.md to reuse; copied only when its source commits match SOURCE_PATHS
JUDGE_MODEL="opus";     JUDGE_EFFORT="medium"
VERIFY_MODEL="opus";    VERIFY_EFFORT="medium"
ESCALATE_MODEL="opus";  ESCALATE_EFFORT="medium" # bugs of high severity or with an unconfirmed cause; "high" = deeper check
REPORT_MODEL="opus";    REPORT_EFFORT="medium"
SOURCE_PATHS=""                 # source the planners and judges may read, space-separated absolute paths
RESEARCH_NOTES=""
SCEN_MAX=12                     # scenarios per character-day; a longer day is split into more days (31-scenario days ended early)
SATISFIED_PCT=90                # coverage (ran / planned, computed in code) that counts as a full day
DESIGN_PARALLEL=5; JUDGE_PARALLEL=4; FILE_PARALLEL=6
POLL_S=20                       # crowd.py checks runners this often
DEDUPE_LLM=1                    # ask a model only about look-alike pairs on the same screen

# --- capacity -----------------------------------------------------------------------------------------
HARD_CAP=4                      # characters at once
LANE_RAM_MB=1500                # web ~1500, mobile ~4000, cli/api ~400
MIN_FREE_PCT=15                 # never admit a character below this free-memory %
MAX_LOAD_PER_CORE=3
SWAP_MAX_MB=4096                # never admit a character while more swap than this is in use
DISK_MIN_GB=10                  # no character starts below this much free disk
BROWSER_SLOTS=""                # web browsers: "remote:1 remote:2 ..." (Chromium on REMOTE_HOST) or "local:1 ..."; empty = no cap.
                                #   Keep one slot more than HARD_CAP web characters, for second-user browsers
REMOTE_MIN_FREE_MB=3000         # remote slots: no new Chromium below this MemAvailable on REMOTE_HOST (empty = no gate)
REMOTE_MAX_LOAD_PER_CORE=3      # ... or at/above cores x this 1-min load (empty = no gate)
REMOTE_CDP_BASE=9320            # remote:N uses CDP port REMOTE_CDP_BASE+N on both machines
REMOTE_CHROME="~/crowd-qa/remote-chrome.sh"   # preflight installs scripts/remote-chrome.sh here on REMOTE_HOST
REMOTE_UPLOADS="/tmp/crowd-uploads"           # upload files for remote browsers, on REMOTE_HOST

# --- the stack ----------------------------------------------------------------------------------------
STACK_MODE="local"              # local | remote (backend on REMOTE_HOST behind tunnel.sh)
REMOTE_HOST=""                  # remote: an ~/.ssh/config alias with key login
TUNNEL_PORTS=""                 # remote: ports forwarded localhost -> REMOTE_HOST, same numbers
API_PORT=""                     # local: net-stall.sh freezes whatever listens here
STACK_VERIFY=""                 # end-to-end stack check (templates/stack-verify.example.py)
HEALTH_CHECKS=""                # api-watch.sh: "name|command" pairs separated by ';' (see references/hooks.md)
STACK_UP_CMD=""                 # watchdog: brings the stack back up
DB_BACKUP_CMD=""                # watchdog: prints a database dump to stdout
DB_RESTORE_CMD=""               # watchdog: reads a dump on stdin
DB_COUNT_CMD=""                 # watchdog: prints one number that only grows (e.g. user count)

# --- hooks for things a real user would get from a third party ---------------------------------------
CONNECT_CMD=""                  # connect a fake third-party account for a character: <email> <name>
CUSTOMERS_CONFIG=""             # channels for scripts/customer.py (templates/customers.example.json)
OUTBOX_CMD=""                   # what the product "sent" to customers (their phone screen)
MAIL_CMD=""                     # a character's inbox: <email>

# --- filing -------------------------------------------------------------------------------------------
FILING="on"                     # off = stop after the merged issue list (state/issues.json)
TRACKERS=""                     # "app=owner/app backend=owner/api": where each finding's repo field files
EVIDENCE_BRANCH="qa-evidence"   # orphan branch in each repo that holds screenshots
RUN_LABEL=""                    # label on every issue, e.g. crowd-qa-2026-10-08
RUN_DATE=""
TESTER_LABEL=""                 # shown in each issue footer, e.g. "Sonnet 5.5 and Codex"
FILE_CMD=""                     # empty = GitHub via gh; else a command that files one issue from a JSON file
FILE_AUTO="no"                  # yes = file without waiting for approval of file-plan.md (crowd.py <run> approve)
PLAN_MODEL=""                   # the batch duplicate check against every issue and comment (default VERIFY_MODEL)

# ---- fixing (after filing): an area per group of bugs, fixer -> reviewer -> re-check by the reporters -> draft PR
FIXING="off"                    # on = run the fix stage after filing
RUNNER="auto"                   # t3 | subagent | cli. auto = the orchestrating session decides (cli when crowd.py
                                #   runs alone). t3 and subagent: the session launches jobs (crowd.py <run> jobs next)
REPOS=""                        # local clones per tracker, for cli/subagent worktrees: "app=/path/app backend=/path/api"
WATCH_STALL_MIN="40"            # fix-watch.py: a running job silent this long is STALLED
WATCH_URL=""                    # fix-watch.py checks this answers when STACK_VERIFY is empty
WATCH_AGENT="off"               # on = one low-effort model call per new problem, diagnosis in logs/watch.md
WATCH_MODEL="opus"
FIX_TRACKERS=""                 # trackers the fix stage may change, e.g. "app"; the others get issues only, no PR (empty = all)
TRACKER_KIND=""                 # "app=fe backend=be": fe areas run dev servers side by side, be areas queue
FIX_BASE="main"                 # branch fixes start from and PRs target
FIX_PARALLEL="4"                # jobs at once
FIX_RETRIES="2"                 # fix rounds after a failed re-check before the PR is labelled needs-human
REVIEW_ROUNDS="2"               # fix rounds a review may ask for
FIX_PORT_BASE="3101"            # area dev servers get FIX_PORT_BASE + n
FIX_AREA_MAX="20"               # bugs per area
FIX_MODEL="auto"                # per role, "auto" or engine:model (claude:opus, codex:gpt-6-luna); t3 picks from the
REVIEW_MODEL="auto"             #   live catalog: fixer Opus, reviewer Claude Opus (a fresh session that never saw the fix),
RECHECK_MODEL="auto"            #   re-check Sonnet
REVIEW_ENGINE=""                # cli runner: claude | codex for the reviewer (default claude)
FE_PREP_CMD=""                  # run in a worktree before every install (tool config the install needs)
FE_INSTALL_CMD=""               # e.g. pnpm install --frozen-lockfile --prefer-offline
FE_DEV_CMD=''                   # single quotes keep \$PORT for the area's port: 'pnpm exec next dev -p \$PORT'
BE_DEPLOY_CMD=''                # single quotes keep \$BRANCH: 'ssh stack "cd api && git fetch && git checkout \$BRANCH && make up"'
BE_RESET_CMD=""                 # put the shared backend back on FIX_BASE
CHECK_CMD=""                    # checks every fixer runs; per tracker: CHECK_CMD_app="pnpm exec tsc --noEmit -p ."
RECHECK_COMMENT_HOST=""         # ssh host whose gh is the account that filed the issues (re-check comments come from it)
MERGE_CHECK="on"                # when every area is finished: merge all fix branches in a scratch worktree, report order and conflicts
BOUT_MAX="10"                   # disputes settled by a judge per day; fighters BOUT_FIGHTER_MODEL, judge BOUT_JUDGE_MODEL
PRODUCT_NAME=""
NOTIFY_CMD=""                   # optional: run with one message when the stack goes down or comes back
SQUARE_LANG=""                  # en = the square refuses non-Latin scripts; empty = any language
CROWD_TITLE=""                  # name shown at the top of the square page (default: the run dir name)
SQUARE_SERVE="on"               # serve the 3D world and the square over HTTP (square-serve.py; never config or state)
SQUARE_PORT="8790"              # first port tried
SQUARE_BIND=""                  # empty = this machine's Tailscale address, else 127.0.0.1; 0.0.0.0 = every network
EOF
fi
echo "crowd run dir ready: $RUN"
