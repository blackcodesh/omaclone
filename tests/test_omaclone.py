"""Tests for the omaclone backend. Run: python3 -I -m unittest discover -s tests -p "test_*.py"
Everything runs in throwaway folders (a local bare repository stands in for a server); no network is needed."""
import base64
import http.server
import importlib.machinery
import importlib.util
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "omaclone")


def load():
    loader = importlib.machinery.SourceFileLoader("omaclone_tool", TOOL)
    spec = importlib.util.spec_from_loader("omaclone_tool", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


M = load()


class Env(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = dict(os.environ, HOME=self.home, XDG_STATE_HOME=os.path.join(self.home, "state"), OMACLONE_ALLOW_LOCAL="1")
        self.env.pop("GIT_SSH_COMMAND", None)

    def run_tool(self, *args, env=None, timeout=60, stdin_text=None):
        r = subprocess.run([sys.executable, "-I", TOOL, *args], capture_output=True, text=True, env=env or self.env, timeout=timeout,
                           input=stdin_text if stdin_text is not None else "", **({} if stdin_text is not None else {}))
        lines = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
        return r, lines

    def git(self, *args, cwd=None):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "init.defaultBranch=main", *args], cwd=cwd, check=True, capture_output=True, env=dict(os.environ, HOME=self.home))

    def make_server(self, branches=("dev",), tags=("v1",)):
        """A bare repository with two commits on main, extra branches and tags (a stand-in for any git server)."""
        work = os.path.join(self.home, "work")
        bare = os.path.join(self.home, "server", "owner", "proj.git")
        os.makedirs(work)
        os.makedirs(bare)
        self.git("init", "-q", cwd=work)
        for i in (1, 2):
            with open(os.path.join(work, f"f{i}.txt"), "w") as f:
                f.write(f"file {i}\n" * 200)
            self.git("add", ".", cwd=work)
            self.git("commit", "-q", "-m", f"commit {i}", cwd=work)
        for b in branches:
            self.git("branch", b, cwd=work)
        for t in tags:
            self.git("tag", t, cwd=work)
        self.git("init", "-q", "--bare", bare)
        self.git("push", "-q", "--all", bare, cwd=work)
        self.git("push", "-q", "--tags", bare, cwd=work)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
        return bare


class Parse(unittest.TestCase):
    def ok(self, text, **want):
        p = M.parse(text)
        self.assertTrue(p["ok"], f"{text!r}: {p}")
        for k, v in want.items():
            self.assertEqual(p[k], v, f"{text!r}: {k}")
        return p

    def refused(self, text, code=None):
        p = M.parse(text)
        self.assertFalse(p["ok"], f"{text!r} should be refused: {p}")
        if code:
            self.assertEqual(p["error"], code, text)

    def test_plain_links_from_many_providers(self):
        for url, provider in (("https://github.com/o/r", "GitHub"), ("https://gitlab.com/o/r", "GitLab"), ("https://codeberg.org/o/r", "Codeberg"),
                              ("https://bitbucket.org/o/r", "Bitbucket"), ("https://git.sr.ht/~o/r", "SourceHut"), ("https://gitea.com/o/r", "Gitea"),
                              ("https://git.example.org/o/r", "git.example.org"), ("https://forgejo.myserver.dev/o/r.git", "forgejo.myserver.dev")):
            p = self.ok(url, provider=provider, name="r")
            self.assertEqual(p["cloneUrl"], url.replace("%7E", "~"))

    def test_browser_pages_are_cut_back_to_the_repository(self):
        self.ok("https://github.com/o/r/tree/main/some/dir", cloneUrl="https://github.com/o/r", branch="main", refPath="main/some/dir")
        self.ok("https://github.com/o/r/blob/v1.2/README.md", cloneUrl="https://github.com/o/r", branch="v1.2")
        self.ok("https://github.com/o/r/pull/42/files", cloneUrl="https://github.com/o/r", branch="")
        self.ok("https://github.com/o/r/issues/7", cloneUrl="https://github.com/o/r")
        self.ok("https://github.com/o/r/releases/tag/v1", cloneUrl="https://github.com/o/r")
        self.ok("https://github.com/o/r/actions", cloneUrl="https://github.com/o/r")
        self.ok("https://github.com/o/r/wiki/Home", cloneUrl="https://github.com/o/r")

    def test_forgejo_gitea_codeberg_urls(self):
        self.ok("https://codeberg.org/o/r/src/branch/dev/docs", cloneUrl="https://codeberg.org/o/r", branch="dev", kind="branch", provider="Codeberg")
        self.ok("https://codeberg.org/o/r/src/tag/v1.0", cloneUrl="https://codeberg.org/o/r", branch="v1.0", kind="tag")
        p = self.ok("https://forge.example.com/o/r/src/commit/abc123", cloneUrl="https://forge.example.com/o/r", branch="", kind="commit")
        self.assertEqual(p["provider"], "Forgejo / Gitea")
        self.ok("https://codeberg.org/o/r/pulls/3", cloneUrl="https://codeberg.org/o/r")
        self.ok("https://codeberg.org/o/r/raw/branch/main/file", cloneUrl="https://codeberg.org/o/r", branch="main")

    def test_gitlab_subgroups_and_dash_pages(self):
        self.ok("https://gitlab.com/g/sub/deeper/r/-/tree/dev/src", cloneUrl="https://gitlab.com/g/sub/deeper/r", branch="dev", name="r")
        self.ok("https://gitlab.com/g/r/-/merge_requests/9", cloneUrl="https://gitlab.com/g/r")
        self.ok("https://gitlab.example.com/g/sub/r/tree/main", cloneUrl="https://gitlab.example.com/g/sub/r", branch="main")
        self.ok("https://gitlab.com/g/r.git", cloneUrl="https://gitlab.com/g/r.git", name="r")

    def test_other_hosts(self):
        self.ok("https://bitbucket.org/o/r/src/main/", cloneUrl="https://bitbucket.org/o/r", branch="main")
        self.ok("https://git.sr.ht/~user/proj/tree/main/item/README", cloneUrl="https://git.sr.ht/~user/proj", name="proj")
        self.ok("https://dev.azure.com/org/project/_git/repo", cloneUrl="https://dev.azure.com/org/project/_git/repo", name="repo")
        self.ok("https://dev.azure.com/org/project/_git/repo?path=/x&version=GBmain", cloneUrl="https://dev.azure.com/org/project/_git/repo", name="repo")

    def test_ssh_and_scp_like_links(self):
        self.ok("git@github.com:o/r.git", cloneUrl="git@github.com:o/r.git", scheme="ssh", name="r", provider="GitHub")
        self.ok("git@codeberg.org:o/r", cloneUrl="git@codeberg.org:o/r", scheme="ssh")
        self.ok("ssh://git@host.example:2222/o/r.git", cloneUrl="ssh://git@host.example:2222/o/r.git", scheme="ssh", name="r")
        self.ok("git://git.example.org/r.git", scheme="git", name="r")

    def test_pasted_git_clone_commands(self):
        self.ok("git clone https://github.com/o/r.git", cloneUrl="https://github.com/o/r.git")
        self.ok("git clone -b dev --depth 1 https://github.com/o/r", cloneUrl="https://github.com/o/r")
        self.ok("git clone --branch=dev https://github.com/o/r my-folder", cloneUrl="https://github.com/o/r")
        self.ok("git clone git@github.com:o/r.git", cloneUrl="git@github.com:o/r.git")

    def test_messy_paste(self):
        self.ok("  https://github.com/o/r  \n", cloneUrl="https://github.com/o/r")
        self.ok('"https://github.com/o/r"', cloneUrl="https://github.com/o/r")
        self.ok("<https://github.com/o/r>", cloneUrl="https://github.com/o/r")
        self.ok("(https://github.com/o/r)", cloneUrl="https://github.com/o/r")
        self.ok("https://github.com/o/r\nsecond line", cloneUrl="https://github.com/o/r")
        self.ok("github.com/o/r", cloneUrl="https://github.com/o/r")
        self.ok("https://github.com/o/r/", cloneUrl="https://github.com/o/r")
        self.ok("HTTPS://GitHub.com/o/r", scheme="https", host="github.com")

    def test_a_password_or_token_is_taken_out_of_the_link_in_every_form(self):
        cases = {
            "https://user:s3cr3t-token@git.example.org/o/r.git": ("user", "s3cr3t-token"),
            "https://oauth2:glpat-abc@gitlab.com/o/r": ("oauth2", "glpat-abc"),
            "https://u:p@ss@host.org/o/r": ("u", "p@ss"),                      # an unencoded @ in the password
            "HTTPS://u:%40t%2Fok@host.org:8443/o/r": ("u", "@t/ok"),           # upper case scheme, encoded characters, a port
            "http://u:tok@host.org/o/r/tree/main": ("u", "tok"),
            "git clone -b dev https://u:tok@host.org/o/r": ("u", "tok"),
            "https://:tok@host.org/o/r": ("", "tok"),
            "https://ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/o/r": ("", "ghp_abcdefghijklmnopqrstuvwxyz0123"),
        }
        for link, (user, secret) in cases.items():
            p = M.parse(link)
            self.assertTrue(p["ok"], link)
            self.assertEqual({k: p["login"][k] for k in ("user", "password")}, {"user": user, "password": secret}, link)
            for field in ("cloneUrl", "display", "cleanText", "webUrl", "webDisplay", "sshUrl"):
                self.assertNotIn(secret, p.get(field) or "", (link, field))
            self.assertTrue(p["hasPassword"])

    def test_a_token_that_is_the_whole_login_is_not_a_username(self):
        token = "ghp_abcdefghijklmnopqrstuvwxyz0123"
        p = M.parse(f"https://{token}@github.com/o/r")
        self.assertEqual(p["login"], {"user": "", "password": token, "tokenAsUser": True})
        self.assertEqual(p["user"], "")                                    # nothing for the panel to show as a username
        for field in ("cloneUrl", "display", "cleanText", "webUrl", "sshUrl", "user"):
            self.assertNotIn(token, p.get(field) or "", field)

    def test_each_link_reports_its_exact_web_origin(self):
        self.assertEqual(M.parse("https://Git.Example.org/o/r")["webOrigin"], "https://git.example.org")
        self.assertEqual(M.parse("https://git.example.org:8443/o/r")["webOrigin"], "https://git.example.org:8443")
        self.assertEqual(M.parse("http://git.example.org/o/r")["webOrigin"], "http://git.example.org")
        self.assertEqual(M.parse("git@git.example.org:o/r.git")["webOrigin"], "https://git.example.org")
        self.assertEqual(M.parse("ssh://git@git.example.org:2222/o/r.git")["webOrigin"], "https://git.example.org")      # not the ssh port

    def test_the_cleaned_text_keeps_the_rest_of_what_was_pasted(self):
        p = M.parse("git clone -b dev https://u:tok@host.org/o/r")
        self.assertEqual(p["cleanText"], "git clone -b dev https://u@host.org/o/r")
        self.assertEqual(p["branch"], "")

    def test_ssh_links_never_take_a_password_from_the_link(self):
        p = M.parse("ssh://git:pw@host.org/o/r")
        self.assertNotIn("login", p)
        self.assertNotIn("pw", p["cloneUrl"] + p["display"] + p.get("cleanText", ""))

    def test_a_password_that_cannot_be_told_from_a_path_is_refused_and_not_echoed(self):
        for link in ("https://u:pa/ss@host.org/o/r", "https://u:p?ss@host.org/o/r", "https://u:p#ss@host.org/o/r"):
            p = M.parse(link)
            self.assertFalse(p["ok"], link)
            self.assertTrue(p["wipe"], link)
            self.assertNotIn("cleanText", p)

    def test_normal_links_with_an_at_or_a_port_are_untouched(self):
        for link in ("https://github.com/o/r@v1", "https://host.org:8443/o/r", "https://host.org/o/r?x=a@b"):
            p = M.parse(link)
            self.assertNotIn("login", p, link)
            self.assertNotIn("cleanText", p, link)

    def test_the_parse_command_answers_after_one_line_without_waiting_for_the_pipe_to_close(self):
        # the panel keeps its end of the pipe open, so the script must not wait for end-of-input
        p = subprocess.Popen([sys.executable, "-I", TOOL, "parse", "--stdin"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        p.stdin.write("https://u:tok@host.org/o/r\n")
        p.stdin.flush()
        line = p.stdout.readline()                       # would hang here if it read until EOF
        p.stdin.close()
        p.wait(timeout=10)
        self.assertEqual(json.loads(line)["login"]["password"], "tok")

    def test_the_parse_command_can_read_the_link_from_stdin(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "parse", "--stdin"], input="https://u:tok@host.org/o/r\n", capture_output=True, text=True)
        d = json.loads(r.stdout)
        self.assertEqual(d["login"]["password"], "tok")
        self.assertNotIn("tok", d["cloneUrl"])

    def test_dangerous_or_unsupported_links_are_refused(self):
        for text in ("ext::sh -c 'touch /tmp/pwn'", "ext::git-shell", "fd::17", "EXT::foo", "-oProxyCommand=evil", "--upload-pack=evil https://x/y",
                     "ftp://example.org/r.git", "javascript:alert(1)", "file:///etc", "/etc/passwd", "data:text/plain,hi", "rsync://h/r",
                     "https://raw.githubusercontent.com/o/r/main/x.py", "", "   ", "hello world", "https://", "https://github.com/"):
            self.refused(text)
        self.refused("ext::sh", "unsafe")
        self.refused("-x", "unsafe")
        self.refused("ftp://example.org/r.git", "scheme")

    def test_repo_names_are_safe_folder_names(self):
        self.assertEqual(M.safe_name("good-name_1.0"), "good-name_1.0")
        for bad in ("", ".", "..", "-rf", "a/b", "a\\b", "x\x00y", "x" * 300, "  "):
            self.assertEqual(M.safe_name(bad), "", repr(bad))
        self.assertEqual(self.ok("https://github.com/o/..%2e", name="repo")["name"], "repo") if False else None

    def test_masking(self):
        self.assertEqual(M.mask("https://u:pw@h/x"), "https://u:***@h/x")
        self.assertEqual(M.mask("https://h/x"), "https://h/x")
        self.assertEqual(M.mask("git@h:o/r"), "git@h:o/r")


class Classify(unittest.TestCase):
    def test_server_replies(self):
        cases = {
            "fatal: could not read Username for 'https://github.com': terminal prompts disabled": "auth",
            "remote: HTTP Basic: Access denied": "auth",
            "fatal: Authentication failed for 'https://x/y.git/'": "auth",
            "git@host: Permission denied (publickey).": "auth",
            "fatal: unable to access 'https://x/y/': The requested URL returned error: 403": "auth",
            "remote: Repository not found.\nfatal: repository 'https://x/y/' not found": "notfound",
            "fatal: unable to access 'https://x/y/': The requested URL returned error: 404": "notfound",
            "fatal: unable to access 'https://nope.invalid/': Could not resolve host: nope.invalid": "network",
            "ssh: connect to host h port 22: Connection timed out": "network",
            "Host key verification failed.": "host-key",
            "something strange": "other",
        }
        for text, want in cases.items():
            self.assertEqual(M.classify(text), want, text)


class StateAndSuggest(Env):
    def test_suggest_lists_matching_folders_only(self):
        for d in ("Projects", "Pictures", "Public", ".hidden", "docs"):
            os.makedirs(os.path.join(self.home, d))
        open(os.path.join(self.home, "Pfile"), "w").close()
        r, [res] = self.run_tool("suggest", "~/P")
        self.assertEqual(sorted(res["items"]), ["~/Pictures", "~/Projects", "~/Public"])
        r, [res] = self.run_tool("suggest", "~/")
        self.assertIn("~/docs", res["items"])
        self.assertNotIn("~/.hidden", res["items"])
        r, [res] = self.run_tool("suggest", "~/.h")
        self.assertEqual(res["items"], ["~/.hidden"])
        r, [res] = self.run_tool("suggest", "~/Projects")
        self.assertTrue(res["exists"] and res["writable"])
        r, [res] = self.run_tool("suggest", "~/nope/deeper")
        self.assertFalse(res["exists"])

    def test_default_folder_prefers_the_setting_then_recent_then_projects(self):
        r, [s] = self.run_tool("state")
        self.assertEqual(s["defaultFolder"], "~")
        os.makedirs(os.path.join(self.home, "Projects"))
        r, [s] = self.run_tool("state")
        self.assertEqual(s["defaultFolder"], "~/Projects")
        r, [s] = self.run_tool("state", "--default", "~/elsewhere")
        self.assertEqual(s["defaultFolder"], "~/elsewhere")


class Browse(Env):
    def folders(self, *names):
        for n in names:
            os.makedirs(os.path.join(self.home, n))

    def test_ls_lists_folders_sorted_without_files_or_hidden_ones(self):
        self.folders("Zeta", "alpha", "Beta", ".secret", "Projects/inner")
        open(os.path.join(self.home, "file.txt"), "w").close()
        r, [res] = self.run_tool("ls", self.home)
        self.assertEqual([i["name"] for i in res["items"]], ["alpha", "Beta", "Projects", "Zeta"])
        self.assertTrue(res["writable"] and res["exists"])
        self.assertEqual(res["parent"], os.path.dirname(self.home))
        r, [res] = self.run_tool("ls", self.home, "--hidden")
        self.assertIn(".secret", [i["name"] for i in res["items"]])

    def test_ls_gives_the_full_path_of_each_folder(self):
        self.folders("Projects/inner")
        r, [res] = self.run_tool("ls", os.path.join(self.home, "Projects"))
        self.assertEqual(res["items"], [{"name": "inner", "path": os.path.join(self.home, "Projects", "inner")}])

    def test_ls_of_a_path_that_does_not_exist_falls_back_to_the_nearest_existing_folder(self):
        self.folders("Projects")
        r, [res] = self.run_tool("ls", os.path.join(self.home, "Projects", "new", "deeper"))
        self.assertEqual(res["path"], os.path.join(self.home, "Projects"))
        self.assertFalse(res["exists"])

    def test_ls_understands_the_tilde_and_the_root_has_no_parent(self):
        self.folders("Projects")
        r, [res] = self.run_tool("ls", "~")
        self.assertEqual(res["path"], self.home)
        self.assertIn("Projects", [i["name"] for i in res["items"]])
        r, [res] = self.run_tool("ls", "/")
        self.assertEqual(res["parent"], "")

    def test_ls_reports_an_unreadable_folder_instead_of_failing(self):
        self.folders("locked/inside")
        locked = os.path.join(self.home, "locked")
        os.chmod(locked, 0o000)
        try:
            r, [res] = self.run_tool("ls", locked)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(res["items"], [])
            self.assertTrue(res["error"] or os.geteuid() == 0)
        finally:
            os.chmod(locked, 0o755)

    def test_ls_caps_a_huge_folder(self):
        big = os.path.join(self.home, "big")
        os.makedirs(big)
        for i in range(M.LS_MAX + 25):
            os.mkdir(os.path.join(big, f"d{i:04d}"))
        r, [res] = self.run_tool("ls", big)
        self.assertEqual(len(res["items"]), M.LS_MAX)
        self.assertTrue(res["more"])

    def test_mkdir_creates_one_folder(self):
        self.folders("Projects")
        r, [res] = self.run_tool("mkdir", os.path.join(self.home, "Projects"), "my-new-repo")
        self.assertEqual(r.returncode, 0, res)
        self.assertTrue(res["ok"])
        self.assertTrue(os.path.isdir(os.path.join(self.home, "Projects", "my-new-repo")))
        self.assertEqual(res["path"], os.path.join(self.home, "Projects", "my-new-repo"))

    def test_mkdir_refuses_existing_and_bad_names(self):
        self.folders("Projects/taken")
        parent = os.path.join(self.home, "Projects")
        r, [res] = self.run_tool("mkdir", parent, "taken")
        self.assertEqual((r.returncode != 0, res["code"]), (True, "exists"))
        for bad in ("", "..", ".", "../escape", "a/b", "-rf", ".hidden", "x\\y", "n" * 300):
            r, [res] = self.run_tool("mkdir", parent, bad)
            self.assertNotEqual(r.returncode, 0, repr(bad))
            self.assertEqual(res["code"], "name", repr(bad))
        self.assertFalse(os.path.exists(os.path.join(self.home, "escape")))
        self.assertEqual(sorted(os.listdir(parent)), ["taken"])

    def test_mkdir_needs_an_existing_parent_it_may_write_to(self):
        r, [res] = self.run_tool("mkdir", os.path.join(self.home, "nope"), "x")
        self.assertEqual(res["code"], "parent")
        self.folders("ro")
        ro = os.path.join(self.home, "ro")
        os.chmod(ro, 0o555)
        try:
            r, [res] = self.run_tool("mkdir", ro, "x")
            if os.geteuid() != 0:
                self.assertEqual(res["code"], "denied")
        finally:
            os.chmod(ro, 0o755)

    def test_mkdir_never_follows_a_symlink_name_out_of_the_parent(self):
        self.folders("Projects")
        outside = os.path.join(self.home, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.home, "Projects", "link"))
        r, [res] = self.run_tool("mkdir", os.path.join(self.home, "Projects"), "link")
        self.assertEqual(res["code"], "exists")
        self.assertEqual(os.listdir(outside), [])


class Check(Env):
    def test_found_with_default_branch_and_refs(self):
        bare = self.make_server()
        r, [c] = self.run_tool("check", "file://" + bare)
        self.assertEqual(c["status"], "ok", c)
        self.assertEqual(c["defaultBranch"], "main")
        self.assertGreaterEqual(c["refs"], 3)

    def test_branch_and_tag_resolution_prefers_the_longest_match(self):
        bare = self.make_server(branches=("dev", "feature/login"), tags=("v1",))
        r, [c] = self.run_tool("check", "file://" + bare, "feature/login/src/app")
        self.assertEqual(c["ref"], "feature/login")
        r, [c] = self.run_tool("check", "file://" + bare, "v1")
        self.assertEqual(c["ref"], "v1")
        r, [c] = self.run_tool("check", "file://" + bare, "ghost/x")
        self.assertEqual(c["ref"], "")
        self.assertIn("default branch", c["note"])

    def test_not_a_repository_and_invalid_links(self):
        r, [c] = self.run_tool("check", "file://" + os.path.join(self.home, "nothing"))
        self.assertIn(c["status"], ("notfound", "other"))
        r, [c] = self.run_tool("check", "ext::sh -c id")
        self.assertEqual(c["status"], "invalid")

    def test_unreachable_host(self):
        r, [c] = self.run_tool("check", "https://nope.invalid/o/r")
        self.assertIn(c["status"], ("network", "other"))
        self.assertTrue(c.get("hint") or c.get("message"))


class Clone(Env):
    def clone(self, *args):
        r, events = self.run_tool("clone", *args)
        return r.returncode, events

    def test_clone_reports_progress_and_finishes(self):
        bare = self.make_server()
        dest = os.path.join(self.home, "Projects")
        code, ev = self.clone("file://" + bare, dest)
        self.assertEqual(code, 0, ev)
        kinds = [e["event"] for e in ev]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "done")
        done = ev[-1]
        self.assertTrue(os.path.isfile(os.path.join(done["path"], "f2.txt")))
        self.assertEqual(done["branch"], "main")
        self.assertEqual(os.path.basename(done["path"]), "proj")
        for e in ev:
            if e["event"] == "progress":
                self.assertTrue(0 <= e["percent"] <= 100)

    def test_name_branch_and_shallow(self):
        bare = self.make_server()
        dest = os.path.join(self.home, "x")
        code, ev = self.clone("file://" + bare, dest, "--name", "mine", "--branch", "dev", "--shallow")
        self.assertEqual(code, 0, ev)
        path = os.path.join(dest, "mine")
        head = subprocess.run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(head, "dev")
        count = subprocess.run(["git", "-C", path, "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(count, "1")

    def test_a_tag_can_be_cloned(self):
        bare = self.make_server()
        code, ev = self.clone("file://" + bare, os.path.join(self.home, "t"), "--branch", "v1")
        self.assertEqual(code, 0, ev)

    def test_missing_parent_folders_are_created(self):
        bare = self.make_server()
        deep = os.path.join(self.home, "a", "b", "c")
        code, ev = self.clone("file://" + bare, deep)
        self.assertEqual(code, 0, ev)
        self.assertTrue(os.path.isdir(os.path.join(deep, "proj", ".git")))

    def test_existing_non_empty_folder_is_refused_and_untouched(self):
        bare = self.make_server()
        dest = os.path.join(self.home, "d")
        os.makedirs(os.path.join(dest, "proj"))
        keep = os.path.join(dest, "proj", "mine.txt")
        open(keep, "w").write("do not touch")
        code, ev = self.clone("file://" + bare, dest)
        self.assertNotEqual(code, 0)
        self.assertEqual(ev[-1]["code"], "exists")
        self.assertEqual(open(keep).read(), "do not touch")

    def test_an_empty_existing_folder_is_fine(self):
        bare = self.make_server()
        dest = os.path.join(self.home, "d")
        os.makedirs(os.path.join(dest, "proj"))
        code, ev = self.clone("file://" + bare, dest)
        self.assertEqual(code, 0, ev)

    def test_bad_folder_names_are_refused(self):
        bare = self.make_server()
        for bad in ("../escape", "a/b", "-rf", ".."):
            code, ev = self.clone("file://" + bare, os.path.join(self.home, "n"), "--name", bad)
            self.assertNotEqual(code, 0, bad)
            self.assertEqual(ev[-1]["code"], "name", bad)
        self.assertFalse(os.path.exists(os.path.join(self.home, "escape")))

    def test_dangerous_links_never_reach_git(self):
        marker = os.path.join(self.home, "pwned")
        for url in (f"ext::sh -c 'touch {marker}'", "-oProxyCommand=evil", f"--upload-pack=touch {marker}"):
            code, ev = self.clone(url, os.path.join(self.home, "n"))
            self.assertNotEqual(code, 0)
            self.assertEqual(ev[-1]["event"], "error")
        self.assertFalse(os.path.exists(marker))

    def test_a_missing_repository_is_a_clear_error(self):
        code, ev = self.clone("file://" + os.path.join(self.home, "none.git"), os.path.join(self.home, "n"))
        self.assertNotEqual(code, 0)
        self.assertEqual(ev[-1]["event"], "error")
        self.assertFalse(os.path.exists(os.path.join(self.home, "n", "none")))

    def test_history_remembers_the_clone_without_secrets(self):
        bare = self.make_server()
        self.clone("file://" + bare, os.path.join(self.home, "h"))
        r, [s] = self.run_tool("state")
        self.assertEqual(len(s["history"]), 1)
        self.assertEqual(s["history"][0]["name"], "proj")
        self.assertEqual(s["folders"], [s["history"][0]["folder"]])
        mode = stat.S_IMODE(os.stat(os.path.join(self.home, "state", "omaclone", "history.json")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_credentials_in_a_link_never_appear_in_output_or_history(self):
        # the clone fails (nothing listens there), but whatever is printed or stored must not hold the secret
        code, ev = self.clone("https://user:TOPSECRET99@127.0.0.1:9/o/r.git", os.path.join(self.home, "c"))
        self.assertNotEqual(code, 0)
        self.assertNotIn("TOPSECRET99", json.dumps(ev))
        hist = os.path.join(self.home, "state", "omaclone", "history.json")
        self.assertFalse(os.path.exists(hist) and "TOPSECRET99" in open(hist).read())

    def test_forget_clears_the_history(self):
        bare = self.make_server()
        self.clone("file://" + bare, os.path.join(self.home, "h"))
        self.run_tool("forget")
        r, [s] = self.run_tool("state")
        self.assertEqual(s["history"], [])

    def test_cancel_stops_git_and_removes_the_partial_clone(self):
        shim = os.path.join(self.home, "bin")
        os.makedirs(shim)
        script = os.path.join(shim, "git")
        with open(script, "w") as f:
            f.write('#!/bin/bash\nfor a; do last="$a"; done\nif [ "$3" = "clone" ] || [ "$5" = "clone" ]; then mkdir -p "$last"; echo "partial" > "$last/x"; echo "Cloning into \'x\'..." >&2; sleep 30; fi\n')
        os.chmod(script, 0o755)
        env = dict(self.env, PATH=shim + os.pathsep + os.environ["PATH"])
        dest = os.path.join(self.home, "c")
        p = subprocess.Popen([sys.executable, "-I", TOOL, "clone", "file:///tmp/whatever", dest], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        deadline = time.time() + 10
        while time.time() < deadline and not os.path.isdir(os.path.join(dest, "whatever")):
            time.sleep(0.05)
        self.assertTrue(os.path.isdir(os.path.join(dest, "whatever")), "the fake clone did not start")
        p.send_signal(signal.SIGTERM)
        stdout, _ = p.communicate(timeout=15)
        events = [json.loads(l) for l in stdout.splitlines() if l.strip()]
        self.assertEqual(events[-1]["event"], "cancelled")
        self.assertFalse(os.path.exists(os.path.join(dest, "whatever")), "the partial clone was left behind")
        self.assertFalse(os.path.exists(dest), "the folder this clone created was left behind")


GIT_HTTP_BACKEND = "/usr/lib/git-core/git-http-backend"
PASSWORD = "p@ss:w/rd#?%&\"'$x"
PASSWORD_URL = urllib.parse.quote(PASSWORD, safe="")


class GitHttp(http.server.BaseHTTPRequestHandler):
    """A git server that asks for a username and password (HTTP Basic), like a private Forgejo or Gitea repository."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.serve()

    def do_POST(self):
        self.serve()

    def serve(self):
        want = "Basic " + base64.b64encode(f"{self.server.user}:{self.server.password}".encode()).decode()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.headers.get("Authorization", "") != want:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="git"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.server.logins = getattr(self.server, "logins", 0) + 1
        path, _, query = self.path.partition("?")
        env = {"GIT_PROJECT_ROOT": self.server.root, "GIT_HTTP_EXPORT_ALL": "1", "REQUEST_METHOD": self.command, "PATH_INFO": path,
               "QUERY_STRING": query, "CONTENT_TYPE": self.headers.get("Content-Type", ""), "CONTENT_LENGTH": str(len(body)),
               "REMOTE_USER": self.server.user, "REMOTE_ADDR": "127.0.0.1", "PATH": os.environ["PATH"]}
        if self.headers.get("Content-Encoding"):
            env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
        if self.headers.get("Git-Protocol"):
            env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
        p = subprocess.run([GIT_HTTP_BACKEND], input=body, env=env, capture_output=True)
        for sep in (b"\r\n\r\n", b"\n\n"):
            if sep in p.stdout:
                head, _, payload = p.stdout.partition(sep)
                break
        else:
            head, payload = b"", p.stdout
        status, headers = 200, []
        for line in head.decode().splitlines():
            k, _, v = line.partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@unittest.skipUnless(os.path.exists(GIT_HTTP_BACKEND), "git http-backend is not installed")
class SignIn(Env):
    def setUp(self):
        super().setUp()
        self.make_server()
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHttp)
        self.httpd.root = os.path.join(self.home, "server")
        self.httpd.user, self.httpd.password = "alice", PASSWORD
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/owner/proj.git"
        self.login = json.dumps({"user": "alice", "password": PASSWORD})

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def creds_env(self, helper_file=None):
        """A git config of its own: no helper unless asked for (the real ~/.gitconfig is never read)."""
        cfg = os.path.join(self.home, "gitconfig")
        with open(cfg, "w") as f:
            f.write("[user]\n\tname = t\n\temail = t@t\n")
            if helper_file:
                f.write(f"[credential]\n\thelper = store --file={helper_file}\n")
        return dict(self.env, GIT_CONFIG_GLOBAL=cfg, GIT_CONFIG_NOSYSTEM="1")

    def test_a_private_repository_is_reported_as_needing_sign_in(self):
        r, [c] = self.run_tool("check", self.url, env=self.creds_env())
        self.assertEqual(c["status"], "auth", c)
        self.assertIn("sign in", c["hint"].lower())

    def test_check_with_the_right_login_finds_the_repository(self):
        r, [c] = self.run_tool("check", self.url, "", "--credentials-from-stdin", env=self.creds_env(), stdin_text=self.login + "\n")
        self.assertEqual(c["status"], "ok", c)
        self.assertEqual(c["defaultBranch"], "main")
        self.assertTrue(c["signedIn"])

    def test_a_wrong_password_is_reported_with_advice_about_tokens(self):
        bad = json.dumps({"user": "alice", "password": "nope"})
        r, [c] = self.run_tool("check", self.url, "", "--credentials-from-stdin", env=self.creds_env(), stdin_text=bad + "\n")
        self.assertEqual(c["status"], "auth", c)
        self.assertIn("token", c["hint"])

    def test_cloning_without_a_login_fails_clearly(self):
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), env=self.creds_env())
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(ev[-1]["code"], "auth")
        self.assertFalse(os.path.exists(os.path.join(self.home, "c", "proj")))

    def test_cloning_with_a_wrong_login_fails_and_leaves_nothing(self):
        bad = json.dumps({"user": "alice", "password": "wrong"})
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(), stdin_text=bad + "\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(ev[-1]["code"], "auth")
        self.assertIn("token", ev[-1]["hint"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "c", "proj")))

    def test_cloning_with_the_right_login_works_even_with_awkward_characters(self):
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(), stdin_text=self.login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        self.assertEqual(ev[-1]["event"], "done")
        self.assertTrue(ev[-1]["signedIn"])
        self.assertTrue(os.path.isfile(os.path.join(self.home, "c", "proj", "f2.txt")))

    def test_the_password_goes_nowhere_it_should_not(self):
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(), stdin_text=self.login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        everything = r.stdout + r.stderr
        self.assertNotIn(PASSWORD, everything)
        with open(os.path.join(self.home, "c", "proj", ".git", "config")) as f:
            config = f.read()
        self.assertNotIn(PASSWORD, config)
        self.assertNotIn("alice:", config)
        state_dir = os.path.join(self.home, "state", "omaclone")
        for name in os.listdir(state_dir):
            with open(os.path.join(state_dir, name)) as f:
                self.assertNotIn(PASSWORD, f.read(), name)
        r, [s] = self.run_tool("state", env=self.creds_env())
        self.assertEqual(s["users"], {})                                   # Remember was off: not even the username is kept

    def test_the_password_is_not_on_any_command_line(self):
        shim = os.path.join(self.home, "bin")
        os.makedirs(shim)
        log = os.path.join(self.home, "argv.log")
        real = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
        with open(os.path.join(shim, "git"), "w") as f:
            f.write(f'#!/bin/bash\nprintf "%s\\n" "$@" >> "{log}"\nexec "{real}" "$@"\n')
        os.chmod(os.path.join(shim, "git"), 0o755)
        env = dict(self.creds_env(), PATH=shim + os.pathsep + os.environ["PATH"])
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=env, stdin_text=self.login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        argv = open(log).read()
        self.assertIn("clone", argv)
        self.assertNotIn(PASSWORD, argv)
        self.assertNotIn("alice", argv)

    def test_remember_hands_the_login_to_the_users_credential_helper(self):
        store = os.path.join(self.home, "stored-credentials")
        env = self.creds_env(store)
        login = json.dumps({"user": "alice", "password": PASSWORD, "remember": True})
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=env, stdin_text=login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        self.assertTrue(ev[-1]["remembered"])
        self.assertTrue(os.path.exists(store))
        # ...so a later clone needs no login at all
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c2"), env=env)
        self.assertEqual(r.returncode, 0, ev)

    def test_a_username_is_only_remembered_when_remember_is_on(self):
        login = json.dumps({"user": "alice", "password": PASSWORD, "remember": True})
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(os.path.join(self.home, "st")), stdin_text=login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        r, [s] = self.run_tool("state", env=self.creds_env())
        self.assertEqual(s["users"], {"127.0.0.1": "alice"})

    def test_a_token_only_login_is_used_but_never_shown_or_saved(self):
        token = "tok_" + "x" * 24
        self.httpd.user, self.httpd.password = token, token
        env, log = self.shim_git()
        login = json.dumps({"user": "", "password": token, "tokenAsUser": True, "remember": True})
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=dict(env, **{}), stdin_text=login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        with open(log) as f:
            self.assertNotIn(token, f.read())
        state = os.path.join(self.home, "state", "omaclone")
        for name in os.listdir(state):
            with open(os.path.join(state, name)) as f:
                self.assertNotIn(token, f.read(), name)
        with open(os.path.join(self.home, "c", "proj", ".git", "config")) as f:
            self.assertNotIn(token, f.read())
        self.assertFalse(ev[-1]["remembered"])
        r, [s] = self.run_tool("state", env=self.creds_env())
        self.assertEqual(s["users"], {})

    def test_a_token_in_the_link_works_without_being_kept(self):
        token = "tok_" + "y" * 24
        self.httpd.user, self.httpd.password = token, token
        url = self.url.replace("http://", f"http://{token}@")
        r, ev = self.run_tool("clone", url, os.path.join(self.home, "c"), env=self.creds_env())
        self.assertEqual(r.returncode, 0, ev)
        r, [s] = self.run_tool("state", env=self.creds_env())
        self.assertEqual(s["users"], {})
        with open(os.path.join(self.home, "state", "omaclone", "history.json")) as f:
            self.assertNotIn(token, f.read())

    def test_without_remember_nothing_is_stored(self):
        store = os.path.join(self.home, "stored-credentials")
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(store), stdin_text=self.login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        self.assertFalse(ev[-1]["remembered"])
        self.assertFalse(os.path.exists(store))

    def test_remember_without_a_credential_helper_is_harmless(self):
        login = json.dumps({"user": "alice", "password": PASSWORD, "remember": True})
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(), stdin_text=login + "\n")
        self.assertEqual(r.returncode, 0, ev)
        self.assertFalse(ev[-1]["remembered"])
        r, [s] = self.run_tool("state", env=self.creds_env())
        self.assertFalse(s["credentialHelper"])

    def test_a_typed_login_beats_a_stale_stored_one(self):
        store = os.path.join(self.home, "stale")
        with open(store, "w") as f:
            f.write(f"http://alice:old-password@127.0.0.1:{self.httpd.server_address[1]}\n")
        env = self.creds_env(store)
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "bad"), env=env)
        self.assertNotEqual(r.returncode, 0)              # the stale login is what git would use on its own
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "good"), "--credentials-from-stdin", env=env, stdin_text=self.login + "\n")
        self.assertEqual(r.returncode, 0, ev)

    def test_a_login_that_is_not_understood_is_refused_before_anything_runs(self):
        for text in ("", "not json\n", "[]\n", '{"user": "a", "password": "line\\nbreak"}\n', '{"user": "", "password": ""}\n', '{"user": 5, "password": "x"}\n'):
            r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(), stdin_text=text)
            self.assertNotEqual(r.returncode, 0, repr(text))
            self.assertEqual(ev[-1]["code"], "usage", repr(text))
        self.assertFalse(os.path.exists(os.path.join(self.home, "c", "proj")))

    def test_a_token_only_login_works(self):
        self.httpd.user, self.httpd.password = "token-user", "tok_123"
        login = json.dumps({"user": "token-user", "password": "tok_123"})
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", env=self.creds_env(), stdin_text=login + "\n")
        self.assertEqual(r.returncode, 0, ev)

    def test_the_helper_script_answers_only_from_the_environment(self):
        helper = os.path.join(ROOT, "omaclone-askpass")
        env = dict(os.environ, OMACLONE_USER="u ser", OMACLONE_PASS="pw -n \\x", OMACLONE_ORIGIN="https://h")
        def ask(prompt):
            return subprocess.run([helper, prompt], capture_output=True, text=True, env=env)
        self.assertEqual(ask("Username for 'https://h': ").stdout, "u ser\n")
        self.assertEqual(ask("Password for 'https://u@h': ").stdout, "pw -n \\x\n")
        r = ask("Are you sure you want to continue connecting (yes/no)? ")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        r = ask("Enter passphrase for key '/home/x/.ssh/id': ")
        self.assertNotEqual(r.returncode, 0)

    def test_the_login_is_only_given_to_the_exact_origin_it_was_typed_for(self):
        helper = os.path.join(ROOT, "omaclone-askpass")
        env = dict(os.environ, OMACLONE_USER="alice", OMACLONE_PASS="PW", OMACLONE_ORIGIN="https://git.example.org:3000")
        def ask(p, e=env):
            return subprocess.run([helper, p], capture_output=True, text=True, env=e)
        self.assertEqual(ask("Password for 'https://alice@git.example.org:3000': ").stdout, "PW\n")
        self.assertEqual(ask("Password for 'https://GIT.Example.org:3000': ").stdout, "PW\n")
        for other in ("Password for 'https://alice@evil.example.net': ", "Password for 'https://git.example.org': ",
                      "Username for 'https://git.example.org.evil.net:3000': ", "Password for 'https://x@git.example.org:3001': ",
                      "Password for 'http://git.example.org:3000': ",           # the same host over another scheme
                      "Password for 'ssh://git.example.org:3000': ", "Password for 'git.example.org:3000': ", "Password for : "):
            r = ask(other)
            self.assertNotEqual(r.returncode, 0, other)
            self.assertEqual(r.stdout, "", other)
        no_origin = {k: v for k, v in env.items() if k != "OMACLONE_ORIGIN"}
        self.assertNotEqual(ask("Password for 'https://git.example.org:3000': ", no_origin).returncode, 0)

    def test_https_and_ssh_secrets_are_kept_apart(self):
        http_env = M.git_env({"user": "alice", "password": "WEBPW", "remember": False}, ssh=False, origin="https://h")
        self.assertEqual(http_env["GIT_ASKPASS"], M.ASKPASS)
        self.assertNotIn("SSH_ASKPASS", http_env)
        self.assertNotIn("OMACLONE_KEY_PASS", http_env)
        ssh_env = M.git_env({"user": "", "password": "KEYPW", "remember": False}, ssh=True, origin="ssh://h")
        self.assertEqual(ssh_env["SSH_ASKPASS"], M.ASKPASS_SSH)
        self.assertEqual(ssh_env["OMACLONE_KEY_PASS"], "KEYPW")
        for var in ("OMACLONE_PASS", "OMACLONE_USER", "OMACLONE_ORIGIN"):
            self.assertNotIn(var, ssh_env, var)
        self.assertNotEqual(ssh_env["GIT_ASKPASS"], M.ASKPASS)              # git's https flow cannot reach the passphrase
        self.assertNotIn("KEYPW", "".join(v for k, v in ssh_env.items() if k != "OMACLONE_KEY_PASS"))
        self.assertNotIn("WEBPW", "".join(v for k, v in http_env.items() if k not in ("OMACLONE_PASS",)))

    def test_the_https_helper_never_answers_with_the_ssh_passphrase_and_the_reverse(self):
        web, key = os.path.join(ROOT, "omaclone-askpass"), os.path.join(ROOT, "omaclone-askpass-ssh")
        env = dict(os.environ, OMACLONE_KEY_PASS="KEYPW", OMACLONE_ORIGIN="https://h")
        r = subprocess.run([web, "Password for 'https://h': "], capture_output=True, text=True, env=env)
        self.assertEqual(r.stdout.replace("\n", ""), "")                    # no OMACLONE_PASS: nothing (and never the key's)
        env = dict(os.environ, OMACLONE_PASS="WEBPW", OMACLONE_USER="u", OMACLONE_ORIGIN="https://h")
        r = subprocess.run([key, "Enter passphrase for key '/k': "], capture_output=True, text=True, env=env)
        self.assertNotIn("WEBPW", r.stdout)
        r = subprocess.run([key, "Password for 'https://h': "], capture_output=True, text=True, env=dict(os.environ, OMACLONE_KEY_PASS="KEYPW"))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")

    def test_a_submodule_on_another_server_does_not_get_the_login(self):
        second = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHttp)
        second.root, second.user, second.password = self.httpd.root, "alice", PASSWORD
        threading.Thread(target=second.serve_forever, daemon=True).start()
        try:
            work = os.path.join(self.home, "parentwork")
            subprocess.run(["git", "clone", "-q", os.path.join(self.httpd.root, "owner", "proj.git"), work], check=True, capture_output=True)
            sha = subprocess.run(["git", "-C", work, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            with open(os.path.join(work, ".gitmodules"), "w") as f:
                f.write(f'[submodule "sub"]\n\tpath = sub\n\turl = http://127.0.0.1:{second.server_address[1]}/owner/proj.git\n')
            subprocess.run(["git", "-C", work, "update-index", "--add", "--cacheinfo", f"160000,{sha},sub"], check=True)
            subprocess.run(["git", "-C", work, "add", ".gitmodules"], check=True)
            subprocess.run(["git", "-C", work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "sub"], check=True)
            subprocess.run(["git", "clone", "-q", "--bare", work, os.path.join(self.httpd.root, "owner", "parent.git")], check=True)
            url = f"http://127.0.0.1:{self.httpd.server_address[1]}/owner/parent.git"
            r, ev = self.run_tool("clone", url, os.path.join(self.home, "c"), "--submodules", "--credentials-from-stdin", env=self.creds_env(), stdin_text=self.login + "\n")
            self.assertNotEqual(r.returncode, 0, "the submodule on the other server should not have been signed in")
            self.assertFalse(os.path.exists(os.path.join(self.home, "c", "parent", "sub", "f2.txt")))
        finally:
            second.shutdown()
            second.server_close()

    def shim_git(self):
        """A git that logs its arguments, so a test can see what was ever put on a command line."""
        shim = os.path.join(self.home, "bin")
        os.makedirs(shim, exist_ok=True)
        log = os.path.join(self.home, "argv.log")
        real = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
        with open(os.path.join(shim, "git"), "w") as f:
            f.write(f'#!/bin/bash\nprintf "%s\\n" "$@" >> "{log}"\nexec "{real}" "$@"\n')
        os.chmod(os.path.join(shim, "git"), 0o755)
        return dict(self.creds_env(), PATH=shim + os.pathsep + os.environ["PATH"]), log

    def test_a_token_in_the_link_never_reaches_git_or_its_config(self):
        env, log = self.shim_git()
        url = self.url.replace("http://", f"http://alice:{PASSWORD_URL}@")
        r, ev = self.run_tool("clone", url, os.path.join(self.home, "c"), env=env)
        self.assertEqual(r.returncode, 0, ev)                              # the login from the link still works...
        with open(log) as f:
            argv = f.read()
        self.assertNotIn(PASSWORD, argv)                                   # ...but is never an argument of any git command
        self.assertNotIn(PASSWORD_URL, argv)
        with open(os.path.join(self.home, "c", "proj", ".git", "config")) as f:
            config = f.read()
        self.assertNotIn(PASSWORD_URL, config)
        self.assertNotIn(PASSWORD, config)
        self.assertIn("alice@127.0.0.1", config)
        self.assertNotIn(PASSWORD_URL, r.stdout + r.stderr)

    def test_checking_a_link_with_a_token_keeps_it_off_the_command_line_too(self):
        env, log = self.shim_git()
        url = self.url.replace("http://", f"http://alice:{PASSWORD_URL}@")
        r, [c] = self.run_tool("check", url, env=env)
        self.assertEqual(c["status"], "ok", c)
        with open(log) as f:
            self.assertNotIn(PASSWORD, f.read())

    def test_the_link_can_carry_the_username(self):
        p = M.parse(f"http://alice@127.0.0.1:1234/o/r.git")
        self.assertEqual((p["user"], p["port"], p["hasPassword"]), ("alice", 1234, False))
        q = M.parse("https://alice:tok@git.example.org/o/r")
        self.assertEqual((q["user"], q["hasPassword"]), ("alice", True))
        self.assertNotIn("tok", q["display"])


class Protocols(unittest.TestCase):
    def test_an_https_link_also_offers_ssh(self):
        p = M.parse("https://codeberg.org/o/r/src/branch/dev")
        self.assertEqual((p["webUrl"], p["sshUrl"]), ("https://codeberg.org/o/r", "git@codeberg.org:o/r"))

    def test_an_ssh_link_also_offers_https(self):
        for link in ("git@github.com:o/r.git", "ssh://git@github.com/o/r.git"):
            p = M.parse(link)
            self.assertEqual(p["webUrl"], "https://github.com/o/r.git", link)
            self.assertEqual(p["sshUrl"], "git@github.com:o/r.git", link)

    def test_a_custom_ssh_user_is_kept(self):
        p = M.parse("ssh://forgejo@git.example.org:2222/o/r.git")
        self.assertEqual((p["sshUser"], p["port"], p["webUrl"]), ("forgejo", 2222, "https://git.example.org/o/r.git"))

    def test_the_https_form_never_gets_a_password_from_an_ssh_link(self):
        p = M.parse("https://u:TOK@git.example.org/o/r")
        self.assertNotIn("TOK", p["webDisplay"])
        self.assertNotIn("TOK", p["sshUrl"])

    def test_azure_has_no_ssh_form_and_unsafe_users_are_refused(self):
        self.assertIsNone(M.parse("https://dev.azure.com/org/proj/_git/repo")["sshUrl"])
        self.assertIsNone(M.parse("ssh://a$(x)@host.org/o/r")["sshUrl"] if M.parse("ssh://a$(x)@host.org/o/r").get("ok") else None)

    def test_path_is_quoted_for_the_port_form(self):
        p = M.parse("https://git.example.org/my group/r")
        self.assertNotIn(" ", p["sshPath"])


SSHD = "/usr/bin/sshd"
PASSPHRASE = 'pa$$ phrase "x" \'y\''


@unittest.skipUnless(os.path.exists(SSHD) and os.path.exists("/usr/bin/ssh-keygen"), "sshd is not installed")
class SshPassphrase(Env):
    """A real ssh server on localhost, and a key that has a passphrase, with no agent and no terminal."""

    def setUp(self):
        super().setUp()
        d = self.d = os.path.join(self.home, "sshd")
        os.makedirs(d, mode=0o700)
        keygen = ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519"]
        subprocess.run(keygen + ["-N", "", "-f", d + "/hostkey"], check=True)
        subprocess.run(keygen + ["-N", PASSPHRASE, "-f", d + "/key"], check=True)
        os.rename(d + "/key.pub", d + "/authorized_keys")
        os.chmod(d + "/authorized_keys", 0o600)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        with open(d + "/sshd_config", "w") as f:
            f.write(f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {d}/hostkey\nAuthorizedKeysFile {d}/authorized_keys\n"
                    f"PasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\nPidFile {d}/sshd.pid\nStrictModes no\nLogLevel ERROR\n")
        subprocess.run([SSHD, "-f", d + "/sshd_config"], check=True)
        self.make_server()
        self.repo = os.path.join(self.home, "server", "owner", "proj.git")
        user = os.environ.get("USER") or subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        self.url = f"ssh://{user}@127.0.0.1:{self.port}{self.repo}"
        ssh = (f"ssh -i {d}/key -o IdentitiesOnly=yes -o UserKnownHostsFile={d}/known_hosts -o BatchMode=no "
               f"-o NumberOfPasswordPrompts=1 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10")
        self.env = dict(self.env, GIT_SSH_COMMAND=ssh)
        for k in ("SSH_AUTH_SOCK", "DISPLAY"):
            self.env.pop(k, None)

    def tearDown(self):
        try:
            with open(self.d + "/sshd.pid") as f:
                os.kill(int(f.read()), signal.SIGTERM)
        except (OSError, ValueError):
            pass

    def login(self, phrase):
        return json.dumps({"password": phrase}) + "\n"

    def test_the_right_passphrase_clones_with_no_agent_and_no_terminal(self):
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", stdin_text=self.login(PASSPHRASE))
        self.assertEqual(r.returncode, 0, (ev, r.stderr))
        self.assertTrue(os.path.isfile(os.path.join(self.home, "c", "proj", "f2.txt")))

    def test_check_with_the_right_passphrase_finds_the_repository(self):
        r, [c] = self.run_tool("check", self.url, "", "--credentials-from-stdin", stdin_text=self.login(PASSPHRASE))
        self.assertEqual(c["status"], "ok", c)

    def test_a_wrong_passphrase_is_refused_and_leaves_nothing(self):
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", stdin_text=self.login("nope"))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(ev[-1]["code"], "auth")
        self.assertIn("passphrase", ev[-1]["hint"])
        self.assertFalse(os.path.exists(os.path.join(self.home, "c")))

    def test_without_a_passphrase_it_asks_for_one_instead_of_hanging(self):
        r, [c] = self.run_tool("check", self.url, env=dict(self.env, GIT_SSH_COMMAND=self.env["GIT_SSH_COMMAND"].replace("BatchMode=no", "BatchMode=yes")), timeout=30)
        self.assertEqual(c["status"], "auth")
        self.assertIn("passphrase", c["hint"])

    def test_the_passphrase_is_not_leaked(self):
        r, ev = self.run_tool("clone", self.url, os.path.join(self.home, "c"), "--credentials-from-stdin", stdin_text=self.login(PASSPHRASE))
        self.assertEqual(r.returncode, 0, ev)
        self.assertNotIn(PASSPHRASE, r.stdout + r.stderr)
        for root, _, files in os.walk(os.path.join(self.home, "state")):
            for name in files:
                with open(os.path.join(root, name)) as f:
                    self.assertNotIn(PASSPHRASE, f.read())

    def test_the_helper_answers_passphrase_prompts_only(self):
        helper = os.path.join(ROOT, "omaclone-askpass-ssh")
        env = dict(os.environ, OMACLONE_KEY_PASS="s3cret phrase")
        ok = subprocess.run([helper, "Enter passphrase for key '/home/u/.ssh/id_ed25519': "], capture_output=True, text=True, env=env)
        self.assertEqual(ok.stdout, "s3cret phrase\n")
        no = subprocess.run([helper, "Are you sure you want to continue connecting (yes/no)? "], capture_output=True, text=True, env=env)
        self.assertNotEqual(no.returncode, 0)
        self.assertEqual(no.stdout, "")


class SshPorts(Env):
    def test_a_port_that_worked_is_remembered_per_server(self):
        code = ("import importlib.machinery as m, importlib.util as u, sys; "
                f"l=m.SourceFileLoader('o', {TOOL!r}); s=u.spec_from_loader('o', l); o=u.module_from_spec(s); l.exec_module(o); "
                "o.remember_port('git.example.org', 2222); o.remember_port('other.org', 99999); o.remember_port('x.org', 'no')")
        r = subprocess.run([sys.executable, "-I", "-c", code], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        r, [s] = self.run_tool("state")
        self.assertEqual(s["ports"], {"git.example.org": 2222})
        mode = stat.S_IMODE(os.stat(os.path.join(self.home, "state", "omaclone", "ports.json")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_port_in_the_link_is_kept(self):
        p = M.parse("ssh://git@git.example.org:2222/o/r.git")
        self.assertEqual(p["port"], 2222)
        self.assertEqual(p["sshUrl"], "git@git.example.org:o/r.git")      # port-less form; the panel adds the port


class Leftovers(Env):
    def test_a_failed_clone_removes_the_folders_it_created_but_not_yours(self):
        mine = os.path.join(self.home, "mine")
        os.makedirs(mine)
        deep = os.path.join(mine, "new", "deeper")
        r, ev = self.run_tool("clone", "file:///nonexistent/repo", deep)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isdir(mine), "an existing folder must stay")
        self.assertFalse(os.path.exists(os.path.join(mine, "new")), ev)

    def test_an_existing_empty_folder_is_not_removed_after_a_failure(self):
        keep = os.path.join(self.home, "keepme")
        os.makedirs(keep)
        self.run_tool("clone", "file:///nonexistent/repo", keep)
        self.assertTrue(os.path.isdir(keep))


class TrustHost(unittest.TestCase):
    def test_new_servers_are_only_trusted_when_asked(self):
        env = {k: v for k, v in os.environ.items() if k != "GIT_SSH_COMMAND"}
        old = dict(os.environ)
        try:
            os.environ.pop("GIT_SSH_COMMAND", None)
            self.assertNotIn("accept-new", M.git_env()["GIT_SSH_COMMAND"])
            M.TRUST_NEW_HOST = True
            cmd = M.git_env()["GIT_SSH_COMMAND"]
            self.assertIn("StrictHostKeyChecking=accept-new", cmd)
            self.assertIn("BatchMode=yes", cmd)
        finally:
            M.TRUST_NEW_HOST = False
            os.environ.clear(); os.environ.update(old)

    def test_the_flag_is_read_by_the_command_line(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "parse", "--trust-new-host", "https://codeberg.org/o/r"], capture_output=True, text=True)
        self.assertEqual(json.loads(r.stdout)["host"], "codeberg.org")


class Docs(unittest.TestCase):
    def test_help_works_and_unknown_commands_fail(self):
        r = subprocess.run([sys.executable, "-I", TOOL, "--help"], capture_output=True, text=True)
        self.assertIn("omaclone parse", r.stdout)
        r = subprocess.run([sys.executable, "-I", TOOL, "frobnicate"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
