# What crowd-qa takes from MiroFish, and what it leaves

MiroFish (`github.com/666ghj/MiroFish`, reference clone in `~/Developer/ref/MiroFish`, read only) is a
prediction engine. It takes seed material, builds a knowledge graph of the entities in it (Zep GraphRAG),
generates a social-media persona per entity, runs those personas on a simulated Twitter and Reddit
(CAMEL-AI OASIS) for N rounds, writes each action back into the graph as memory, and has a report agent
plan and write a forecast from the result. You can then interview any agent.

## Taken
- **Persona depth** (`backend/app/services/oasis_profile_generator.py`, `_build_individual_persona_prompt`).
  Each persona carries basic facts (age, job, place), background, personality, behaviour on the platform,
  stance, quirks and catchphrases, and a personal memory of the event. crowd-qa's card keeps the same
  parts, but aimed at an app: phone comfort, patience, language and script, accessibility, device, what
  they want on each day, and the voice they complain in.
- **Memory between rounds** (`zep_graph_memory_updater.py` feeds actions back). crowd-qa uses a plain diary,
  `memory.md`, written by the character at the end of each day and put in the next day's prompt. It holds
  facts (store name, password, orders) and feelings (what made them cross).
- **A shared platform where agents react to each other.** MiroFish's agents post, reply and like. On the
  town square characters post what broke, then others try it and reply me-too, cant-repro, disagree or
  tip. That turns one tester's bug into a reproduced bug across devices, which is the useful part.
- **Agents acting on each other.** In MiroFish, opinions spread through follows and replies. In crowd-qa,
  customers act on what owners set up, and owners deal with the results the next day.
- **Rounds** become days. **The report agent** becomes `LAUNCH_REPORT.md`, but it may only cite evidence:
  filed issues, verdicts, diaries and square posts.

## Left out
- The knowledge graph and Zep. An app's "world" is its code and docs, and the conductor reads them directly.
- Prediction. Nothing here forecasts behaviour. Characters act, and only what the screenshots show counts.
- Thousands of agents. Each character drives a real simulator through a max-effort tester, so a crowd of
  about 20 is already hours of work and a real RAM budget.
- Free-running social dynamics. The square is a tool for reproduction, capped at about 6 posts per character per day.
