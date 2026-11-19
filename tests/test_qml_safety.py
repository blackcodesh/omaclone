"""Static checks on Panel.qml: link text from a server or the clipboard never renders as rich text, and nothing the person
pasted becomes part of a shell command line."""
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
SRC = open(os.path.join(ROOT, "Panel.qml")).read()


class PanelSafety(unittest.TestCase):
    def test_every_text_item_is_plain_text(self):
        for m in re.finditer(r"(?<![\w.])Text\s*\{", SRC):
            body = SRC[m.end(): m.end() + 400]
            own = body.split("\n}", 1)[0]
            self.assertIn("textFormat: Text.PlainText", own[:400], "a Text item without textFormat: Text.PlainText near: " + SRC[m.start(): m.start() + 80])

    def test_commands_are_argument_lists_never_built_strings(self):
        self.assertNotRegex(SRC, r"execDetached\(\s*[\"'][^\"']*\+", "a command string built by concatenation")
        self.assertNotIn("sh -c", SRC.replace('bash", "-lc"', ""))
        # every terminal script is a fixed single-quoted string; the link and folder reach it as $1 and $2
        scripts = re.findall(r'"bash", "-lc",\s*\n?\s*\'([^\']*)\'', SRC)
        self.assertGreaterEqual(len(scripts), 2)
        for script in scripts:
            self.assertNotIn("+", script)
        self.assertTrue(any('git clone -- "$1" "$2"' in script for script in scripts))

    def test_the_script_is_called_with_the_isolated_python_flag(self):
        self.assertIn('["python3", "-I", root.toolPath]', SRC)

    def test_the_clipboard_is_filtered_before_it_reaches_a_command(self):
        self.assertIn("root.looksLikeLink(text)", SRC)
        self.assertLess(SRC.index("root.looksLikeLink(text)"), SRC.index('root.tool(["parse", text]', SRC.index("function readClipboard")))

    def test_clipboard_is_only_used_when_it_is_a_git_link(self):
        self.assertRegex(SRC, r"p && p\.ok && root\.url\.trim\(\) === \"\"")


if __name__ == "__main__":
    unittest.main()
