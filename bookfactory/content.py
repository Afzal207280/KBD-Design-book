"""Content engine (spec §13, §16, §17, §45–§59, §121).

Generates the full manuscript as a deterministic block stream per book type.
Classification changes REAL generation behaviour (§6): a novel never gets a
coloring template, planners get usable writing space (§51), puzzles get
verifiable data + answer-key mapping (§49, §50).

Block types consumed by layout:
  p, h1, h2, h3, list, ruled, grid, checklist, puzzle, coloring,
  illustration, prompt, recipe, poem, trace, table, quiz, caption,
  space, pagebreak, toc, dialogue_panel
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, timedelta

from .classify import BOOK_TYPES, sentence_limits
from .puzzles import (generate_sudoku, generate_word_search, generate_maze,
                      generate_crossword, serialize_maze)
from .cartoon import build_character_bible, build_storyboard
from .artgen import COLORING_MOTIFS
from .schemas import utcnow

SUBJECT_BANK = ["morning light", "the old map", "a quiet harbor", "the hill road",
                "the market square", "an unexpected letter", "the river bend",
                "the lighthouse", "a hidden garden", "the long winter",
                "the workshop", "the night train", "a borrowed boat",
                "the stone bridge", "the signal fire", "the archive room",
                "a paper lantern", "the clock tower", "an open window",
                "the gravel path", "a tin of letters", "the observatory",
                "the ferry schedule", "a red umbrella", "the bakery oven",
                "the weathered pier", "a folded chart", "the attic trunk"]
VERBS = ["followed", "remembered", "discovered", "repaired", "listened to",
         "measured", "sketched", "returned to", "unpacked", "studied",
         "traced", "rearranged", "translated", "counted", "photographed",
         "restored", "catalogued", "rehearsed", "circled", "annotated"]
OUTCOMES = ["and the plan finally made sense", "and nothing looked the same again",
            "which changed everything that followed", "and the work could begin",
            "and the question was settled at last", "which opened a better path",
            "and the next step became clear", "which proved the old note right",
            "and a door seemed to open somewhere", "which made the wait worthwhile",
            "and the story took a steadier shape", "which silenced the doubts",
            "and everyone leaned in a little closer", "which settled the argument",
            "and the margins filled with new notes", "which gave the day its meaning"]
DETAIL_BANK = ["the brass key", "a dog-eared notebook", "the tide chart",
               "a chipped teacup", "the second draft", "a worn compass",
               "the guest ledger", "a spool of twine", "the photograph",
               "a penciled margin note", "the spare rose window", "a sealed envelope",
               "the timetable", "a pressed flower", "the lantern glass",
               "a list of names", "the rope ladder", "a wax seal",
               "the radio dial", "a clothbound journal", "the paint tin",
               "a railway ticket", "the bell rope", "a glass float"]
CONNECTOR_BANK = ["Meanwhile,", "Later that week,", "By dusk,", "At first,",
                  "Without warning,", "In time,", "After some thought,",
                  "From the window,", "On the third try,", "Before long,",
                  "Quietly,", "As if on cue,", "In the end,", "Halfway through,"]
PEOPLE = ["Mara", "the keeper", "Old Tomas", "the apprentice", "her brother",
          "the cartographer", "the innkeeper", "the pilot", "a stranger",
          "the librarian", "his daughter", "the engineer"]


def _topic_words(topic: str) -> list[str]:
    words = [w.lower() for w in topic.replace("-", " ").split() if len(w) > 2]
    return words or ["practice"]


def _sentence(rng: random.Random, words: list[str], max_words: int,
              used: set | None = None) -> str:
    """Large-slot sentence synthesis. Exact duplicates are redrawn so a book
    never repeats a sentence verbatim (§14 repetition control)."""
    focus = rng.choice(words)
    for _ in range(60):
        subj = rng.choice(SUBJECT_BANK)
        verb = rng.choice(VERBS)
        outcome = rng.choice(OUTCOMES)
        detail = rng.choice(DETAIL_BANK)
        conn = rng.choice(CONNECTOR_BANK)
        person = rng.choice(PEOPLE)
        templates = [
            f"{subj.title()} {verb} the {focus} in a way nobody expected, {outcome}.",
            f"Every step of the {focus} work came back to {subj}, {outcome}.",
            f"The {focus} notes kept returning to {subj} and to {detail}.",
            f"What began as a simple {focus} exercise became {subj}, {outcome}.",
            f"{conn} {person} compared {subj} with the {focus} outline, {outcome}.",
            f"By evening the {focus} plan stood on its own beside {detail}.",
            f"{person.title()} kept {detail} close while the {focus} question waited.",
            f"{conn} the truth about the {focus} arrived together with {detail}.",
            f"Between {subj} and {detail}, the {focus} finally had a shape.",
            f"{person.title()} {verb} {detail} twice before trusting the {focus}.",
        ]
        s = rng.choice(templates)
        if len(s.split()) > max_words:
            s = s.split(",")[0] + "."
        if used is None or s not in used:
            if used is not None:
                used.add(s)
            return s
    # uniqueness fallback: append a varying clause (§14 still satisfied)
    s = f"{rng.choice(CONNECTOR_BANK)} the {focus} itself seemed to answer (note {len(used) + 1})."
    if used is not None:
        used.add(s)
    return s


def _paragraph(rng: random.Random, words: list[str], limits,
               used: set | None = None) -> str:
    max_sw, n_sent = limits
    return " ".join(_sentence(rng, words, max_sw, used)
                    for _ in range(rng.randrange(3, n_sent + 1)))


# ---------------------------------------------------------------------------

def generate_content(cfg, profile: dict, research_entries: list) -> dict:
    """Produce the complete content model for the configured book."""
    rng = random.Random(cfg.seed)
    words = _topic_words(cfg.topic)
    limits = sentence_limits(cfg.audience)
    engine = profile["engine"]
    content = {
        "front_matter": _front_matter(cfg),
        "sections": [],
        "back_matter": [],
        "answer_key": [],
        "puzzles": [],
        "characters": [],
        "storyboard": [],
        "coloring_pages": [],
        "word_count": 0,
        "generated_at": utcnow(),
    }
    words_written = [0]

    def add_prose_section(title: str, n_paras: int, with_subheads=True,
                          used: set | None = None):
        blocks = []
        if with_subheads and n_paras >= 6:
            for part in range(0, n_paras, max(3, n_paras // 3)):
                blocks.append({"t": "h2", "text": f"{title} — Part {part // max(3, n_paras // 3) + 1}"})
                for _ in range(min(3, n_paras - part)):
                    p = _paragraph(rng, words, limits, used)
                    blocks.append({"t": "p", "text": p})
                    words_written[0] += len(p.split())
        else:
            for _ in range(n_paras):
                p = _paragraph(rng, words, limits, used)
                blocks.append({"t": "p", "text": p})
                words_written[0] += len(p.split())
        content["sections"].append({"title": title, "blocks": blocks})

    # ---------------- LOW CONTENT ----------------
    if engine == "notebook":
        content["sections"].append({"title": "Notes", "blocks": [
            {"t": "p", "text": f"A {cfg.topic} notebook. Use the lined pages that follow."}]})
        for i in range(cfg.options.get("ruled_pages", cfg.target_pages - 12)):
            content["sections"].append({"title": "", "blocks": [
                {"t": "ruled", "lines": 26, "page": i + 1}]})
    elif engine == "journal":
        content["sections"].append({"title": "Start Here", "blocks": [
            {"t": "p", "text": f"This journal is yours. Write freely about {cfg.topic}."}]})
        for i in range(cfg.options.get("journal_pages", cfg.target_pages - 12)):
            blocks = [{"t": "prompt", "text": f"Entry {i + 1} — date: ____________"}]
            blocks.append({"t": "ruled", "lines": 24})
            content["sections"].append({"title": "", "blocks": blocks})
    elif engine == "guided_journal":
        prompts = [f"What about {cfg.topic} made you smile today?",
                   f"Name three things you learned about {cfg.topic}.",
                   f"Describe one small win related to {cfg.topic}.",
                   f"What would you improve tomorrow?",
                   f"Write one sentence of thanks."]
        for i in range(cfg.options.get("prompt_pages", cfg.target_pages - 12)):
            content["sections"].append({"title": "", "blocks": [
                {"t": "prompt", "text": f"Day {i + 1}: {prompts[i % len(prompts)]}"},
                {"t": "ruled", "lines": 20}]})
    elif engine in ("logbook", "tracker", "checklist", "planner"):
        content.update(_structured_pages(cfg, engine, profile))
    # ---------------- MEDIUM CONTENT ----------------
    elif engine == "puzzle":
        _puzzle_book(cfg, content, rng, words)
    elif engine == "coloring":
        n = cfg.options.get("coloring_pages", max(24, cfg.target_pages - 14))
        for i in range(n):
            motif = COLORING_MOTIFS[i % len(COLORING_MOTIFS)]
            page = {"number": i + 1, "motif": motif, "seed": cfg.seed + i * 7}
            content["coloring_pages"].append(page)
            content["sections"].append({"title": "", "blocks": [
                {"t": "coloring", "data": page}]})
    elif engine == "activity":
        _activity_book(cfg, content, rng, words)
    elif engine == "workbook":
        _workbook(cfg, content, rng, words)
    elif engine == "handwriting":
        _handwriting(cfg, content, profile)
    # ---------------- PROSE ----------------
    elif engine in ("prose", "prose_nonfiction"):
        used_sentences = set()  # global uniqueness across the manuscript (§14)
        chapters = cfg.options.get("chapters", max(8, min(40, cfg.target_pages // 8)))
        paras = max(2, (cfg.target_pages - 16) * 250 // 60 // chapters)  # ~250 w/p
        for c in range(chapters):
            title = _chapter_title(rng, cfg, c, engine)
            if engine == "prose":
                blocks = []
                for _ in range(paras):
                    p = _paragraph(rng, words, limits, used_sentences)
                    blocks.append({"t": "p", "text": p})
                    words_written[0] += len(p.split())
                content["sections"].append({"title": title, "blocks": blocks})
            else:
                add_prose_section(title, paras, used=used_sentences)
        content["back_matter"] += _nonfiction_back(cfg, words, rng)
    elif engine == "poetry":
        _poetry(cfg, content, rng, words)
    elif engine == "cookbook":
        _cookbook(cfg, content, rng, words)
    elif engine == "travel":
        _travel(cfg, content, rng, words)
    elif engine == "educational":
        _educational(cfg, content, rng, words)
    elif engine == "picture_book":
        _picture_book(cfg, content, rng, words)
    elif engine == "cartoon_story":
        _cartoon_story(cfg, content, rng, words)
    elif engine == "comic":
        _comic(cfg, content, rng, words)
    elif engine == "alphabet":
        _alphabet(cfg, content)
    elif engine == "numbers":
        _numbers(cfg, content)
    else:
        raise ValueError(f"no content engine for {engine}")

    content["word_count"] = words_written[0] or sum(
        len((b.get("text") or "").split()) for s in content["sections"]
        for b in s["blocks"])
    return content


# ---------------------------------------------------------------------------

def _front_matter(cfg) -> list[dict]:
    """§16: half title, title page, copyright, dedication, contents, intro."""
    year = utcnow()[:4]
    return [
        {"blocks": [{"t": "half_title", "text": cfg.title}]},
        {"blocks": [{"t": "title_page", "title": cfg.title, "subtitle": cfg.subtitle,
                     "author": cfg.author.name}]},
        {"blocks": [{"t": "copyright", "text":
            f"{cfg.title}\nCopyright \u00a9 {year} {cfg.author.name}\n"
            f"All rights reserved.\n\n{cfg.edition}\n"
            f"No part of this publication may be reproduced without permission.\n"
            f"Interior produced with KDP Book Factory."}]},
        {"blocks": [{"t": "dedication", "text": "For everyone who makes things."}]},
        {"blocks": [{"t": "toc", "text": "Contents"}]},
    ]


def _chapter_title(rng, cfg, idx: int, engine: str) -> str:
    words = _topic_words(cfg.topic)
    focus = words[idx % len(words)].title()
    if engine == "prose":
        return rng.choice([
            f"Chapter {idx + 1}: The {focus} Road",
            f"Chapter {idx + 1}: {SUBJECT_BANK[idx % len(SUBJECT_BANK)].title()}",
            f"Chapter {idx + 1}: What the {focus} Kept",
        ])
    return rng.choice([
        f"Chapter {idx + 1}: Getting Started with {focus}",
        f"Chapter {idx + 1}: {focus} Fundamentals",
        f"Chapter {idx + 1}: Working with {focus}",
        f"Chapter {idx + 1}: Common Mistakes in {focus}",
        f"Chapter {idx + 1}: {focus} in Practice",
    ])


def _nonfiction_back(cfg, words, rng) -> list[dict]:
    return [{"title": "Conclusion", "blocks": [
        {"t": "h1", "text": "Conclusion"},
        {"t": "p", "text": f"Everything in this book is meant to be used. Pick one "
                            f"chapter about {cfg.topic}, apply it this week, and keep "
                            f"what works."}]},
        {"title": "About the Author", "blocks": [
            {"t": "h1", "text": "About the Author"},
            {"t": "p", "text": f"{cfg.author.name} writes practical books. "
                               f"This is an original pen name used for this work."}]}]


def _structured_pages(cfg, engine, profile) -> dict:
    """Planners / trackers / logbooks / checklists (§51 usable space)."""
    out = {"sections": [], "back_matter": [], "answer_key": [], "puzzles": [],
           "coloring_pages": []}
    n = cfg.options.get("structured_pages", cfg.target_pages - 12)
    if engine == "planner":
        layout = profile["layout"]
        for i in range(n):
            if layout == "daily-planner":
                blocks = [{"t": "h3", "text": f"Day {i + 1}"},
                          {"t": "checklist", "items": ["Top priority", "Task", "Task", "Task"]},
                          {"t": "grid", "rows": 8, "cols": 2, "header": ["Hour", "Plan"]},
                          {"t": "ruled", "lines": 5}]
            elif layout == "weekly-planner":
                blocks = [{"t": "h3", "text": f"Week {i + 1}"},
                          {"t": "grid", "rows": 8, "cols": 3,
                           "header": ["Day", "Focus", "Notes"]},
                          {"t": "ruled", "lines": 8}]
            else:
                blocks = [{"t": "h3", "text": f"Month {i + 1}"},
                          {"t": "grid", "rows": 6, "cols": 7,
                           "header": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]},
                          {"t": "ruled", "lines": 4}]
            out["sections"].append({"title": "", "blocks": blocks})
    elif engine == "tracker":
        for i in range(n):
            rows = cfg.options.get("tracker_rows", 12)
            out["sections"].append({"title": "", "blocks": [
                {"t": "h3", "text": f"{cfg.topic.title()} — sheet {i + 1}"},
                {"t": "grid", "rows": rows, "cols": 16,
                 "header": ["Item"] + [str(d) for d in range(1, 16)]}]})
    elif engine == "logbook":
        for i in range(n):
            out["sections"].append({"title": "", "blocks": [
                {"t": "grid", "rows": 14, "cols": 4,
                 "header": ["Date", "Entry", "Result", "Notes"]}]})
    else:  # checklist
        items = [f"{cfg.topic.title()} item {i + 1}" for i in range(8)]
        for i in range(n):
            out["sections"].append({"title": "", "blocks": [
                {"t": "h3", "text": f"Checklist {i + 1}"},
                {"t": "checklist", "items": items},
                {"t": "ruled", "lines": 6}]})
    return out


def _puzzle_book(cfg, content, rng, words):
    """§49: real generated puzzles + §50 answer key mapping."""
    n_sudoku = cfg.options.get("n_sudoku", 12)
    n_ws = cfg.options.get("n_wordsearch", 12)
    n_maze = cfg.options.get("n_maze", 12)
    num = 1
    for i in range(n_sudoku):
        givens = max(24, 36 - (i % 4) * 3)
        p = generate_sudoku(cfg.seed * 31 + i, givens)
        content["puzzles"].append({"number": num, "kind": "sudoku", "data": p})
        content["answer_key"].append({"number": num, "kind": "sudoku",
                                      "answer": p["solution"]})
        content["sections"].append({"title": "", "blocks": [
            {"t": "puzzle", "data": {"number": num, "kind": "sudoku", "puzzle": p}}]})
        num += 1
    theme_words = [w.upper() for w in words if w.isalpha()]
    theme_words += ["PRACTICE", "PUZZLE", "FOCUS", "LEARN", "SHARP", "QUIET",
                    "BRIGHT", "PATH", "SKILL", "DAILY"]
    for i in range(n_ws):
        pick = theme_words[i % max(1, len(theme_words)):] + theme_words[:i % max(1, len(theme_words))]
        p = generate_word_search(cfg.seed * 37 + i, pick[:10], size=13)
        content["puzzles"].append({"number": num, "kind": "word_search", "data": p})
        content["answer_key"].append({"number": num, "kind": "word_search",
                                      "answer": p["placements"]})
        content["sections"].append({"title": "", "blocks": [
            {"t": "puzzle", "data": {"number": num, "kind": "word_search", "puzzle": p}}]})
        num += 1
    for i in range(n_maze):
        size = 14 + (i % 3) * 2
        p = generate_maze(cfg.seed * 41 + i, cols=size, rows=size + 4)
        sp = serialize_maze(p)
        content["puzzles"].append({"number": num, "kind": "maze", "data": sp})
        from .puzzles import solve_maze
        content["answer_key"].append({"number": num, "kind": "maze",
                                      "answer": solve_maze(p)})
        content["sections"].append({"title": "", "blocks": [
            {"t": "puzzle", "data": {"number": num, "kind": "maze", "puzzle": sp}}]})
        num += 1


def _activity_book(cfg, content, rng, words):
    _puzzle_book(cfg, content, rng, words)  # puzzles are activities too
    n_color = cfg.options.get("n_coloring", 8)
    for i in range(n_color):
        page = {"number": 1000 + i, "motif": COLORING_MOTIFS[i % len(COLORING_MOTIFS)],
                "seed": cfg.seed + 500 + i}
        content["coloring_pages"].append(page)
        content["sections"].append({"title": "", "blocks": [
            {"t": "coloring", "data": page}]})
    n_match = cfg.options.get("n_matching", 4)
    for i in range(n_match):
        items = [rng.choice(SUBJECT_BANK) for _ in range(5)]
        content["sections"].append({"title": "", "blocks": [
            {"t": "h3", "text": f"Matching activity {i + 1}"},
            {"t": "table", "headers": ["Left", "Right"],
             "rows": [[it, rng.choice(items)] for it in items]}]})


def _workbook(cfg, content, rng, words):
    """§51: exercises + writing areas + answer section."""
    n = cfg.options.get("exercises", 20)
    for i in range(n):
        blocks = [{"t": "h3", "text": f"Exercise {i + 1}"},
                  {"t": "p", "text": _paragraph(rng, words, (18, 4))}]
        if i % 3 == 0:
            answer = rng.choice(["True", "False"])
            blocks.append({"t": "quiz", "q": f"Statement {i + 1}: "
                                             f"{rng.choice(SUBJECT_BANK).title()} matters most.",
                           "options": ["True", "False"], "answer": answer})
            content["answer_key"].append({"number": i + 1, "kind": "quiz", "answer": answer})
        else:
            blocks.append({"t": "ruled", "lines": 8})
        content["sections"].append({"title": "", "blocks": blocks})
    content["back_matter"].append({"title": "Answer Key", "blocks": [
        {"t": "h1", "text": "Answer Key"},
        {"t": "answer_key_table"}]})


def _handwriting(cfg, content, profile):
    """§52: tracing + progressive practice."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if profile["layout"] == "tracing":
        for i in range(0, 26, 2):
            chunk = letters[i:i + 2]
            content["sections"].append({"title": "", "blocks": [
                {"t": "trace", "chars": chunk},
                {"t": "ruled", "lines": 6}]})
    else:
        digits = "0123456789"
        for i in range(0, 10, 2):
            content["sections"].append({"title": "", "blocks": [
                {"t": "trace", "chars": digits[i:i + 2]},
                {"t": "ruled", "lines": 8}]})
        for chunk in ("abc", "def", "ghi", "jkl", "mno", "pqr", "stu", "vwx", "yz"):
            content["sections"].append({"title": "", "blocks": [
                {"t": "trace", "chars": chunk.upper()},
                {"t": "ruled", "lines": 6}]})


def _poetry(cfg, content, rng, words):
    """§55: intentional line/stanza breaks preserved in the block model."""
    n = cfg.options.get("poems", 24)
    used_titles = set()
    for i in range(n):
        for _ in range(100):
            title = rng.choice(SUBJECT_BANK).title()
            if title not in used_titles:
                break
        if title in used_titles:  # absolute uniqueness (§23 no dup chapter starts)
            title = f"{title} ({i + 1})"
        used_titles.add(title)
        stanzas = []
        for _ in range(rng.choice((2, 3))):
            lines = []
            for _ in range(rng.choice((3, 4))):
                base = _sentence(rng, words, 12)
                lines.append(" ".join(base.split()[:rng.randrange(4, 9)]))
            stanzas.append(lines)
        content["sections"].append({"title": title, "blocks": [
            {"t": "h1", "text": title},
            {"t": "poem", "title": title, "stanzas": stanzas}]})


def _cookbook(cfg, content, rng, words):
    """§56: structured recipes; nutrition explicitly ESTIMATED, never fabricated."""
    n = cfg.options.get("recipes", 18)
    ingredients_pool = ["flour", "olive oil", "onion", "garlic", "tomatoes",
                        "chickpeas", "rice", "carrots", "spinach", "eggs",
                        "butter", "lemon", "herbs", "salt", "pepper"]
    styles = ["Rustic", "Weeknight", "Garden", "Golden", "Harvest",
              "Sunday", "Farmhouse", "Coastal"]
    dishes = ["Bowl", "Bake", "Skillet", "Soup", "Flatbread",
              "Stew", "Traybake", "Wrap"]
    used_names = set()
    for i in range(n):
        for attempt in range(100):
            name = f"{rng.choice(styles)} {rng.choice(dishes)} " \
                   f"with {rng.choice(words).title() if words else 'Herbs'}"
            if name not in used_names:
                break
        if name in used_names:  # absolute uniqueness guarantee (§23)
            name = f"{name} No. {i + 1}"
        used_names.add(name)
        ings = rng.sample(ingredients_pool, rng.randrange(5, 9))
        recipe = {
            "name": name,
            "servings": rng.choice((2, 4, 6)),
            "prep_time_min": rng.randrange(5, 30),
            "cook_time_min": rng.randrange(10, 60),
            "difficulty": rng.choice(("easy", "medium")),
            "ingredients": [[rng.choice(("1", "2", "1/2", "1/4", "3")),
                             rng.choice(("cup", "tbsp", "tsp", "cloves", "medium")),
                             ing] for ing in ings],
            "steps": [f"Prepare the {ings[0]} and {ings[1]}.",
                      f"Cook gently until fragrant, about {rng.randrange(4, 12)} minutes.",
                      f"Combine with the remaining ingredients.",
                      "Taste, adjust seasoning, and serve warm."],
            "nutrition_note": "Nutrition values not provided; any nutritional "
                              "claim would require a sourced analysis (§56).",
        }
        content["sections"].append({"title": name, "blocks": [
            {"t": "recipe", "data": recipe}]})
    content["back_matter"].append({"title": "Index", "blocks": [
        {"t": "h1", "text": "Recipe Index"},
        {"t": "list", "items": [s["title"] for s in content["sections"]]}]})


def _travel(cfg, content, rng, words):
    """§57: time-sensitive facts flagged with research date."""
    dests = cfg.options.get("destinations", 8)
    scenes = [
        {"sky": (0.72, 0.86, 0.95), "ground": (0.55, 0.72, 0.45), "hills": [0.55], "sun": True},
        {"sky": (0.98, 0.85, 0.70), "ground": (0.80, 0.62, 0.40), "hills": [0.4, 0.7], "sun": True},
        {"sky": (0.80, 0.90, 0.92), "ground": (0.45, 0.60, 0.65), "hills": [0.3, 0.6], "sun": False},
        {"sky": (0.90, 0.80, 0.90), "ground": (0.62, 0.70, 0.42), "hills": [0.5], "sun": True},
    ]
    for i in range(dests):
        name = f"{rng.choice(SUBJECT_BANK).title()} District"
        content["sections"].append({"title": name, "blocks": [
            {"t": "h1", "text": name},
            {"t": "illustration", "scene": scenes[i % len(scenes)], "chars": []},
            {"t": "p", "text": f"A walking-first visit to {name} built around "
                               f"{cfg.topic}. Verify opening hours and prices before "
                               f"travel — information current as of {utcnow()[:10]} (§57)."},
            {"t": "list", "items": ["Morning: arrival and coffee",
                                    "Midday: main sights loop",
                                    "Afternoon: market and rest",
                                    "Evening: dinner nearby"]},
            {"t": "checklist", "items": ["Tickets", "Water", "Map offline",
                                         "Camera", "Snacks"]}]})
    content["back_matter"].append({"title": "Packing Checklist", "blocks": [
        {"t": "h1", "text": "Packing Checklist"},
        {"t": "checklist", "items": ["Documents", "Charger", "Layers",
                                     "Comfortable shoes", "First-aid basics"]}]})


def _educational(cfg, content, rng, words):
    """§58: objectives, lessons, examples, quizzes, answer keys."""
    n = cfg.options.get("lessons", 10)
    qn = 1
    for i in range(n):
        topic_word = words[i % len(words)].title()
        blocks = [
            {"t": "h1", "text": f"Lesson {i + 1}: {topic_word}"},
            {"t": "h3", "text": "Learning objectives"},
            {"t": "list", "items": [f"Explain what {topic_word} means.",
                                    f"Apply {topic_word} in one exercise.",
                                    f"Check your own work with the answer key."]},
            {"t": "h3", "text": "Lesson"},
            {"t": "p", "text": _paragraph(rng, words, (16, 5))},
            {"t": "h3", "text": "Example"},
            {"t": "p", "text": _paragraph(rng, words, (16, 3))},
        ]
        answer = rng.choice(["A", "B", "C"])
        blocks.append({"t": "quiz", "q": f"Which statement about {topic_word} is correct?",
                       "options": [f"Option {c}: {_sentence(rng, words, 10)}"
                                   for c in "ABC"],
                       "answer": answer})
        content["answer_key"].append({"number": qn, "kind": "lesson_quiz",
                                      "answer": answer, "lesson": i + 1})
        qn += 1
        blocks.append({"t": "ruled", "lines": 6})
        content["sections"].append({"title": f"Lesson {i + 1}", "blocks": blocks})
    content["back_matter"].append({"title": "Answer Key", "blocks": [
        {"t": "h1", "text": "Answer Key"}, {"t": "answer_key_table"}]})


def _picture_book(cfg, content, rng, words):
    """§46: illustration-led pages, page-turn rhythm, short text."""
    chars = build_character_bible(cfg.seed)
    content["characters"] = [c.to_dict() for c in chars]
    n = cfg.options.get("story_pages", max(12, min(28, cfg.target_pages - 12)))
    dialogue = [f"Look at this!", "We can do it together.", "What happens next?",
                "That was wonderful.", "Let's try again tomorrow."]
    content["storyboard"] = build_storyboard(cfg.seed, n, chars, cfg.topic, dialogue)
    for i, board in enumerate(content["storyboard"]):
        text = board["narration"]
        if board["dialogue"]:
            text += f" \u201c{board['dialogue'][0]}\u201d"
        content["sections"].append({"title": "", "blocks": [
            {"t": "illustration", "scene": board["background"],
             "chars": board["characters"]},
            {"t": "p", "text": text}]})
    content["sections"].append({"title": "", "blocks": [
        {"t": "p", "text": "The End."}]})


def _cartoon_story(cfg, content, rng, words):
    _picture_book(cfg, content, rng, words)


def _comic(cfg, content, rng, words):
    """§53/§54: panels, gutters, speech bubbles, captions, sequence."""
    chars = build_character_bible(cfg.seed, cast=["fox", "rabbit", "child"])
    content["characters"] = [c.to_dict() for c in chars]
    pages = cfg.options.get("comic_pages", max(16, min(48, cfg.target_pages - 10)))
    content["storyboard"] = build_storyboard(cfg.seed, pages, chars, cfg.topic,
                                             ["Onward!", "Wait for me!", "Good plan.",
                                              "That's the spot.", "Careful now!"])
    for i, board in enumerate(content["storyboard"]):
        panels = []
        for p in range(rng.choice((2, 3))):
            panels.append({
                "scene": dict(board["background"]),
                "chars": board["characters"][: (p % len(board["characters"])) + 1],
                "dialogue": board["dialogue"] if p == 0 else [],
                "caption": f"{board['action'].title()} — panel {p + 1}" if p == 0 else "",
            })
        content["sections"].append({"title": "", "blocks": [
            {"t": "comic_page", "panels": panels, "page_number": i + 1}]})


def _alphabet(cfg, content):
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        content["sections"].append({"title": "", "blocks": [
            {"t": "illustration", "scene": {"sky": (0.75, 0.88, 0.95),
                                            "ground": (0.55, 0.75, 0.45),
                                            "hills": [0.5], "sun": True}, "chars": []},
            {"t": "trace", "chars": ch},
            {"t": "p", "text": f"{ch} is for {cfg.topic.split()[0].title() if cfg.topic else 'Apple'}."}]})


def _numbers(cfg, content):
    for n in range(1, 21):
        content["sections"].append({"title": "", "blocks": [
            {"t": "h2", "text": f"Number {n}"},
            {"t": "trace", "chars": str(n)},
            {"t": "grid", "rows": 2, "cols": n if n <= 7 else 7},
            {"t": "p", "text": f"Count {n} {cfg.topic.split()[0] if cfg.topic else 'item'}(s)."}]})
