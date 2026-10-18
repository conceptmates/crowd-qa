#!/usr/bin/env python3
"""End-to-end tests of the crowd-qa scripts with fake engines: no model is called and nothing leaves the machine.
Each test builds a small run dir, runs the real scripts, and checks one behaviour from the run audits.

  python3 tests/test_crowd.py            # all
  python3 tests/test_crowd.py -k quota   # by name
"""
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
import warnings

warnings.simplefilter("ignore", ResourceWarning)

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(os.path.dirname(HERE), "skills", "crowd-qa")
WORK = os.path.join(HERE, ".work")
BIN = os.path.join(WORK, "bin")


def setUpModule():
    os.makedirs(BIN, exist_ok=True)
    for n in ("claude", "codex", "gh", "git", "agent-browser", "ssh", "caam"):
        p = os.path.join(BIN, n)
        if not os.path.lexists(p):
            os.symlink(os.path.join(HERE, "fakes", "fake_caam.py" if n == "caam" else "fake_engine.py"), p)


class Run:
    def __init__(self, name, lanes, config="", tester=None, plans=True, cards=None):
        self.dir = os.path.join(WORK, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.env = {**os.environ, "PATH": BIN + ":" + os.environ["PATH"], "FAKE_LOG": os.path.join(self.dir, "fake.jsonl"),
                    "FAKE_CAAM": os.path.join(self.dir, "caam.json")}
        subprocess.run(["bash", os.path.join(SKILL, "scripts", "init-crowd.sh"), self.dir], check=True,
                       capture_output=True, env=self.env)
        R = self.dir
        with open(os.path.join(R, "config.env"), "a") as f:
            f.write(f"""
ENGINE_CONFIRMED=yes TESTER=claude TESTER_MODEL=sonnet TESTER_EFFORT=medium HARD_CAP=6
LANE_RAM_MB=1 MIN_FREE_PCT=0 MAX_LOAD_PER_CORE=100000 SWAP_MAX_MB=100000000 DISK_MIN_GB=0
CAPACITY_POLL_S=1 STACK_POLL_S=1 POLL_S=0.3 HEALTH_CHECKS="" RUN_LABEL=crowd-test SOURCE_PATHS="{R}" DAYS=2
FILE_CMD="bash {R}/file-hook.sh" TRACKERS="" CHUNK_USD=5 ROUND_TIMEOUT_S=120
{config}
""")
        with open(os.path.join(R, "file-hook.sh"), "w") as f:
            f.write('mkdir -p "$(dirname "$1")/../hooked"; n=$(ls "$(dirname "$1")/../hooked" | wc -l | tr -d " ");'
                    ' cp "$1" "$(dirname "$1")/../hooked/$n.json"; echo "HOOK-$n"\n')
        json.dump(lanes, open(os.path.join(R, "lanes.json"), "w"))
        json.dump(tester or {}, open(os.path.join(R, "fake-tester.json"), "w"))
        open(os.path.join(R, "context.md"), "w").write("# Run context\nShared context for every character.\n")
        for l in lanes:
            ld = os.path.join(R, "lanes", l["id"])
            os.makedirs(ld, exist_ok=True)
            extra = (cards or {}).get(l["id"], "")
            open(os.path.join(ld, "card.md"), "w").write(
                f"# {l['id']}\n- **Name**: {l['id'].title()}\n- **Email**: {l['id']}@crowd.test\n- **Surface**: web\n{extra}")
            if plans:
                body = "".join(f"## Day {d}\n" + "".join(f"### S{(d - 1) * 3 + i} — step {i}  [failure]\nSteps: x\n"
                                                        for i in (1, 2, 3)) for d in (1, 2))
                open(os.path.join(ld, "scenarios.md"), "w").write(body + "## Exhaustive sweep\n- every screen\n")
        open(os.path.join(R, "product-map.md"), "w").write("# map\n" + "x" * 600)

    def crowd(self, *cmd, timeout=180, background=False):
        args = ["python3", os.path.join(self.dir, "scripts", "crowd.py"), self.dir, *cmd]
        if background:
            return subprocess.Popen(args, env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
        return subprocess.run(args, env=self.env, capture_output=True, text=True, timeout=timeout)

    def sh(self, *cmd, timeout=120):
        return subprocess.run(cmd, env=self.env, capture_output=True, text=True, timeout=timeout)

    def calls(self, **match):
        p = os.path.join(self.dir, "fake.jsonl")
        out = [json.loads(l) for l in open(p)] if os.path.exists(p) else []
        return [c for c in out if all(c.get(k) == v for k, v in match.items())]

    def path(self, *p):
        return os.path.join(self.dir, *p)

    def read(self, *p):
        return open(self.path(*p)).read()

    def verdict(self, lane, d):
        return json.load(open(self.path("lanes", lane, f"round{d}", "verdict.json")))

    def config(self, text):
        with open(self.path("config.env"), "a") as f:
            f.write(text + "\n")

    def accounts(self, state):
        json.dump(state, open(self.path("caam.json"), "w"))

    def caam(self):
        return json.load(open(self.path("caam.json")))


def lanes(*ids, deps=None):
    return [{"id": i, "role": "owner", "surface": "web", "depends_on": (deps or {}).get(i, [])} for i in ids]


class Preflight(unittest.TestCase):
    def test_engine_question_blocks_launch_until_confirmed(self):
        r = Run("preflight", lanes("maya"), config="ENGINE_CONFIRMED=")
        out = r.sh("bash", r.path("scripts", "preflight.sh"), r.dir).stdout
        self.assertIn("FAIL  tester engines not chosen", out)
        self.assertIn("Sonnet, medium effort (Recommended)", out)
        with open(r.path("config.env"), "a") as f:
            f.write("ENGINE_CONFIRMED=yes\n")
        out = r.sh("bash", r.path("scripts", "preflight.sh"), r.dir).stdout
        self.assertIn("PASS  engines chosen: TESTER=claude sonnet medium", out)
        self.assertIn("PASS  engine CLI: claude", out)


class Prompt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Run("prompt", lanes("maya", "leo"), tester={"maya": {"findings": [{"title": "Save button loses the draft"}]}})
        open(cls.r.path("stack-faults.md"), "wb").write(b"- Uploads fail on this stack: the fake storage has no bucket.\n"
                                                       b"- **Stack outage \x80\x9317:41** (an old api-watch line)\n")
        cls.res = cls.r.crowd("run")

    def test_run_finished(self):
        self.assertEqual(self.res.returncode, 0, self.res.stdout + self.res.stderr)
        for l in ("maya", "leo"):
            for d in (1, 2):
                self.assertTrue(os.path.exists(self.r.path("lanes", l, f"round{d}", "verdict.json")))

    def test_shared_prefix_is_identical_across_characters(self):
        a = self.r.read("lanes", "maya", "round1", "prompt.md")
        b = self.r.read("lanes", "leo", "round1", "prompt.md")
        cut = a.index("## Your assignment")
        self.assertGreater(cut, 3000)
        self.assertEqual(a[:cut], b[:cut])
        self.assertNotIn("Maya", a[:cut])

    def test_only_todays_scenarios_are_in_the_prompt(self):
        p = self.r.read("lanes", "maya", "round1", "prompt.md")
        self.assertIn("### S1", p)
        self.assertNotIn("### S4", p)
        self.assertIn("## Exhaustive sweep", p)
        p2 = self.r.read("lanes", "maya", "round2", "prompt.md")
        self.assertIn("### S4", p2)
        self.assertNotIn("### S1 ", p2)

    def test_known_stack_faults_are_in_the_prompt(self):
        self.assertIn("Uploads fail on this stack", self.r.read("lanes", "leo", "round1", "prompt.md"))

    def test_prompt_is_valid_utf8_even_when_a_source_file_is_not(self):
        open(self.r.path("lanes", "leo", "round1", "prompt.md"), "rb").read().decode("utf-8")

    def test_day2_rechecks_yesterdays_bugs_instead_of_carrying_them(self):
        p2 = self.r.read("lanes", "maya", "round2", "prompt.md")
        self.assertIn("Your bugs from day 1 (already recorded: re-check, never report again)", p2)
        self.assertIn("Save button loses the draft", p2)
        self.assertNotIn("carry forward", p2)

    def test_day2_repeat_is_rejected_in_code(self):
        v = self.verdict("maya", 2)
        self.assertEqual(v["verified_findings"], [])
        self.assertTrue(any("repeat of an earlier day" in x["reason"] for x in v["rejected"]))

    def verdict(self, l, d):
        return self.r.verdict(l, d)

    def test_claude_testers_start_lean_with_a_budget(self):
        t = [c for c in self.r.calls(kind="tester") if c["engine"] == "claude"][0]
        for flag in ("--setting-sources", "--strict-mcp-config", "--disable-slash-commands", "--max-budget-usd"):
            self.assertIn(flag, t["args"])
        self.assertEqual(t["args"][t["args"].index("--model") + 1], "sonnet")

    def test_brief_names_speed_and_block_rules(self):
        p = self.r.read("lanes", "maya", "round1", "prompt.md")
        self.assertIn("Never add a fixed `sleep`", p)
        self.assertIn("Before you call a scenario \"blocked\"", p)
        self.assertIn("do not load or read any\ninstalled skills", p)


class Engines(unittest.TestCase):
    def test_card_engine_and_per_engine_models(self):
        r = Run("engines", lanes("maya", "hacker"), config="CODEX_MODEL=gpt-test CODEX_EFFORT=max",
                cards={"hacker": "- **Engine**: codex\n"})
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        by = {(c["lane"], c["engine"]) for c in r.calls(kind="tester")}
        self.assertIn(("hacker", "codex"), by)
        self.assertIn(("maya", "claude"), by)
        cx = [c for c in r.calls(kind="tester") if c["engine"] == "codex"][0]["args"]
        self.assertIn("gpt-test", cx)
        self.assertIn('model_reasoning_effort=max', cx)

    def test_spent_chunk_continues_in_a_fresh_session(self):
        r = Run("chunk", lanes("maya"), tester={"maya": {"budget_first": True}}, config="DAYS=1")
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        calls = [c for c in r.calls(kind="tester") if c["day"] == "1"]
        self.assertEqual(len(calls), 2)
        sids = [c["args"][c["args"].index("--session-id") + 1] for c in calls if "--session-id" in c["args"]]
        self.assertEqual(len(set(sids)), 2, "the second chunk must be a new session")
        self.assertIn("chunk 1 spent", r.read("lanes", "maya", "round1", "resume.log"))
        self.assertIn("## RESUME", r.read("lanes", "maya", "round1", "prompt.md"))
        self.assertEqual(r.read("lanes", "maya", "round1", "DONE").strip(), "exit=0 engine=claude")

    def test_quota_pause_holds_only_that_engine(self):
        r = Run("quota", lanes("maya", "hacker"), tester={"hacker": {"limit_first": True}},
                cards={"hacker": "- **Engine**: codex\n"}, config="DAYS=1 RETRY_PRIMARY_MIN=60")
        p = r.crowd("run", background=True)
        self.addCleanup(lambda: p.poll() is None and os.killpg(p.pid, signal.SIGKILL))
        deadline = time.time() + 60
        while time.time() < deadline and not (os.path.exists(r.path("lanes", "maya", "round1", "verdict.json"))
                                              and os.path.exists(r.path("QUOTA_PAUSE.codex"))):
            time.sleep(0.5)
        self.assertTrue(os.path.exists(r.path("QUOTA_PAUSE.codex")), "codex hit its limit")
        self.assertTrue(os.path.exists(r.path("lanes", "maya", "round1", "verdict.json")), "claude characters keep going")
        self.assertFalse(os.path.exists(r.path("lanes", "hacker", "round1", "verdict.json")), "codex characters wait")
        # make the pause old enough: the driver lifts it and runs the codex character again
        old = time.time() - 3700
        os.utime(r.path("QUOTA_PAUSE.codex"), (old, old))
        deadline = time.time() + 60
        while time.time() < deadline and p.poll() is None:
            time.sleep(0.5)
        self.assertEqual(p.poll(), 0)
        self.assertTrue(os.path.exists(r.path("lanes", "hacker", "round1", "verdict.json")))
        self.assertTrue(glob.glob(r.path("QUOTA_PAUSE.codex.lifted-*")))

    def test_fallback_takes_over_only_the_engine_that_ran_out(self):
        r = Run("fallback", lanes("maya", "hacker"), tester={"hacker": {"limit_first": True}},
                cards={"hacker": "- **Engine**: codex\n"}, config="DAYS=1 FALLBACK_TESTER=claude FALLBACK_MODEL=sonnet")
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("from=codex", r.read("TESTER_FALLBACK"))
        hacker = [c["engine"] for c in r.calls(kind="tester", lane="hacker")]
        self.assertEqual(hacker, ["codex", "claude"])
        self.assertEqual([c["engine"] for c in r.calls(kind="tester", lane="maya")], ["claude"])


class Scheduling(unittest.TestCase):
    def test_dependents_wait_for_the_run_not_the_judge_and_days_do_not_barrier(self):
        r = Run("sched", lanes("shop", "buyer", "solo", deps={"buyer": ["shop"]}),
                tester={"shop": {"findings": [{"title": "Shop page shows the wrong price"}]}, "buyer": {"sleep": 4}})
        r.env["FAKE_JUDGE_SLEEP"] = "3"
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        log = r.read("logs", "crowd.log")

        def at(s):
            self.assertIn(s, log)
            return log.index(s)
        self.assertLess(at("shop d1: tester finished"), at("buyer d1: started"))
        self.assertLess(at("buyer d1: started"), at("judge:shop:d1:"), "buyer starts while shop is still being judged")
        self.assertLess(at("solo d2: started"), at("buyer d1: tester finished"), "no barrier between days")

    def test_a_satisfied_character_still_gets_day_2(self):
        r = Run("satisfied", lanes("maya"))
        r.crowd("run")
        self.assertTrue(r.verdict("maya", 1)["satisfied"])
        self.assertTrue(os.path.exists(r.path("lanes", "maya", "round2", "verdict.json")))

    def test_stack_gate_holds_a_character_until_the_stack_answers(self):
        r = Run("stack", lanes("maya"), config=f'DAYS=1 HEALTH_CHECKS="api|test -f {WORK}/stack/healthy"')
        os.makedirs(r.path("lanes", "maya", "round1"), exist_ok=True)
        p = subprocess.Popen(["bash", r.path("scripts", "run-tester.sh"), r.dir, "maya", "1"], env=r.env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(4)
        self.assertEqual(json.load(open(r.path("lanes", "maya", "state.json")))["status"], "waiting-stack")
        self.assertEqual(r.calls(kind="tester"), [])
        open(r.path("healthy"), "w").close()
        p.wait(timeout=60)
        self.assertEqual(len(r.calls(kind="tester")), 1)

    def test_a_runner_that_dies_is_judged_not_waited_on(self):
        r = Run("die", lanes("maya"), tester={"maya": {"die": True}}, config="DAYS=1")
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("runner-died", r.read("lanes", "maya", "round1", "DONE"))
        self.assertTrue(os.path.exists(r.path("lanes", "maya", "round1", "verdict.json")))

    def test_restarted_driver_waits_for_live_runners_and_never_starts_a_second(self):
        r = Run("restart", lanes("maya"), tester={"maya": {"sleep": 6}}, config="DAYS=1")
        p = r.crowd("run", background=True)
        deadline = time.time() + 20
        while time.time() < deadline and not r.calls(kind="tester"):
            time.sleep(0.3)
        os.killpg(p.pid, signal.SIGKILL)   # the driver dies; its runner (own session) lives on
        p.wait()
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(len(r.calls(kind="tester")), 1)
        self.assertTrue(os.path.exists(r.path("lanes", "maya", "round1", "verdict.json")))


class Judging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = Run("judge", lanes("maya", "quiet"), config="DAYS=1", tester={
            "maya": {"status": {"S2": "blocked", "S3": "not_run"}, "findings": [
                {"title": "Inbox count is wrong after archiving", "shots": "ok"},
                {"title": "Profile photo upload never finishes", "shots": "none"},
                {"title": "Settings page loses the timezone", "shots": "missing"},
                {"title": "Old already filed export bug returns", "shots": "ok"},
                {"title": "REJECTME tooltip overlaps", "shots": "ok"}]}})
        open(cls.r.path("filed.txt"), "w").write("#7 Old already filed export bug returns\n")
        cls.r.crowd("run")
        cls.v = cls.r.verdict("maya", 1)

    def test_rejections_that_need_no_model(self):
        by = {x["title"]: x for x in self.v["rejected"]}
        self.assertEqual(by["Profile photo upload never finishes"]["reason"], "no evidence attached")
        self.assertIn("evidence files missing", by["Settings page loses the timezone"]["reason"])
        self.assertIn("already filed as #7", by["Old already filed export bug returns"]["reason"])
        self.assertEqual(by["REJECTME tooltip overlaps"]["by"], "judge")
        self.assertEqual([f["title"] for f in self.v["verified_findings"]], ["Inbox count is wrong after archiving"])

    def test_the_judge_sees_only_findings_that_passed_the_code_checks(self):
        step = [c for c in self.r.calls(kind="step") if c["step"] == "judge:maya:d1"]
        self.assertEqual(len(step), 1)

    def test_coverage_is_computed_from_the_plan(self):
        self.assertEqual(self.v["coverage_score"], 33)
        self.assertEqual(self.v["scenarios"], {"ran": ["S1"], "blocked": ["S2"], "missing": ["S3"]})
        fb = self.r.read("lanes", "maya", "feedback-r1.md")
        self.assertIn("Run S2 (it was blocked)", fb)
        self.assertIn("Run S3 (it was not run)", fb)

    def test_a_day_with_no_findings_needs_no_model(self):
        self.assertEqual([c for c in self.r.calls(kind="step") if c["step"].startswith("judge:quiet")], [])
        self.assertTrue(self.r.verdict("quiet", 1)["satisfied"])


class Planning(unittest.TestCase):
    def test_planners_write_plans_and_a_failed_plan_skips_only_that_character(self):
        r = Run("plan", lanes("maya", "noplan"), plans=False, config="DAYS=1")
        os.remove(r.path("product-map.md"))
        res = r.crowd("plan")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertTrue(os.path.exists(r.path("product-map.md")))
        self.assertIn("## Day 1", r.read("lanes", "maya", "scenarios.md"))
        self.assertEqual(json.load(open(r.path("state", "skipped.json"))), ["noplan"])
        self.assertEqual(len([c for c in r.calls(kind="step") if c["step"] == "plan:noplan"]), 2, "retried once")
        r.crowd("run")
        self.assertTrue(os.path.exists(r.path("lanes", "maya", "round1", "verdict.json")))
        self.assertFalse(os.path.exists(r.path("lanes", "noplan", "round1")))
        self.assertEqual(r.crowd("plan").returncode, 0)
        self.assertEqual(len([c for c in r.calls(kind="step") if c["step"] == "plan:maya"]), 1, "existing plans are kept")


class Filing(unittest.TestCase):
    def test_merge_confirmers_labels_and_github_filing(self):
        r = Run("file", lanes("maya", "leo"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app" FILE_AUTO=yes', tester={
            "maya": {"findings": [{"title": "Archive button deletes the whole conversation thread", "route": "/inbox"},
                                  {"title": "REFUSE weird spacing on cards", "route": "/cards"},
                                  {"title": "REPAIR login banner shows twice", "route": "/login"},
                                  {"title": "HIGH CAUSE payment page double charges", "route": "/pay", "severity": "high"}]},
            "leo": {"findings": [{"title": "Archive button deletes the whole conversation thread too", "route": "/inbox"}]}})
        res = r.crowd("all")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        issues = json.load(open(r.path("state", "issues.json")))
        archive = [i for i in issues if i["title"].startswith("Archive button")]
        self.assertEqual(len(archive), 1, "two characters, one bug")
        self.assertEqual([c["id"] for c in archive[0]["confirmers"]], ["leo"])
        self.assertIn("persona:leo", archive[0]["labels"])
        self.assertIn("role:owner", archive[0]["labels"])
        filed = json.load(open(r.path("state", "filed.json")))
        titles = sorted(v["title"] for v in filed.values())
        self.assertEqual(len(titles), 3)
        self.assertTrue(any(t.startswith("REPAIR") for t in titles), "repaired evidence gets filed")
        refused = json.load(open(r.path("state", "refused.json")))
        self.assertEqual([v["title"] for v in refused.values()], ["REFUSE weird spacing on cards"])
        body = [c for c in r.calls(kind="gh-body") if c["title"].startswith("Archive")][0]["body"]
        for part in ("> \"", "**Steps to reproduce**", "**Also hit by**", "**Leo**", "qa-evidence/crowd-test/issue-",
                     "Found in the"):
            self.assertIn(part, body)
        models = {c["step"].split(":")[0]: c["model"] for c in r.calls(kind="step") if c["step"].startswith("verify:")}
        hi = [c for c in r.calls(kind="step") if c["step"].startswith("verify:") and c["model"] == "opus"]
        self.assertTrue(hi, "high severity and unconfirmed causes are checked on the escalation model")
        self.assertTrue(models)
        self.assertEqual(len(r.read("filed.txt").strip().splitlines()), 3)
        self.assertTrue(os.path.exists(r.path("LAUNCH_REPORT.md")))
        self.assertIn("| maya | owner | 1 |", r.read("state", "report-pack.md"))
        # a second run files nothing again and calls no model
        n = len(r.calls())
        res = r.crowd("all")
        self.assertEqual(res.returncode, 0)
        new = r.calls()[n:]
        self.assertEqual([c for c in new if c["kind"] in ("tester", "gh-body")], [])
        self.assertEqual([c["step"] for c in new if c["kind"] == "step"], [], "nothing changed, so no report either")

    def test_the_file_plan_checks_issues_and_comments_and_waits_for_approval(self):
        r = Run("fileplan", lanes("maya"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app"', tester={"maya": {"findings": [
            {"title": "Archive button deletes the whole conversation thread"}, {"title": "Search ignores accents in names"},
            {"title": "Export drops the last row of the file"}, {"title": "Coupon field accepts any length"}]}})
        snap = [{"number": 41, "title": "Search ignores accents in names", "state": "OPEN", "body": "", "labels": [],
                 "comments": [], "url": ""},
                {"number": 12, "title": "Export drops the last row of the file", "state": "CLOSED", "stateReason": "COMPLETED",
                 "body": "", "labels": [], "comments": [], "url": ""},
                {"number": 30, "title": "Coupon field length", "state": "CLOSED", "stateReason": "NOT_PLANNED",
                 "body": "Coupon field accepts any length", "labels": [],
                 "comments": [{"author": {"login": "dev"}, "createdAt": "2026-10-02", "body": "This is intended."}], "url": ""}]
        json.dump(snap, open(r.path("snap.json"), "w"))
        r.env["FAKE_GH_SNAPSHOT"] = r.path("snap.json")
        r.crowd("run")
        res = r.crowd("file")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(r.calls(kind="gh-body") + r.calls(kind="gh-comment"), [], "nothing is sent before approval")
        self.assertIn("nothing sent", r.read("logs", "crowd.log"))
        md = r.read("file-plan.md")
        for part in ("New issues", "Comment on an open issue", "Regression of a closed issue", "Skipped", "This is intended."):
            self.assertIn(part, md)
        snapshot = json.load(open(r.path("state", "issues-snapshot-app.json")))
        self.assertEqual(snapshot[2]["comments"][0]["text"], "This is intended.", "comments are part of the snapshot")
        res = r.crowd("approve")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        filed = json.load(open(r.path("state", "filed.json")))
        acts = sorted((v["action"], v.get("target")) for v in filed.values())
        self.assertEqual(acts, [("comment", 41), ("create", None), ("regression", 12)])
        reg = [c for c in r.calls(kind="gh-body") if c["title"].startswith("Export")][0]
        self.assertIn("**Regression of #12**", reg["body"])
        self.assertIn("by #5", reg["body"])
        self.assertIn("**Also hit by Maya**", r.calls(kind="gh-comment")[0]["body"])
        refused = json.load(open(r.path("state", "refused.json")))
        self.assertTrue(any(v["reason"].startswith("skipped: intended (#30)") for v in refused.values()))
        self.assertEqual(len([c for c in r.calls(kind="step") if c["step"].startswith("file-plan:")]), 1, "one batch call")

    def test_an_approval_covers_only_the_plan_lines_that_existed_when_it_was_given(self):
        r = Run("approvekeys", lanes("maya"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app"',
                tester={"maya": {"findings": [{"title": "Archive button deletes the whole conversation thread"}]}})
        r.crowd("run")
        r.crowd("file")
        r.crowd("approve")
        self.assertEqual(len(json.load(open(r.path("state", "filed.json")))), 1)
        issues = json.load(open(r.path("state", "issues.json")))
        issues.append({**issues[0], "title": "Export drops the last row of the file", "key": "later0000001"})
        json.dump(issues, open(r.path("state", "issues.json"), "w"))
        r.crowd("file")
        self.assertEqual(len(json.load(open(r.path("state", "filed.json")))), 1, "a bug planned after approval waits")
        self.assertIn("1 lines wait for approval", r.read("logs", "crowd.log"))

    def test_file_cmd_hook(self):
        r = Run("hook", lanes("maya"), config="DAYS=1", tester={"maya": {"findings": [{"title": "Search ignores accents"}]}})
        r.crowd("run")
        r.crowd("dedupe")
        res = r.crowd("file")
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertIn("HOOK-0 Search ignores accents", r.read("filed.txt"))
        sent = json.load(open(r.path("hooked", "0.json")))
        self.assertEqual(sent["title"], "Search ignores accents")
        self.assertIn("**Expected**", sent["body"])


class Fixing(unittest.TestCase):
    def repo(self, r):
        d = r.path("product-repo")
        os.makedirs(d, exist_ok=True)
        for c in (["init", "-q", "-b", "main"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"]):
            r.sh("git", "-C", d, *c)
        return d

    def fix_run(self, name, extra=""):
        r = Run(name, lanes("maya", "leo"), config=f'DAYS=1 FILE_CMD="" TRACKERS="app=owner/app" FILE_AUTO=yes FIXING=on '
                f'REVIEW_ENGINE=claude {extra}', tester={
                    "maya": {"findings": [{"title": "Archive button deletes the whole conversation thread", "route": "/inbox"},
                                          {"title": "Search ignores accents in names", "route": "/search"}]},
                    "leo": {"findings": []}})
        with open(r.path("config.env"), "a") as f:
            f.write(f'REPOS="app={self.repo(r)}"\n')
        return r

    def roles(self, r):
        return [c["role"] for c in r.calls(kind="job")]

    def test_cli_runner_fix_review_recheck_and_retry_until_the_reporters_pass_it(self):
        r = self.fix_run("fixcli", "RUNNER=cli")
        r.env["FAKE_REVIEW_CHANGES_ONCE"] = "1"
        r.env["FAKE_RECHECK_FAIL_ONCE"] = "1"
        res = r.crowd("all", timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.roles(r), ["fix", "review", "fix", "review", "recheck", "fix", "review", "recheck"])
        jobs = r.calls(kind="job")
        wt = r.path("fix", "wt", "inbox-flows")
        self.assertTrue(all(c["cwd"] == os.path.realpath(wt) or c["cwd"] == wt for c in jobs), "every job runs in the area's worktree")
        self.assertIn("fix/crowd-test-inbox-flows", r.sh("git", "-C", wt, "branch", "--show-current").stdout)
        review = [c["prompt"] for c in jobs if c["role"] == "review"]
        self.assertFalse(any("changed " in p for p in review), "the reviewer never sees the fixer's notes")
        self.assertIn("no guard", review[1], "the second review checks the first review's comment")
        recheck = [c["prompt"] for c in jobs if c["role"] == "recheck"]
        self.assertIn("re-test: open the inbox", recheck[0])
        self.assertFalse(any("changed " in p for p in recheck))
        self.assertEqual(len(jobs[5]["keys"]), 1, "only the bug that failed the re-check goes back to the fixer")
        self.assertIn("still deletes", jobs[5]["prompt"])
        areas = json.load(open(r.path("state", "areas-detail.json")))
        self.assertEqual(areas["inbox-flows"]["status"], "done")
        self.assertEqual(set(areas["inbox-flows"]["verdicts"].values()), {"PASS"})
        body = r.calls(kind="gh-pr-edit")[-1]["body"]
        for part in ("| issue | reporter | re-check | note |", "### Fixes #", "Reported by **maya**", "**Cause:** src/app.js:10",
                     "-before.png", "-after.png", "-recheck.png", "**Re-check by maya: PASS.**", "<summary>Checks",
                     "<summary>Review</summary>", "no guard", "Generated with [Claude Code]"):
            self.assertIn(part, body)
        prs = json.load(open(r.path("state", "prs.json")))
        self.assertEqual(prs["inbox-flows"]["recheck"], {"pass": 2, "partial": 0, "fail": 0})
        self.assertIn("## Fix pull requests", r.read("state", "report-pack.md"))
        self.assertIn("fix areas: inbox-flows=done", r.crowd("status").stdout)

    def test_a_disputed_review_comment_goes_to_a_bout_and_the_loser_drops_it(self):
        r = self.fix_run("fixbout", "RUNNER=cli")
        r.env.update(FAKE_REVIEW_CHANGES_ONCE="1", FAKE_REBUT="1", FAKE_BOUT_WINNER="b")
        res = r.crowd("all", timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        bouts = [json.load(open(p)) for p in glob.glob(r.path("state", "bouts", "review-*.json"))]
        self.assertEqual(len(bouts), 1)
        self.assertEqual((bouts[0]["winner"], bouts[0]["a"], bouts[0]["b"]), ("b", "reviewer", "fixer"))
        self.assertEqual(len(bouts[0]["rounds"]), 2, "the given claim and answer, then one more round")
        review2 = [c["prompt"] for c in r.calls(kind="job") if c["role"] == "review"][1]
        self.assertIn("must not be raised again", review2)
        self.assertIn("Disputes settled by a judge", r.calls(kind="gh-pr-edit")[-1]["body"])

    def test_a_bug_another_character_could_not_reproduce_meets_them_in_a_bout_before_filing(self):
        r = Run("cantrepro", lanes("maya"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app" FILE_AUTO=yes',
                tester={"maya": {"findings": [{"title": "Export drops the last row of the file"}]}})
        r.crowd("run")
        r.crowd("dedupe")
        issues = json.load(open(r.path("state", "issues.json")))
        issues[0]["cant_repro"] = ["**Leo** could not reproduce: the export had every row."]
        json.dump(issues, open(r.path("state", "issues.json"), "w"))
        r.env["FAKE_BOUT_WINNER"] = "a"
        r.crowd("file")
        self.assertEqual(r.calls(kind="gh-body"), [])
        self.assertIn("sided with the can't-repro report", json.dumps(json.load(open(r.path("state", "refused.json")))))
        b = json.load(open(glob.glob(r.path("state", "bouts", "square-*.json"))[0]))
        self.assertEqual((b["a"], b["b"], b["winner"]), ("can't-repro", "Maya", "a"))

    def test_a_judge_that_gives_no_verdict_decides_nothing(self):
        r = Run("noverdict", lanes("maya"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app" FILE_AUTO=yes',
                tester={"maya": {"findings": [{"title": "Export drops the last row of the file"}]}})
        r.crowd("run")
        r.crowd("dedupe")
        issues = json.load(open(r.path("state", "issues.json")))
        issues[0]["cant_repro"] = ["**Leo** could not reproduce."]
        json.dump(issues, open(r.path("state", "issues.json"), "w"))
        r.env["FAKE_BOUT_NO_VERDICT"] = "1"
        r.crowd("file")
        self.assertEqual(len(r.calls(kind="gh-body")), 1, "the bug is filed, not thrown out by a failed judge")
        b = json.load(open(glob.glob(r.path("state", "bouts", "square-*.json"))[0]))
        self.assertEqual((b["status"], b["winner"]), ("no-verdict", ""))

    def test_reporting_a_job_twice_queues_its_follow_up_once(self):
        r = self.fix_run("fixtwice", "RUNNER=t3")
        r.crowd("all", timeout=300)
        j = json.loads(r.crowd("jobs", "next").stdout)[0]
        json.dump({"fixed": j["keys"], "branch": j["branch"]}, open(j["result"], "w"))
        r.crowd("jobs", "done", j["id"])
        r.crowd("jobs", "done", j["id"])
        reviews = [k for k in json.load(open(r.path("state", "jobs.json"))) if k.startswith("review-")]
        self.assertEqual(len(reviews), 1)

    def test_the_merge_check_orders_the_branches_and_names_the_conflict(self):
        r = self.fix_run("fixmerge", "RUNNER=cli CHECK_CMD=true")
        r.env.update(FAKE_GROUP_SPLIT="1", FAKE_FIX_COMMIT="1")
        res = r.crowd("all", timeout=400)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        mc = json.load(open(r.path("state", "merge-check.json")))["app"]
        self.assertEqual(mc["order"], ["area-0"])
        self.assertEqual(mc["conflicts"], [{"area": "area-1", "files": ["shared.txt"]}])
        self.assertTrue(mc["checks"]["ok"])
        self.assertIn("Do not merge cleanly", r.read("merge-check.md"))
        bodies = {c["body"].split("area **")[1].split("**")[0]: c["body"] for c in r.calls(kind="gh-pr-edit")}
        self.assertIn("**Merge order:** 1 of 1, first", bodies["area-0"])
        self.assertIn("**Merge conflict:**", bodies["area-1"])
        self.assertIn("shared.txt", bodies["area-1"])
        self.assertFalse(os.path.exists(r.path("fix", "merge-check", "app")), "the scratch worktree is removed")

    def test_retries_run_out_and_the_pr_is_labelled_needs_human(self):
        r = self.fix_run("fixhuman", "RUNNER=cli FIX_RETRIES=0")
        r.env["FAKE_RECHECK_FAIL_ONCE"] = "1"
        r.crowd("all", timeout=300)
        self.assertEqual(json.load(open(r.path("state", "areas-detail.json")))["inbox-flows"]["status"], "needs-human")
        self.assertTrue([c for c in r.calls(kind="gh") if c["args"][:2] == ["pr", "edit"] and "needs-human" in c["args"]])

    def test_t3_runner_jobs_are_launched_by_the_session_and_reviews_reuse_the_fixers_worktree(self):
        r = self.fix_run("fixt3", "RUNNER=t3")
        res = r.crowd("all", timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(r.calls(kind="job"), [], "crowd.py starts no agent itself")
        self.assertIn("jobs ready for the t3 runner", r.read("logs", "crowd.log"))
        nxt = json.loads(r.crowd("jobs", "next").stdout)
        self.assertEqual([(j["role"], j["launch"]) for j in nxt], [("fix", "new-worktree")])
        self.assertIn("# Fix brief", nxt[0]["prompt_text"])
        self.assertIn("the worktree this thread started in", nxt[0]["prompt_text"])
        self.assertEqual(json.loads(r.crowd("jobs", "next").stdout), [], "a claimed job is not handed out twice")
        wt = r.path("t3-worktree")
        os.makedirs(wt)
        r.crowd("jobs", "start", nxt[0]["id"], "--runner", "t3", "--model", "claude-team3:claude-opus-5-5",
                "--thread", "T-1", "--worktree", wt)
        json.dump({"fixed": nxt[0]["keys"], "branch": nxt[0]["branch"], "pr": "https://github.com/owner/app/pull/9",
                   "worktree": wt}, open(nxt[0]["result"], "w"))
        done = json.loads(r.crowd("jobs", "done", nxt[0]["id"]).stdout)
        self.assertEqual(done["ready"], 1)
        rev = json.loads(r.crowd("jobs", "next").stdout)[0]
        self.assertEqual((rev["role"], rev["launch"], rev["worktree"]), ("review", "existing-worktree", wt))
        self.assertIn(wt, rev["prompt_text"])
        r.crowd("jobs", "start", rev["id"], "--thread", "T-2")
        self.assertIn("1 jobs queued again", r.crowd("jobs", "requeue-running").stdout, "a session that died leaves the job to rerun")
        jobs = json.load(open(r.path("state", "jobs.json")))
        self.assertEqual(jobs[nxt[0]["id"]]["model"], "claude-team3:claude-opus-5-5")
        self.assertIn("T-1", r.crowd("jobs", "list").stdout)

    def test_a_mobile_area_gets_a_device_and_a_build_never_a_browser(self):
        r = self.fix_run("fixmobile", 'RUNNER=t3 TRACKER_KIND="app=mobile"')
        res = r.crowd("all", timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        brief = json.loads(r.crowd("jobs", "next").stdout)[0]["prompt_text"]
        self.assertIn("fix-device.sh", brief)
        self.assertIn("never run it in a browser or build it for web", brief)
        self.assertIn("## Device", brief)
        self.assertNotIn("fix-browser.sh", brief)
        self.assertNotIn("fix-stack.sh", brief)
        self.assertNotIn("{{", brief)

    def test_fix_from_runs_adds_the_issues_an_earlier_run_created(self):
        old = os.path.join(WORK, "fixfrom-parent")
        shutil.rmtree(old, ignore_errors=True)
        os.makedirs(os.path.join(old, "state"))
        json.dump({"k-old": {"url": "https://github.com/owner/app/issues/41", "title": "COD order shows paid", "tracker": "app"},
                   "k-dup": {"url": "https://github.com/owner/app/issues/12#issuecomment-1", "title": "Seen again",
                             "tracker": "app", "action": "comment"}}, open(os.path.join(old, "state", "filed.json"), "w"))
        json.dump([{"key": "k-old", "reporter": "kareem", "severity": "high"}], open(os.path.join(old, "state", "issues.json"), "w"))
        r = self.fix_run("fixfrom", f'RUNNER=t3 FIX_FROM_RUNS="{old}"')
        res = r.crowd("all", timeout=300)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        areas = json.load(open(r.path("state", "areas-detail.json")))
        keys = [k for a in areas.values() for k in a["bugs"]]
        self.assertIn("k-old", keys)
        self.assertNotIn("k-dup", keys, "a comment on someone else's issue is not ours to fix")
        prompts = " ".join(j["prompt_text"] for j in json.loads(r.crowd("jobs", "next").stdout))
        self.assertIn(f"filed by an earlier run: issue, card and diary under {old}", prompts)

    def test_a_job_without_a_valid_result_is_tried_again_then_left_to_a_person(self):
        r = self.fix_run("fixbad", "RUNNER=t3")
        r.crowd("all", timeout=300)
        j = json.loads(r.crowd("jobs", "next").stdout)[0]
        r.crowd("jobs", "done", j["id"])
        self.assertEqual(json.load(open(r.path("state", "jobs.json")))[j["id"]]["status"], "queued")
        r.crowd("jobs", "done", j["id"])
        self.assertEqual(json.load(open(r.path("state", "jobs.json")))[j["id"]]["status"], "failed")
        self.assertEqual(json.load(open(r.path("state", "areas-detail.json")))["inbox-flows"]["status"], "needs-human")


class EdgeCases(unittest.TestCase):
    def test_a_rate_limited_model_step_waits_and_tries_again(self):
        r = Run("limit", lanes("maya"), plans=False, config="DAYS=1 LLM_LIMIT_WAIT_S=0.2")
        r.env["FAKE_LIMIT_STEP"] = "plan:"
        self.assertEqual(r.crowd("plan").returncode, 0)
        self.assertIn("## Day 1", r.read("lanes", "maya", "scenarios.md"))
        self.assertEqual(len(r.calls(kind="limited")), 1)
        self.assertIn("model limit", r.read("logs", "crowd.log"))
        self.assertEqual(json.load(open(r.path("state", "skipped.json"))), [])

    def test_a_bad_answer_is_asked_for_again_in_the_same_session(self):
        r = Run("badjson", lanes("maya"), config="DAYS=1", tester={"maya": {"findings": [{"title": "Export drops the last row"}]}})
        r.env["FAKE_BAD_JSON_STEP"] = "judge:"
        r.crowd("run")
        self.assertEqual(len(r.calls(kind="resume-step")), 1, "the retry resumes the session instead of starting over")
        self.assertEqual(len(r.verdict("maya", 1)["verified_findings"]), 1)

    def test_a_failed_judge_leaves_the_day_to_be_judged_again_not_its_bugs_rejected(self):
        r = Run("judgefail", lanes("maya"), config="DAYS=1", tester={"maya": {"findings": [{"title": "Export drops the last row"}]}})
        r.env["FAKE_JUDGE_FAIL"] = "1"
        r.crowd("run")
        self.assertFalse(os.path.exists(r.path("lanes", "maya", "round1", "verdict.json")))
        self.assertIn("no verdict written", r.read("logs", "crowd.log"))
        r.crowd("run")
        self.assertEqual(len(r.verdict("maya", 1)["verified_findings"]), 1)
        self.assertEqual(len(r.calls(kind="tester")), 1, "the tester is not run again")

    def test_similar_but_not_identical_titles_go_to_the_judge(self):
        r = Run("similar", lanes("maya"), config="DAYS=1",
                tester={"maya": {"findings": [{"title": "Export button drops the last row of the CSV file"}]}})
        open(r.path("filed.txt"), "w").write("#9 Export button drops the header row of the CSV\n")
        r.crowd("run")
        self.assertTrue([c for c in r.calls(kind="step") if c["step"].startswith("judge:") and c["dup_note"]])
        self.assertEqual(len(r.verdict("maya", 1)["verified_findings"]), 1)

    def test_a_filing_error_is_retried_and_an_existing_title_is_not_filed_twice(self):
        r = Run("fileerr", lanes("maya"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app" FILE_AUTO=yes', tester={"maya": {"findings": [
            {"title": "Archive button deletes the whole conversation thread"}, {"title": "Search ignores accents in names"}]}})
        r.env["FAKE_GH_FAIL_ONCE"] = "1"
        r.env["FAKE_GH_EXISTING"] = "Search ignores accents in names"
        r.crowd("all")
        filed = json.load(open(r.path("state", "filed.json")))
        self.assertEqual(json.load(open(r.path("state", "refused.json"))), {}, "a filing error is not a refusal")
        self.assertIn("https://github.com/owner/app/issues/77", [v["issue"] for v in filed.values()])
        self.assertEqual(len(filed), 1)
        r.crowd("file")
        filed = json.load(open(r.path("state", "filed.json")))
        self.assertEqual(len(filed), 2)
        self.assertEqual([c["title"] for c in r.calls(kind="gh-body")], ["Archive button deletes the whole conversation thread"])
        self.assertEqual([c["target"] for c in r.calls(kind="gh-comment")], ["77"], "the open issue gets a comment instead")

    def test_status_counts_claude_tester_sessions(self):
        r = Run("status", lanes("maya"), config="DAYS=1")
        r.crowd("run")
        self.assertTrue(r.read("lanes", "maya", "round1", "claude-sessions.txt").strip())
        self.assertIn("Claude testers $", r.crowd("status").stdout)


class Watchers(unittest.TestCase):
    def test_api_watch_once_and_capacity_swap_cap(self):
        r = Run("watch", lanes("maya"), config='HEALTH_CHECKS="ok|true; bad|false" SWAP_MAX_MB=-1')
        res = r.sh("bash", r.path("scripts", "api-watch.sh"), r.dir, "once")
        self.assertEqual(res.returncode, 1)
        self.assertIn("failing: bad", res.stdout)
        res = r.sh("bash", r.path("scripts", "capacity.sh"), r.dir, "check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("swap", res.stdout)


def plan_text(days):
    """days: {day: number of scenarios}"""
    out, n = "", 0
    for d, k in sorted(days.items()):
        out += f"## Day {d}\n"
        for _ in range(k):
            n += 1
            out += f"### S{n} — step {n}  [failure]\nSteps: x\n"
    return out + "## Exhaustive sweep\n- every screen\n"


class Browsers(unittest.TestCase):
    """browser-pool.sh: slot accounting, shrinking BROWSER_SLOTS, ports another session holds, remote gates."""

    def setUp(self):
        self.r = Run(self._testMethodName[5:20], lanes("maya"), config=(
            'BROWSER_SLOTS="remote:1 remote:2" REMOTE_HOST=fakehost REMOTE_MIN_FREE_MB="" REMOTE_MAX_LOAD_PER_CORE="" '
            'REMOTE_CDP_BASE=47320 BROWSER_POLL_S=0.2'))
        self.holders = []

    def tearDown(self):
        for h in self.holders:
            h.poll() is None and h.kill()

    def holder(self):
        h = subprocess.Popen(["sleep", "120"])
        self.holders.append(h)
        return h

    def acquire(self, lane, holder, timeout=8):
        try:
            res = self.r.sh("bash", self.r.path("scripts", "browser-pool.sh"), self.r.dir, "acquire", lane, str(holder.pid),
                            timeout=timeout)
            return res.stdout.strip().splitlines()[-1]
        except subprocess.TimeoutExpired:
            return None

    def release(self, lane):
        self.r.sh("bash", self.r.path("scripts", "browser-pool.sh"), self.r.dir, "release", lane)

    def test_slots_are_counted_and_a_dead_holder_frees_its_slot(self):
        a, b = self.holder(), self.holder()
        self.assertEqual(self.acquire("ana", a), "remote:1")
        self.assertEqual(self.acquire("ben", b), "remote:2")
        self.assertIsNone(self.acquire("cai", self.holder(), timeout=2), "two slots, two live browsers: the third waits")
        self.assertIn("[cai] WAIT browser", self.r.read("capacity.log"))
        a.kill()
        a.wait()
        self.assertEqual(self.acquire("cai", self.holder()), "remote:1")

    def test_shrinking_the_slots_applies_while_old_holders_still_run(self):
        self.r.config('BROWSER_SLOTS="remote:1 remote:2 remote:3"')
        for lane in ("ana", "ben", "cai"):
            self.acquire(lane, self.holder())
        self.r.config('BROWSER_SLOTS="remote:1 remote:2"')
        self.release("ana")
        self.assertIsNone(self.acquire("dan", self.holder(), timeout=2), "ben and cai still hold 2 of the 2 slots now allowed")
        self.assertIn("all 2 remote browsers busy", self.r.read("capacity.log"))
        listing = self.r.sh("bash", self.r.path("scripts", "browser-pool.sh"), self.r.dir, "list").stdout
        self.assertIn("remote:3 cai (removed from BROWSER_SLOTS)", listing)
        self.release("ben")
        self.assertIn(self.acquire("dan", self.holder()), ("remote:1", "remote:2"))

    def test_a_port_another_session_holds_is_skipped_and_never_forwarded(self):
        import socket
        busy = socket.socket()
        busy.bind(("127.0.0.1", 47321))
        busy.listen()
        self.addCleanup(busy.close)
        self.assertEqual(self.acquire("ana", self.holder()), "remote:2")
        self.assertIn("port 47321 already listens here", self.r.read("capacity.log"))
        res = self.r.sh("bash", self.r.path("scripts", "remote-browser.sh"), self.r.dir, "start", "ana", "remote:1")
        self.assertEqual(res.returncode, 3)
        self.assertIn("refusing", res.stderr)
        self.assertEqual(self.r.calls(kind="ssh"), [], "nothing was started on the remote for a busy port")

    def test_remote_memory_gate_holds_new_browsers(self):
        self.r.config("REMOTE_MIN_FREE_MB=3000")
        self.r.env["FAKE_SSH_OUT"] = "8 1.0 1000"
        self.assertIsNone(self.acquire("ana", self.holder(), timeout=2))
        self.assertIn("remote free 1000MB < 3000MB", self.r.read("capacity.log"))
        self.r.env["FAKE_SSH_OUT"] = "8 1.0 8000"
        self.assertEqual(self.acquire("ana", self.holder()), "remote:1")

    def test_local_slots_cap_local_browsers_and_extra_browsers_share_the_pool(self):
        self.r.config('BROWSER_SLOTS="local:1 local:2"')
        a = self.holder()
        self.assertEqual(self.acquire("ana", a), "local:1")
        self.r.env["RUNNER_PID"] = str(a.pid)     # the runner holds its tester's extra browser
        res = self.r.sh("bash", self.r.path("scripts", "extra-browser.sh"), self.r.dir, "ana", "2")
        self.assertEqual(res.stdout.strip(), "crowd-ana-2")
        self.assertEqual(self.r.read(".browsers", "extra-ana-2").strip(), "local:2")
        self.assertIsNone(self.acquire("ben", self.holder(), timeout=2), "the extra browser took the last slot")
        self.release("ana")
        self.assertEqual(self.acquire("ben", self.holder()), "local:1")


class QuotaGuard(unittest.TestCase):
    """quota-guard.sh decisions, with a fake caam: rotate, never act on a missing reading, pause, fall back."""

    def guard(self, r):
        res = r.sh("bash", r.path("scripts", "quota-guard.sh"), r.dir, "once")
        self.assertEqual(res.returncode, 0, res.stderr)
        return r.read("quota.log") if os.path.exists(r.path("quota.log")) else ""

    def run_with(self, name, state, config=""):
        r = Run(name, lanes("maya"), config="CLAUDE_ACCOUNTS=\"a b\" PRIMARY_MAX=94 WEEKLY_MAX=95 SWITCH_HEADROOM=10 " + config)
        r.accounts(state)
        return r

    def activations(self, r):
        return [c["args"][1:] for c in r.calls(kind="caam") if c["args"][0] == "activate"]

    def test_no_reading_for_the_active_account_is_never_a_limit(self):
        r = self.run_with("g-noread", {"claude": {"active": "a", "profiles": {
            "a": {"p": "-", "w": "-"}, "b": {"p": "5", "w": "5"}}}})
        self.assertIn("no reading for active a; keeping it", self.guard(r))
        self.assertEqual(self.activations(r), [])
        self.assertFalse(os.path.exists(r.path("QUOTA_PAUSE.claude")))
        self.assertFalse(os.path.exists(r.path(".engine-switched")))

    def test_an_account_over_the_bar_switches_to_the_first_with_headroom(self):
        r = self.run_with("g-switch", {"claude": {"active": "a", "profiles": {
            "a": {"p": "97", "w": "40"}, "b": {"p": "20", "w": "30"}}}})
        self.assertIn("SWITCH claude a -> b", self.guard(r))
        self.assertEqual(r.caam()["claude"]["active"], "b")
        self.assertTrue(os.path.exists(r.path(".engine-switched")), "runners resume their session after a switch")

    def test_every_account_near_its_limit_pauses_and_a_reset_lifts_it(self):
        r = self.run_with("g-pause", {"claude": {"active": "a", "profiles": {
            "a": {"p": "97", "w": "40"}, "b": {"p": "90", "w": "30"}}}})
        r.env["FAKE_CLAUDE_REFUSE"] = "a,b"
        self.assertIn("PAUSE claude", self.guard(r))
        self.assertTrue(os.path.exists(r.path("QUOTA_PAUSE.claude")))
        st = r.caam()
        st["claude"]["profiles"]["b"]["p"] = "3"
        r.accounts(st)
        self.assertIn("RESUME claude on b", self.guard(r))
        self.assertFalse(os.path.exists(r.path("QUOTA_PAUSE.claude")))
        self.assertTrue(glob.glob(r.path("logs", "QUOTA_PAUSE.claude.lifted-*")))

    def test_an_unread_claude_account_is_tried_with_one_small_call(self):
        r = self.run_with("g-unread", {"claude": {"active": "a", "profiles": {
            "a": {"p": "97", "w": "40"}, "b": {"p": "-", "w": "-", "st": "?"}}}})
        self.assertIn("no reading for b, but a test call worked", self.guard(r))
        self.assertEqual(r.caam()["claude"]["active"], "b")
        r2 = self.run_with("g-unread2", {"claude": {"active": "a", "profiles": {
            "a": {"p": "97", "w": "40"}, "b": {"p": "-", "w": "-", "st": "?"}}}})
        r2.env["FAKE_CLAUDE_REFUSE"] = "b"
        log = self.guard(r2)
        self.assertIn("b refused a test call", log)
        self.assertEqual(r2.caam()["claude"]["active"], "a", "switched back after the failed test call")
        self.assertIn("PAUSE claude", log)

    def test_codex_falls_back_instead_of_pausing_and_comes_back(self):
        r = Run("g-codex", lanes("maya"), config='CODEX_ACCOUNTS="x y" FALLBACK_TESTER=claude CODEX_NEAR_MAX=85')
        r.accounts({"codex": {"active": "x", "profiles": {"x": {"p": "88", "w": "20"}, "y": {"p": "100", "w": "50"}}}})
        self.assertIn("FALLBACK codex", self.guard(r))
        self.assertIn("by=guard", r.read("TESTER_FALLBACK"))
        self.assertFalse(os.path.exists(r.path("QUOTA_PAUSE.codex")))
        r.accounts({"codex": {"active": "x", "profiles": {"x": {"p": "88", "w": "20"}, "y": {"p": "2", "w": "5"}}}})
        self.assertIn("SWITCH codex x -> y", self.guard(r))
        self.assertFalse(os.path.exists(r.path("TESTER_FALLBACK")), "new days start on Codex again")

    def test_a_guard_switch_resumes_the_same_tester_session(self):
        r = Run("g-resume", lanes("hacker"), config="DAYS=1", tester={"hacker": {"switch_first": True}},
                cards={"hacker": "- **Engine**: codex\n"})
        res = r.crowd("run")
        self.assertEqual(res.returncode, 0, res.stdout)
        calls = r.calls(kind="tester", lane="hacker")
        self.assertEqual(len(calls), 2)
        sid = r.read("lanes", "hacker", "round1", "tester.log").split("session id: ")[1].split()[0]
        self.assertEqual(calls[1]["args"][:3], ["exec", "resume", sid], "the same session continues, not a fresh one")
        self.assertIn("stopped by quota-guard", r.read("lanes", "hacker", "round1", "resume.log"))
        self.assertNotIn("## RESUME", r.read("lanes", "hacker", "round1", "prompt.md"), "the brief is not sent again")
        self.assertEqual(r.read("lanes", "hacker", "round1", "DONE").strip(), "exit=0 engine=codex")


class ModelCheck(unittest.TestCase):
    def test_preflight_tests_every_codex_account_with_the_tester_model(self):
        r = Run("modelcheck", lanes("maya"), config='TESTER=codex TESTER_MODEL=gpt-x CODEX_ACCOUNTS="a1 a2 a3"')
        r.accounts({"codex": {"active": "a2", "profiles": {
            "a1": {"plan": "go", "isolated": True}, "a2": {"plan": "plus", "isolated": True}, "a3": {"plan": "go"}}}})
        r.env["FAKE_CODEX_REFUSE"] = "a1:gpt-x,a3:gpt-x"
        out = r.sh("bash", r.path("scripts", "preflight.sh"), r.dir).stdout
        self.assertIn("FAIL  codex account a1 refuses model gpt-x", out)
        self.assertIn("PASS  codex account a2 accepts model gpt-x", out)
        self.assertIn("FAIL  codex account a3 is on a ChatGPT 'go' plan", out)
        self.assertEqual([c for c in r.calls(kind="caam") if c["args"][0] == "activate"], [],
                         "isolated profiles are tested without a machine-wide switch")
        r.config("PREFLIGHT_MODEL_PROBE=switch")
        out = r.sh("bash", r.path("scripts", "preflight.sh"), r.dir).stdout
        self.assertIn("FAIL  codex account a3 refuses model gpt-x", out)
        self.assertEqual(r.caam()["codex"]["active"], "a2", "the original account is active again")


class Plans(unittest.TestCase):
    def test_a_day_longer_than_scen_max_is_split_into_sessions(self):
        r = Run("split", lanes("maya"), config="DAYS=1 SCEN_MAX=2")
        open(r.path("lanes", "maya", "scenarios.md"), "w").write(plan_text({1: 5}))
        res = r.crowd("all")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        plan = r.read("lanes", "maya", "scenarios.md")
        self.assertIn("## Day 3 (day 1, part 3 of 3)", plan)
        self.assertEqual(r.read("lanes", "maya", "days").strip(), "3")
        for d, ids in ((1, ["S1", "S2"]), (2, ["S3", "S4"]), (3, ["S5"])):
            self.assertEqual(r.verdict("maya", d)["scenarios"]["ran"], ids)
        p3 = r.read("lanes", "maya", "round3", "prompt.md")
        self.assertIn("### S5", p3)
        self.assertNotIn("### S1 ", p3)
        self.assertTrue(os.path.exists(r.path("lanes", "maya", "scenarios.unsplit.md")))
        self.assertIn("| maya | owner | 3 |", r.read("state", "report-pack.md"))

    def test_a_saved_product_map_is_reused_only_for_the_same_source_commit(self):
        repo = os.path.join(WORK, "maprepo")
        shutil.rmtree(repo, ignore_errors=True)
        os.makedirs(repo)
        git = ["/usr/bin/git", "-C", repo, "-c", "user.email=t@t", "-c", "user.name=t"]
        subprocess.run(git[:3] + ["init", "-q"], check=True)
        open(os.path.join(repo, "a.txt"), "w").write("1")
        subprocess.run(git + ["add", "."], check=True)
        subprocess.run(git + ["commit", "-qm", "one"], check=True)
        head = subprocess.run(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        saved = os.path.join(WORK, f"saved-map-src-{head[:7]}.md")
        open(saved, "w").write("# saved map\n" + "y" * 600)
        r = Run("mapreuse", lanes("maya"), config=f'DAYS=1 SOURCE_PATHS="{repo}" PRODUCT_MAP_FROM="{saved}"')
        os.remove(r.path("product-map.md"))
        self.assertEqual(r.crowd("plan").returncode, 0)
        self.assertEqual(r.read("product-map.md"), open(saved).read())
        self.assertEqual([c for c in r.calls(kind="step") if c["step"] == "product-map"], [])
        self.assertIn("source commits match", r.read("logs", "crowd.log"))
        open(os.path.join(repo, "a.txt"), "w").write("2")
        subprocess.run(git + ["commit", "-qam", "two"], check=True)
        r2 = Run("mapstale", lanes("maya"), config=f'DAYS=1 SOURCE_PATHS="{repo}" PRODUCT_MAP_FROM="{saved}"')
        os.remove(r2.path("product-map.md"))
        self.assertEqual(r2.crowd("plan").returncode, 0)
        self.assertEqual(len([c for c in r2.calls(kind="step") if c["step"] == "product-map"]), 1)
        self.assertIn("written from other commits", r2.read("logs", "crowd.log"))
        new_head = subprocess.run(git[:3] + ["rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(json.load(open(r2.path("product-map.md.src")))["commits"], {repo: new_head})


class Makeup(unittest.TestCase):
    def test_makeup_keeps_only_unfinished_scenarios_and_drops_ones_blocked_by_filed_bugs(self):
        r = Run("makeup-old", lanes("maya", "leo", "done", deps={"leo": ["maya", "done"]}), config="SCEN_MAX=2")
        for lane, d, statuses in (
                ("maya", 1, {"S1": ("pass", ""), "S2": ("blocked", "refund fails, see #7"), "S3": ("blocked", "no ticket to scan")}),
                ("maya", 2, {"S4": ("fail", ""), "S5": ("not_run", "ran out of time")}),
                ("leo", 1, {"S1": ("blocked", "Export drops the last row of the CSV again"), "S2": ("pass", ""), "S3": ("pass", "")}),
                ("leo", 2, {f"S{i}": ("pass", "") for i in (4, 5, 6)}),
                ("done", 1, {f"S{i}": ("pass", "") for i in (1, 2, 3)}), ("done", 2, {f"S{i}": ("pass", "") for i in (4, 5, 6)})):
            os.makedirs(r.path("lanes", lane, f"round{d}"), exist_ok=True)
            json.dump({"scenarios": [{"id": k, "status": v[0], "notes": v[1]} for k, v in statuses.items()]},
                      open(r.path("lanes", lane, f"round{d}", "report.json"), "w"))
        open(r.path("lanes", "maya", "memory.md"), "w").write("Day 1: my password is CrowdQA!2026\n")
        open(r.path("filed.txt"), "w").write("#7 Refund approval fails\n#9 Export drops the last row of the CSV\n")
        new = os.path.join(WORK, "makeup-new")
        shutil.rmtree(new, ignore_errors=True)
        res = r.crowd("makeup", new)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        maya = open(os.path.join(new, "lanes", "maya", "scenarios.md")).read()
        self.assertIn("### S1 — step 3  [failure]  (from day 1 S3)", maya)
        self.assertIn("_Earlier: blocked. no ticket to scan_", maya)
        self.assertIn("(from day 2 S5)", maya)
        self.assertIn("(from day 2 S6)", maya, "planned but never reported")
        self.assertIn("_Earlier: never reported._", maya)
        self.assertNotIn("from day 1 S2", maya, "blocked by filed #7")
        self.assertIn("## Day 2\n", maya, "three scenarios at SCEN_MAX=2 make two days")
        self.assertFalse(os.path.exists(os.path.join(new, "lanes", "leo")), "leo's only gap is a filed bug")
        self.assertFalse(os.path.exists(os.path.join(new, "lanes", "done")))
        self.assertEqual(json.load(open(os.path.join(new, "lanes.json"))), [{"id": "maya", "role": "owner", "surface": "web", "depends_on": []}])
        self.assertEqual(open(os.path.join(new, "filed.txt")).read(), r.read("filed.txt"))
        self.assertIn("my password", open(os.path.join(new, "lanes", "maya", "memory.md")).read())
        self.assertIn("**Name**: Maya", open(os.path.join(new, "lanes", "maya", "card.md")).read())
        summary = json.load(open(os.path.join(new, "state", "makeup.json")))
        self.assertEqual((summary["blocked"], summary["not_run"], summary["never reported"], summary["days"]), (1, 1, 1, 2))
        self.assertEqual(len(summary["left_out_blocked_by_filed_bugs"]), 2)
        conf = subprocess.run(["bash", "-c", f'. "{new}/config.env"; echo "$RUN_DIR $DAYS"'], capture_output=True, text=True).stdout
        self.assertEqual(conf.strip(), f"{new} 2")
        env = {**r.env, "FAKE_LOG": os.path.join(new, "fake.jsonl")}
        shutil.copy(r.path("fake-tester.json"), os.path.join(new, "fake-tester.json"))
        res = subprocess.run(["python3", os.path.join(new, "scripts", "crowd.py"), new, "run"], env=env,
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(json.load(open(os.path.join(new, "lanes", "maya", "round1", "verdict.json")))["scenarios"]["ran"], ["S1", "S2"])

    def test_makeup_gives_each_character_its_own_day_count_and_keeps_the_tools(self):
        """2026-10-19: DAYS=2 for the run made plan_ok re-plan every one-day character in full, and the run dir had
        no tools/ (device shims, act-as helpers), so every remote device was 'gone'."""
        r = Run("makeup-days", lanes("maya", "ann"), config="SCEN_MAX=2")
        for lane, d, statuses in (
                ("maya", 1, {"S1": ("not_run", ""), "S2": ("not_run", ""), "S3": ("not_run", "")}),
                ("maya", 2, {f"S{i}": ("pass", "") for i in (4, 5, 6)}),
                ("ann", 1, {"S1": ("blocked", "simulator gone"), "S2": ("pass", ""), "S3": ("pass", "")}),
                ("ann", 2, {f"S{i}": ("pass", "") for i in (4, 5, 6)})):
            os.makedirs(r.path("lanes", lane, f"round{d}"), exist_ok=True)
            json.dump({"scenarios": [{"id": k, "status": v[0], "notes": v[1]} for k, v in statuses.items()]},
                      open(r.path("lanes", lane, f"round{d}", "report.json"), "w"))
        os.makedirs(r.path("tools", "bin"), exist_ok=True)
        open(r.path("tools", "bin", "xcrun"), "w").write("#!/bin/sh\n")
        new = os.path.join(WORK, "makeup-days-new")
        shutil.rmtree(new, ignore_errors=True)
        res = r.crowd("makeup", new)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(os.path.exists(os.path.join(new, "tools", "bin", "xcrun")))
        self.assertEqual(open(os.path.join(new, "lanes", "maya", "days")).read().strip(), "2")
        self.assertEqual(open(os.path.join(new, "lanes", "ann", "days")).read().strip(), "1")
        ann = os.path.join(new, "lanes", "ann", "scenarios.md")
        before = open(ann).read()
        env = {**r.env, "FAKE_LOG": os.path.join(new, "fake.jsonl")}
        shutil.copy(r.path("fake-tester.json"), os.path.join(new, "fake-tester.json"))
        res = subprocess.run(["python3", os.path.join(new, "scripts", "crowd.py"), new, "run"], env=env,
                             capture_output=True, text=True, timeout=180)
        self.assertEqual(res.returncode, 0, res.stdout)
        self.assertEqual(open(ann).read(), before, "a one-day character is planned, not re-planned")
        self.assertTrue(os.path.exists(os.path.join(new, "lanes", "ann", "round1", "verdict.json")))
        self.assertFalse(os.path.exists(os.path.join(new, "lanes", "ann", "round2")))
        self.assertTrue(os.path.exists(os.path.join(new, "lanes", "maya", "round2", "verdict.json")))


class SquareView(unittest.TestCase):
    """square-view.py: state.json for the 3D world, index.html (world) and square.html (2D) from one pass."""

    SHAPE = {"run": {"name", "date", "days", "stage", "updated"},
             "characters": {"id", "name", "role", "surface", "status", "day", "done", "total", "bugs", "color", "shot"},
             "posts": {"id", "author", "kind", "text", "ts", "parent", "shot"},
             "bugs": {"key", "title", "severity", "reporter", "confirmers", "state", "url", "area"},
             "jobs": {"id", "role", "area", "status", "runner", "model", "thread", "persona", "started"},
             "bouts": {"id", "kind", "a", "b", "subject", "rounds", "winner", "scores", "status"},
             "prs": {"area", "url", "number", "status", "bugs", "recheck"}}

    def render(self, r):
        res = subprocess.run(["python3", os.path.join(SKILL, "scripts", "square-view.py"), r.dir], capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.load(open(r.path("square", "state.json")))

    def assertShape(self, st):
        self.assertEqual(set(st), set(self.SHAPE))
        self.assertTrue(self.SHAPE["run"] <= set(st["run"]))
        for k, keys in self.SHAPE.items():
            for item in ([] if k == "run" else st[k]):
                self.assertTrue(keys <= set(item), f"{k}: missing {keys - set(item)}")

    def test_state_json_from_every_pipeline_source(self):
        r = Run("squareview", lanes("maya", "leo", "ana", "bo", "cy"), config="DAYS=2")
        S = r.path("state")

        def put(rel, obj):
            os.makedirs(os.path.dirname(r.path(*rel.split("/"))), exist_ok=True)
            with open(r.path(*rel.split("/")), "w") as f:
                f.write(obj if isinstance(obj, str) else json.dumps(obj))
        for lane, st in (("maya", "running"), ("bo", "waiting-stack"), ("cy", "waiting")):
            put(f"lanes/{lane}/round1/report.json", {"scenarios": [{"id": "S1", "status": "pass"}]})
            put(f"lanes/{lane}/state.json", {"status": st, "surface": "web"})
        put("lanes/maya/round1/shots/S1.png", "png")
        for lane in ("leo", "ana"):
            put(f"lanes/{lane}/round1/report.json", {"scenarios": [{"id": "S1", "status": "pass"}]})
            put(f"lanes/{lane}/round1/DONE", "exit=0 engine=claude\n")
            put(f"lanes/{lane}/state.json", {"status": "done"})
        put("lanes/ana/round1/verdict.json", {"verified_findings": [{"title": "Pay button double charges"}]})
        with open(r.path("square", "feed.jsonl"), "w") as f:
            for e in ({"id": "P1", "author": "maya", "kind": "post", "text": "Checkout hangs", "ts": "2026-10-18T10:00:00",
                       "shot": r.path("lanes", "maya", "round1", "shots", "S1.png")},
                      {"id": "P2", "author": "leo", "kind": "me-too", "ref": "P1", "text": "Same", "ts": "2026-10-18T10:05:00"},
                      {"id": "P3", "author": "ana", "kind": "cant-repro", "ref": "P1", "text": "Works for me", "ts": "2026-10-18T10:06:00"}):
                f.write(json.dumps(e) + "\n")
            f.write("not json\n")
        put("state/issues.json", [{"key": "k1", "title": "Pay button double charges", "severity": "high", "reporter": "ana",
                                   "confirmers": [{"id": "maya"}, {"id": "S3"}]},
                                  {"key": "k2", "title": "Avatar upload spins", "severity": "low", "reporter": "maya", "confirmers": []},
                                  {"key": "k3", "title": "Stack fault", "severity": "low", "reporter": "leo", "confirmers": []}])
        put("state/filed.json", {"k1": {"url": "https://github.com/o/a/issues/1", "title": "Pay button double charges"}})
        put("state/file-plan.json", [{"key": "k1", "action": "create"}, {"key": "k2", "action": "comment", "target": "#4"},
                                     {"key": "k3", "action": "skip", "reason": "stack"}])
        put("state/areas.json", {"payments": ["k1"], "profile": ["k2"]})
        put("state/jobs.json", {
            "fix-payments": {"role": "fix", "area": "payments", "status": "done", "runner": "t3", "model": "opus", "thread": "thr_1"},
            "review-payments": {"role": "review", "area": "payments", "status": "done", "runner": "cli", "model": "codex"},
            "recheck-ana": {"role": "recheck", "area": "payments", "status": "running", "runner": "subagent", "persona": "ana"},
            "recheck-cy": {"role": "recheck", "area": "profile", "status": "done", "persona": "cy", "result": {"verdict": "PASS"}}})
        put("state/bouts/b1.json", {"id": "b1", "kind": "disagree", "a": "ana", "b": "maya", "subject": "P3",
                                    "rounds": [{"by": "ana", "text": "Cannot reproduce", "severity": "medium"}], "winner": "maya",
                                    "scores": {"ana": 4, "maya": 7}, "status": "decided"})
        put("state/bouts/broken.json", "[1, 2]")
        put("state/prs.json", {"payments": {"url": "https://github.com/o/a/pull/9", "number": 9, "status": "draft", "bugs": ["k1"],
                                            "recheck": {"pass": 1, "partial": 0, "fail": 0}},
                               "profile": {"url": "", "number": "", "status": "open", "bugs": ["k2"]}})
        st = self.render(r)
        self.assertShape(st)
        self.assertEqual(st["run"]["stage"], "recheck", "a recheck job is running")
        self.assertEqual(st["run"]["days"], 2)
        status = {c["id"]: c["status"] for c in st["characters"]}
        self.assertEqual(status, {"maya": "testing", "leo": "judged", "ana": "rechecking", "bo": "blocked", "cy": "waiting"})
        self.assertEqual(len({c["color"] for c in st["characters"]}), 5, "every character gets its own colour")
        maya = [c for c in st["characters"] if c["id"] == "maya"][0]
        self.assertEqual(maya["shot"], "../lanes/maya/round1/shots/S1.png")
        self.assertEqual([(p["kind"], p["parent"]) for p in st["posts"]], [("post", ""), ("metoo", "P1"), ("cantrepro", "P1")])
        bugs = {b["key"]: b for b in st["bugs"]}
        self.assertEqual(bugs["k1"]["state"], "in-pr")
        self.assertEqual(bugs["k1"]["url"], "https://github.com/o/a/issues/1")
        self.assertEqual(bugs["k1"]["area"], "payments")
        self.assertEqual(bugs["k1"]["confirmers"], ["maya"], "scenario ids are not characters")
        self.assertEqual(bugs["k3"]["state"], "skipped")
        self.assertEqual({b["id"] for b in st["bouts"]}, {"b1"}, "a malformed bout file is skipped")
        self.assertEqual(st["bouts"][0]["rounds"][0]["by"], "ana")
        prs = {p["area"]: p for p in st["prs"]}
        self.assertEqual(prs["payments"]["recheck"], {"pass": 1, "partial": 0, "fail": 0})
        self.assertEqual(prs["profile"]["recheck"], {"pass": 1, "partial": 0, "fail": 0}, "counted from finished recheck jobs")
        index = r.read("square", "index.html")
        self.assertIn('"three"', index)
        self.assertIn('id="state"', index, "state is inlined for file://")
        self.assertNotIn('src="world.js"', index, "world.js is inlined: browsers refuse module files from file://")
        self.assertIn("square.html", index)
        for f in ("square.html", "state.js", "square-data.js"):
            self.assertTrue(os.path.exists(r.path("square", f)), f)
        self.assertIn('"Checkout hangs"', r.read("square", "square-data.js"), "the 2D page polls this in place")

    def test_a_fresh_run_gives_empty_lists_not_a_crash(self):
        r = Run("squareview-empty", lanes("maya"), plans=False)
        os.remove(r.path("square", "feed.jsonl"))
        st = self.render(r)
        self.assertShape(st)
        self.assertEqual(st["run"]["stage"], "plan")
        self.assertEqual([st[k] for k in ("posts", "bugs", "jobs", "bouts", "prs")], [[], [], [], [], []])
        self.assertEqual(st["characters"][0]["status"], "waiting")
        self.assertTrue(os.path.exists(r.path("square", "square.html")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
