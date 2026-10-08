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
    for n in ("claude", "codex", "gh", "git", "agent-browser"):
        p = os.path.join(BIN, n)
        if not os.path.lexists(p):
            os.symlink(os.path.join(HERE, "fakes", "fake_engine.py"), p)


class Run:
    def __init__(self, name, lanes, config="", tester=None, plans=True, cards=None):
        self.dir = os.path.join(WORK, name)
        shutil.rmtree(self.dir, ignore_errors=True)
        self.env = {**os.environ, "PATH": BIN + ":" + os.environ["PATH"], "FAKE_LOG": os.path.join(self.dir, "fake.jsonl")}
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
        open(cls.r.path("stack-faults.md"), "w").write("- Uploads fail on this stack: the fake storage has no bucket.\n")
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
        r = Run("file", lanes("maya", "leo"), config='DAYS=1 FILE_CMD="" TRACKERS="app=owner/app"', tester={
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
        self.assertEqual([c["step"] for c in new if c["kind"] == "step"], ["report"])

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


class Watchers(unittest.TestCase):
    def test_api_watch_once_and_capacity_swap_cap(self):
        r = Run("watch", lanes("maya"), config='HEALTH_CHECKS="ok|true; bad|false" SWAP_MAX_MB=-1')
        res = r.sh("bash", r.path("scripts", "api-watch.sh"), r.dir, "once")
        self.assertEqual(res.returncode, 1)
        self.assertIn("failing: bad", res.stdout)
        res = r.sh("bash", r.path("scripts", "capacity.sh"), r.dir, "check")
        self.assertEqual(res.returncode, 1)
        self.assertIn("swap", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
