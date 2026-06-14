"""P0 unit tests — pure parsers fed with synthetic git output.

No subprocess, no real repo: these lock the 1.8.3.1 text formats so the parsing
is regression-proof on any machine. Run:  python -m unittest discover -s tests
"""
import unittest

from gitkit.backend.capabilities import derive, parse_version
from gitkit.backend.cli_git import (
    _FS,
    _RS,
    _parse_decoration,
    _parse_track,
    parse_log_records,
    parse_numstat_z,
    parse_remote_branch_lines,
    parse_status_z,
)


def _rec(sha, parents, deco, author, date, subject):
    return _FS.join([sha, parents, deco, author, date, subject]) + _RS


class TestStatusZ(unittest.TestCase):
    def test_three_panels(self):
        # 'XY<space>path', NUL-separated. ' M'=modified, 'A '=staged, '??'=untracked.
        data = b" M file3.txt\x00A  staged_new.txt\x00?? untracked.txt\x00"
        entries = parse_status_z(data)
        self.assertEqual(len(entries), 3)
        mod, staged, untracked = entries
        self.assertEqual((mod.index_status, mod.worktree_status), (" ", "M"))
        self.assertTrue(mod.is_unstaged and not mod.is_staged)
        self.assertEqual(mod.category, "modified")
        self.assertTrue(staged.is_staged and staged.category == "staged")
        self.assertTrue(untracked.is_untracked and untracked.category == "untracked")

    def test_partially_staged_MM(self):
        entry = parse_status_z(b"MM crossbar.v\x00")[0]
        self.assertTrue(entry.is_staged)
        self.assertTrue(entry.is_unstaged)  # same file appears in BOTH panels

    def test_rename_carries_orig_in_next_token(self):
        # Rename: first token is dest path, following NUL token is the source.
        entries = parse_status_z(b"R  newname.v\x00oldname.v\x00")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].path, "newname.v")
        self.assertEqual(entries[0].orig_path, "oldname.v")

    def test_non_ascii_path_survives(self):
        data = " M 路由表.v\x00".encode("utf-8")
        entries = parse_status_z(data)
        self.assertEqual(entries[0].path, "路由表.v")

    def test_empty(self):
        self.assertEqual(parse_status_z(b""), [])


class TestDecoration(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_parse_decoration(""), [])

    def test_head_arrow_split(self):
        self.assertEqual(
            _parse_decoration(" (HEAD -> main, origin/main)"),
            ["HEAD", "main", "origin/main"],
        )

    def test_tag_kept(self):
        self.assertEqual(_parse_decoration(" (tag: v1.0)"), ["tag: v1.0"])


class TestTrack(unittest.TestCase):
    def test_both(self):
        self.assertEqual(_parse_track("[ahead 1, behind 3]"), (1, 3, False))

    def test_ahead_only(self):
        self.assertEqual(_parse_track("[ahead 2]"), (2, 0, False))

    def test_behind_only(self):
        self.assertEqual(_parse_track("[behind 5]"), (0, 5, False))

    def test_gone(self):
        self.assertEqual(_parse_track("[gone]"), (0, 0, True))

    def test_up_to_date(self):
        self.assertEqual(_parse_track(""), (0, 0, False))


class TestLogRecords(unittest.TestCase):
    def test_root_merge_and_refs(self):
        stream = (
            _rec("a" * 40, "b" * 40 + " " + "c" * 40, " (HEAD -> main)",
                 "Ricky", "2026-06-13", "Merge feature")
            + _rec("d" * 40, "", "", "Ricky", "2026-06-12", "A: initial")
        )
        commits = parse_log_records(stream)
        self.assertEqual(len(commits), 2)
        merge, root = commits
        self.assertEqual(len(merge.parents), 2)
        self.assertTrue(merge.is_merge)
        self.assertEqual(merge.refs, ["HEAD", "main"])
        self.assertEqual(merge.short_sha, "aaaaaaa")
        self.assertEqual(root.parents, [])  # root commit
        self.assertFalse(root.is_merge)

    def test_subject_with_spaces(self):
        c = parse_log_records(_rec("a" * 40, "", "", "X", "2026-06-13", "fix: a b c"))[0]
        self.assertEqual(c.subject, "fix: a b c")


class TestNumstat(unittest.TestCase):
    def test_counts_and_binary(self):
        files = parse_numstat_z(b"3\t1\tfile.py\x00-\t-\tbin.png\x00")
        self.assertEqual((files[0].added, files[0].removed), (3, 1))
        self.assertEqual((files[1].added, files[1].removed), (0, 0))  # binary => '-'


class TestRemoteBranches(unittest.TestCase):
    def test_filters_head_pointer_and_keeps_slashed_names(self):
        # 'origin' (no slash) is the origin/HEAD symref short form -> dropped.
        text = (
            "origin\t1111111111111111111111111111111111111111\n"
            "origin/master\t2222222222222222222222222222222222222222\n"
            "origin/release/7.7.7\t3333333333333333333333333333333333333333\n"
            "origin/HEAD\t4444444444444444444444444444444444444444\n"
        )
        rbs = parse_remote_branch_lines(text)
        names = [b.name for b in rbs]
        self.assertEqual(names, ["origin/master", "origin/release/7.7.7"])
        self.assertEqual(rbs[0].remote, "origin")
        self.assertEqual(rbs[0].short_sha, "2222222")
        # nested name keeps its remote as the first segment only
        self.assertEqual(rbs[1].remote, "origin")
        self.assertEqual(rbs[1].name, "origin/release/7.7.7")


class TestCapabilities(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(parse_version("git version 2.54.0.windows.1"), (2, 54, 0))
        self.assertEqual(parse_version("git version 1.8.3.1"), (1, 8, 3))

    def test_flags_old_git(self):
        caps = derive((1, 8, 3))
        self.assertFalse(caps.has_dash_C)
        self.assertFalse(caps.has_switch_restore)
        self.assertFalse(caps.has_porcelain_v2)

    def test_flags_new_git(self):
        caps = derive((2, 54, 0))
        self.assertTrue(caps.has_dash_C)
        self.assertTrue(caps.has_switch_restore)
        self.assertTrue(caps.has_porcelain_v2)


if __name__ == "__main__":
    unittest.main()
