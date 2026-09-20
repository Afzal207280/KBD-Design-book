"""Acceptance tests §237: puzzle generators + INDEPENDENT verifiers.

Verifiers are separate code paths from generators; a puzzle that passes its
own generator is worthless — it must survive an independent re-check.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bookfactory.puzzles import (generate_sudoku, verify_sudoku,
                                 generate_word_search, verify_word_search,
                                 generate_maze, verify_maze, solve_maze,
                                 serialize_maze, rehydrate_maze)


class TestSudoku(unittest.TestCase):
    def test_solution_valid_and_matches_givens(self):
        for seed in (1, 7, 42, 999):
            p = generate_sudoku(seed, givens=30)
            res = verify_sudoku(p)
            self.assertTrue(res["valid"], res["errors"])
            # givens preserved in solution
            for r in range(9):
                for c in range(9):
                    if p["grid"][r][c] != 0:
                        self.assertEqual(p["grid"][r][c], p["solution"][r][c])

    def test_independent_verifier_rejects_corruption(self):
        p = generate_sudoku(5, givens=30)
        bad = {"grid": [row[:] for row in p["grid"]],
               "solution": [row[:] for row in p["solution"]]}
        bad["solution"][0][0] = (bad["solution"][0][0] % 9) + 1
        res = verify_sudoku(bad)
        self.assertFalse(res["valid"])

    def test_deterministic(self):
        a = generate_sudoku(123, givens=28)
        b = generate_sudoku(123, givens=28)
        self.assertEqual(a["solution"], b["solution"])


class TestWordSearch(unittest.TestCase):
    def test_all_words_placed_and_findable(self):
        p = generate_word_search(11, ["FOREST", "RIVER", "PATH", "QUIET"], size=13)
        res = verify_word_search(p)
        self.assertTrue(res["valid"], res["errors"])

    def test_verifier_rejects_missing_word(self):
        p = generate_word_search(11, ["FOREST", "RIVER"], size=13)
        p["words"] = p["words"] + ["ABSENT"]
        res = verify_word_search(p)
        self.assertFalse(res["valid"])


class TestMaze(unittest.TestCase):
    def test_solvability_and_serialization_roundtrip(self):
        p = generate_maze(3, cols=14, rows=18)
        res = verify_maze(p)
        self.assertTrue(res["valid"], res["errors"])
        path = solve_maze(p)
        self.assertIsNotNone(path)
        self.assertGreater(len(path), 5)
        sp = serialize_maze(p)
        rp = rehydrate_maze(sp)
        self.assertEqual(sorted(p.keys()), sorted(rp.keys()))
        res2 = verify_maze(rp)
        self.assertTrue(res2["valid"], res2["errors"])


if __name__ == "__main__":
    unittest.main()
