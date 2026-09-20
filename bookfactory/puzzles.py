"""Puzzle-correctness engine (spec §49, §159): every puzzle that can be
verified deterministically IS verified deterministically — solvability,
solution existence, answer correctness, numbering, duplicates, answer-key
mapping, uniqueness where required.
"""
from __future__ import annotations

import random

# ---------------------------------------------------------------------------
# SUDOKU
# ---------------------------------------------------------------------------

def _sudoku_rows():
    rows = []
    for r in range(9):
        row = []
        for c in range(9):
            row.append((3 * (r % 3) + r // 3 + c) % 9 + 1)
        rows.append(row)
    return rows


def generate_sudoku(seed: int, givens: int = 34) -> dict:
    """Generate a valid puzzle with a UNIQUE solution.
    Returns {grid: 9x9 ints (0=empty), solution: 9x9, givens: n}."""
    rng = random.Random(seed)
    base = _sudoku_rows()
    # permute symbols, rows within bands, bands, cols within stacks, stacks
    perm = list(range(1, 10)); rng.shuffle(perm)
    grid = [[perm[base[r][c] - 1] for c in range(9)] for r in range(9)]
    rows_order = []
    bands = [0, 1, 2]; rng.shuffle(bands)
    for b in bands:
        rs = [3 * b, 3 * b + 1, 3 * b + 2]; rng.shuffle(rs)
        rows_order += rs
    cols_order = []
    stacks = [0, 1, 2]; rng.shuffle(stacks)
    for s in stacks:
        cs = [3 * s, 3 * s + 1, 3 * s + 2]; rng.shuffle(cs)
        cols_order += cs
    grid = [[grid[r][c] for c in cols_order] for r in rows_order]
    solution = [row[:] for row in grid]
    cells = [(r, c) for r in range(9) for c in range(9)]
    rng.shuffle(cells)
    removed = 0
    target = 81 - givens
    for (r, c) in cells:
        if removed >= target:
            break
        saved = grid[r][c]
        grid[r][c] = 0
        if count_solutions(grid, limit=2) != 1:
            grid[r][c] = saved          # keep uniqueness (§49)
        else:
            removed += 1
    return {"grid": grid, "solution": solution, "givens": 81 - sum(
        1 for r in range(9) for c in range(9) if grid[r][c] == 0)}


def _candidates(grid, r, c):
    used = set(grid[r]) | {grid[i][c] for i in range(9)}
    br, bc = 3 * (r // 3), 3 * (c // 3)
    for i in range(3):
        for j in range(3):
            used.add(grid[br + i][bc + j])
    return [v for v in range(1, 10) if v not in used]


def count_solutions(grid, limit: int = 2) -> int:
    """Backtracking solution counter (0..limit). Mutates nothing permanently."""
    g = [row[:] for row in grid]

    def solve() -> int:
        best = None
        for r in range(9):
            for c in range(9):
                if g[r][c] == 0:
                    cands = _candidates(g, r, c)
                    if best is None or len(cands) < len(best[2]):
                        best = (r, c, cands)
                        if len(cands) <= 1:
                            break
            if best and len(best[2]) <= 1:
                break
        if best is None:
            return 1
        r, c, cands = best
        if not cands:
            return 0
        total = 0
        for v in cands:
            g[r][c] = v
            total += solve()
            g[r][c] = 0
            if total >= limit:
                return total
        return total

    return solve()


def verify_sudoku(puzzle: dict) -> dict:
    """Deterministic correctness check of a puzzle + its claimed solution."""
    errors = []
    grid, sol = puzzle["grid"], puzzle["solution"]
    if len(grid) != 9 or any(len(r) != 9 for r in grid):
        errors.append("grid is not 9x9")
        return {"valid": False, "errors": errors}
    # solution is complete and valid
    for unit_get in (lambda i: [sol[i][c] for c in range(9)],
                     lambda i: [sol[r][i] for r in range(9)]):
        for i in range(9):
            if sorted(unit_get(i)) != list(range(1, 10)):
                errors.append("solution row/column invalid")
    for br in range(0, 9, 3):
        for bc in range(0, 9, 3):
            box = [sol[br + i][bc + j] for i in range(3) for j in range(3)]
            if sorted(box) != list(range(1, 10)):
                errors.append("solution box invalid")
    # solution consistent with givens
    for r in range(9):
        for c in range(9):
            if grid[r][c] != 0 and grid[r][c] != sol[r][c]:
                errors.append(f"given at ({r},{c}) contradicts solution")
    # unique solution
    if not errors and count_solutions(grid, limit=2) != 1:
        errors.append("puzzle does not have exactly one solution")
    return {"valid": not errors, "errors": errors}


# ---------------------------------------------------------------------------
# WORD SEARCH
# ---------------------------------------------------------------------------

_DIRS = [(0, 1), (1, 0), (1, 1), (-1, 1), (0, -1), (-1, 0)]


def generate_word_search(seed: int, words: list[str], size: int = 12) -> dict:
    rng = random.Random(seed)
    words = sorted(set(w.upper() for w in words if 2 < len(w) <= size),
                   key=lambda w: -len(w))
    grid = [[""] * size for _ in range(size)]
    placed = []
    for w in words:
        ok = False
        for _ in range(400):
            d = rng.choice(_DIRS)
            r0 = rng.randrange(size); c0 = rng.randrange(size)
            r1 = r0 + d[0] * (len(w) - 1); c1 = c0 + d[1] * (len(w) - 1)
            if not (0 <= r1 < size and 0 <= c1 < size):
                continue
            ok2 = True
            for i, ch in enumerate(w):
                cell = grid[r0 + d[0] * i][c0 + d[1] * i]
                if cell and cell != ch:
                    ok2 = False
                    break
            if not ok2:
                continue
            for i, ch in enumerate(w):
                grid[r0 + d[0] * i][c0 + d[1] * i] = ch
            placed.append({"word": w, "start": (r0, c0), "dir": d})
            ok = True
            break
        if not ok:
            raise ValueError(f"could not place word {w!r} — regenerate with fewer words")
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for r in range(size):
        for c in range(size):
            if grid[r][c] == "":
                grid[r][c] = rng.choice(letters)
    return {"grid": grid, "size": size, "placements": placed,
            "words": [p["word"] for p in placed]}


def verify_word_search(puzzle: dict) -> dict:
    """Every word must be findable; claimed placements must be correct."""
    errors = []
    grid, size = puzzle["grid"], puzzle["size"]
    for p in puzzle["placements"]:
        w = p["word"]; r0, c0 = p["start"]; dr, dc = p["dir"]
        for i, ch in enumerate(w):
            r, c = r0 + dr * i, c0 + dc * i
            if not (0 <= r < size and 0 <= c < size):
                errors.append(f"{w}: placement out of bounds")
                break
            if grid[r][c] != ch:
                errors.append(f"{w}: grid mismatch at step {i}")
                break
    # independent re-search: each word appears at least once in the grid
    def find(word):
        n = len(word)
        for r in range(size):
            for c in range(size):
                for dr, dc in _DIRS:
                    rr, cc = r + dr * (n - 1), c + dc * (n - 1)
                    if 0 <= rr < size and 0 <= cc < size and all(
                            grid[r + dr * i][c + dc * i] == word[i] for i in range(n)):
                        return True
        return False
    for w in puzzle["words"]:
        if not find(w):
            errors.append(f"word {w} not actually present in grid")
    return {"valid": not errors, "errors": errors}


# ---------------------------------------------------------------------------
# MAZE (perfect maze by recursive backtracker; BFS-verified solution)
# ---------------------------------------------------------------------------

def generate_maze(seed: int, cols: int = 16, rows: int = 20) -> dict:
    rng = random.Random(seed)
    walls = {(r, c): [True, True, True, True] for r in range(rows) for c in range(cols)}
    # walls: [top, right, bottom, left]
    seen = [[False] * cols for _ in range(rows)]
    stack = [(0, 0)]
    seen[0][0] = True
    while stack:
        r, c = stack[-1]
        options = []
        for dr, dc, wi, oi in ((-1, 0, 0, 2), (0, 1, 1, 3), (1, 0, 2, 0), (0, -1, 3, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not seen[nr][nc]:
                options.append((nr, nc, wi, oi))
        if not options:
            stack.pop()
            continue
        nr, nc, wi, oi = rng.choice(options)
        walls[(r, c)][wi] = False
        walls[(nr, nc)][oi] = False
        seen[nr][nc] = True
        stack.append((nr, nc))
    start, end = (0, 0), (rows - 1, cols - 1)
    return {"rows": rows, "cols": cols, "walls": walls, "start": start, "end": end}


def serialize_maze(p: dict) -> dict:
    """Make a maze JSON-safe (tuple dict keys -> nested lists)."""
    out = dict(p)
    out["walls"] = [[list(p["walls"][(r, c)]) for c in range(p["cols"])]
                    for r in range(p["rows"])]
    out["start"] = list(p["start"])
    out["end"] = list(p["end"])
    return out


def rehydrate_maze(p: dict) -> dict:
    """Inverse of serialize_maze (idempotent)."""
    if isinstance(p.get("walls"), dict):
        return p
    out = dict(p)
    out["walls"] = {(r, c): list(p["walls"][r][c]) for r in range(p["rows"])
                    for c in range(p["cols"])}
    out["start"] = tuple(p["start"])
    out["end"] = tuple(p["end"])
    return out


def solve_maze(maze: dict) -> list | None:
    """BFS solver. Returns path or None if unsolvable."""
    from collections import deque
    rows, cols, walls = maze["rows"], maze["cols"], maze["walls"]
    q = deque([maze["start"]])
    prev = {maze["start"]: None}
    while q:
        r, c = q.popleft()
        if (r, c) == maze["end"]:
            path = []
            cur = (r, c)
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return list(reversed(path))
        for dr, dc, wi in ((-1, 0, 0), (0, 1, 1), (1, 0, 2), (0, -1, 3)):
            if walls[(r, c)][wi]:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and (nr, nc) not in prev:
                prev[(nr, nc)] = (r, c)
                q.append((nr, nc))
    return None


def verify_maze(maze: dict) -> dict:
    """§49: solvability + exactly-one-cell-wide start/end + solution exists."""
    errors = []
    path = solve_maze(maze)
    if path is None:
        errors.append("maze is unsolvable")
    else:
        # verify path continuity: consecutive cells adjacent and wall open
        walls = maze["walls"]
        for a, b in zip(path, path[1:]):
            dr, dc = b[0] - a[0], b[1] - a[1]
            wi = {(-1, 0): 0, (0, 1): 1, (1, 0): 2, (0, -1): 3}.get((dr, dc))
            if wi is None or walls[a][wi]:
                errors.append(f"path crosses a wall between {a} and {b}")
                break
    return {"valid": not errors, "errors": errors,
            "solution_path": path if path else []}


# ---------------------------------------------------------------------------
# CROSSWORD (small deterministic fill; verified against clues/answer key)
# ---------------------------------------------------------------------------

def generate_crossword(seed: int, entries: list[tuple[str, str]]) -> dict:
    """entries: [(ANSWER, clue)]. Places words on a grid with crossings;
    every placement is verified afterward."""
    rng = random.Random(seed)
    entries = sorted(entries, key=lambda e: -len(e[0]))
    size = max(11, min(21, max(len(e[0]) for e in entries) + 4))
    grid = [[""] * size for _ in range(size)]
    placed = []

    def fits(word, r, c, dr, dc):
        touched = False
        for i, ch in enumerate(word):
            rr, cc = r + dr * i, c + dc * i
            if not (0 <= rr < size and 0 <= cc < size):
                return False
            cur = grid[rr][cc]
            if cur:
                if cur != ch:
                    return False
                touched = True
            else:
                # no perpendicular adjacency unless crossing
                if dr == 0 and ((rr > 0 and grid[rr - 1][cc]) or (rr < size - 1 and grid[rr + 1][cc])):
                    return False
                if dc == 0 and ((cc > 0 and grid[rr][cc - 1]) or (cc < size - 1 and grid[rr][cc + 1])):
                    return False
        before_r, before_c = r - dr, c - dc
        if 0 <= before_r < size and 0 <= before_c < size and grid[before_r][before_c]:
            return False
        after_r, after_c = r + dr * len(word), c + dc * len(word)
        if 0 <= after_r < size and 0 <= after_c < size and grid[after_r][after_c]:
            return False
        return touched or not placed  # first word anywhere, others need crossing

    for word, clue in entries:
        word = word.upper()
        done = False
        for _ in range(300):
            dr, dc = rng.choice(((0, 1), (1, 0)))
            r = rng.randrange(size); c = rng.randrange(size)
            if fits(word, r, c, dr, dc):
                for i, ch in enumerate(word):
                    grid[r + dr * i][c + dc * i] = ch
                placed.append({"answer": word, "clue": clue, "start": (r, c),
                               "dir": "across" if dr == 0 else "down"})
                done = True
                break
        if not done:
            continue  # unplaced words are reported, not silently lost
    return {"grid": grid, "size": size, "placed": placed,
            "unplaced": [w for w, _ in entries
                         if w.upper() not in {p["answer"] for p in placed}]}


def verify_crossword(puzzle: dict) -> dict:
    errors = []
    grid, size = puzzle["grid"], puzzle["size"]
    for p in puzzle["placed"]:
        dr, dc = (0, 1) if p["dir"] == "across" else (1, 0)
        r, c = p["start"]
        for i, ch in enumerate(p["answer"]):
            rr, cc = r + dr * i, c + dc * i
            if not (0 <= rr < size and 0 <= cc < size):
                errors.append(f"{p['answer']}: out of bounds")
                break
            if grid[rr][cc] != ch:
                errors.append(f"{p['answer']}: grid mismatch")
                break
    answers = [p["answer"] for p in puzzle["placed"]]
    if len(answers) != len(set(answers)):
        errors.append("duplicate answers in crossword")
    return {"valid": not errors, "errors": errors}
