# OmaClone: clone any git repository from the Omarchy bar

![OmaClone: paste a git link, pick a folder, clone](poster/13-blackcodesh-omaclone-detail.webp)

Copy a repository link in your browser, click the branch icon in the bar, pick where it should go, and press **Clone**.
No typing `git clone`, no remembering the URL form. It works with **GitHub, Forgejo, Codeberg, GitLab, Gitea,
Bitbucket, SourceHut and any other git server**, because underneath it is plain `git clone`.

**Compatibility:** works with Omarchy 4.5 (checked on 4.5.0-3: manifest validation, the plugin's own tests, a live run on a 4.5 bar, and real clones from GitHub and Codeberg).

## What's new in 1.0.4

- A typed login is bound to its origin for every web link, Azure DevOps included (these have no SSH form, so they used to skip the binding). A login that is not bound to the link being cloned is never used.
- Saved usernames from versions before 1.0.3 are discarded once: a token could have been saved there. Usernames are saved again only when you switch on "Remember this login".

## What's new in 1.0.3

More credential fixes from the marketplace review:

- A typed login is bound to its exact origin (scheme, host and port). If any of them changes, the login is dropped from the form, and a sign-in that is still on its way is cancelled when the link, protocol or port changes.
- A token that is the whole login is never copied into the username field, and is never saved.
- A username is remembered only when you switch on "Remember this login" (it used to be saved after every signed-in clone).
- Documented: keep a password or token out of a URL you type on a command line (use `parse --stdin` and `--credentials-from-stdin`).

## What's new in 1.0.2

Follow-up credential fixes from the marketplace review:

- **One parser, secrets out first.** A password or token in a link (every form: `user:token@`, an `@` or encoded characters in the password, upper-case scheme, `git clone …` lines, a bare token) is taken out by the backend before anything is shown, passed to a process or written to git config. The panel sends links to the parser on stdin, never as an argument. A password that cannot be told from a path (an unencoded `/`, `?` or `#`) is refused and not echoed.
- **Secrets are kept apart by protocol and exact origin.** An https login is given only to the exact origin it was typed for (scheme, host and port); an SSH key passphrase goes only to SSH's own passphrase prompt (`omaclone-askpass-ssh`) and never to git's https flow.
- The remote of the cloned repository never contained the token to begin with (no rewrite step that could fail).

## What's new in 1.0.1

Credential handling, after a review of 1.0.0:

- A login you type is given only to the server it was typed for: a submodule on another server no longer receives it.
- An SSH key passphrase and an https password are separate, so switching from SSH to HTTPS can never send the passphrase to a server.
- A token pasted inside a link is moved into the sign-in card at once (it no longer shows in the link field or on a command line), and the cloned repository's remote no longer keeps it.
- Fixed: a token with special characters (`@`, `%`, `/`) in a link was encoded twice, so sign-in failed.

## What's new in 1.0.0

First release.

## How it works

1. **Copy a link** in your browser: the repository page, a folder or file inside it, a pull request, a `git clone …`
   line from a README. Any of them.
2. **Click the branch icon** in the bar. If the clipboard holds a git link, it is already in the field.
3. OmaClone **cleans the link up** and asks the server about it (`git ls-remote`): *Found · default branch main · 3
   branches and tags*, or *Needs sign-in*, *Not found*, *Cannot reach it*, each with what to do next.
4. **Pick the folder**: type a path (it suggests folders as you type and remembers the ones you used), or press
   **Browse…** to click through your folders: **↑ Up**, **Home**, **New folder** (type a name and press Create, you are
   taken into it) and **Hidden** to show dot-folders. **Use this folder** fills the field. Adjust the name or branch if you
   like, and press **Clone** (or Enter in the folder field). A folder that does not exist yet is created for you.
5. Watch the **progress** (connecting, receiving objects with the speed, resolving deltas) and **Cancel** any time:
   a cancelled clone leaves nothing behind.
6. When it finishes: **Open folder**, **Terminal** or **Editor** with one click, or **Clone another**.

## Links it understands

| You paste | It clones |
|-----------|-----------|
| `https://github.com/owner/repo` | that repository |
| `https://github.com/owner/repo/tree/dev/src/lib` | the repository, branch `dev` |
| `https://github.com/owner/repo/pull/42/files`, `/issues/7`, `/releases`, `/wiki` … | the repository |
| `https://codeberg.org/owner/repo/src/branch/main/docs` (Forgejo, Gitea) | the repository, branch `main` |
| `https://gitlab.com/group/sub/repo/-/tree/dev/src` (GitLab, with subgroups) | `group/sub/repo`, branch `dev` |
| `https://bitbucket.org/owner/repo/src/main/` | the repository, branch `main` |
| `https://git.sr.ht/~user/repo/tree/main/item/README` | the repository |
| `https://dev.azure.com/org/project/_git/repo` | the repository |
| `git@github.com:owner/repo.git`, `ssh://git@host:2222/owner/repo.git`, `git://host/repo.git` | that repository over SSH or git |
| `git clone -b dev --depth 1 https://github.com/owner/repo` | the repository (it reads the link out of the command) |
| `github.com/owner/repo` (no `https://`) | that repository |
| `https://user:TOKEN@git.example.org/owner/repo.git` | that repository, signed in with the token |

The branch in a browser link is checked against the server, so a branch with a slash in its name
(`feature/login`) is found too. A tag works as well as a branch. A link to a single commit clones the
default branch.

## HTTPS or SSH

Under the link there is a **Connect with HTTPS / SSH** switch. Paste either kind of link and switch to the other: an
`https://…` link becomes `git@host:owner/repo`, and a `git@…` link becomes `https://host/owner/repo`. A server with its
SSH on another port (common for self-hosted Forgejo and Gitea) has a **port** box next to SSH. The server is checked
again after each change. The first time you use SSH with a server, a **Trust this server** button records its key
(what answering "yes" in a terminal does; a key that later changes is still refused). SSH uses your own keys.

## Private repositories

When a repository needs a login (a private Forgejo, Gitea, GitLab or GitHub repository), OmaClone shows a **Sign in**
card right in the popup, no terminal needed:

1. Type your **username** and **password or access token** (many servers, GitHub included, want a token rather than your
   account password). The username is filled in from the link, or from the last time you used that server.
2. Press **Sign in and clone**. The login is checked against the server first, so a wrong password is reported on the
   spot and nothing is created.
3. Optional: if you have a git credential helper (`git credential-manager`, libsecret, `gh auth setup-git`), a
   **Remember this login** switch hands it to that helper so `git pull` works later. Otherwise nothing is remembered.

**Over SSH** it uses your own key. If the key has a **passphrase**, the card asks for it (**Unlock and clone**): no
`ssh-agent` and no terminal needed, it is used for that one clone and never saved. The key must already be added to your
account on the server.

Other ways still work: an **SSH link** (`git@host:owner/repo.git`) with your SSH key, a **token in the link**
(`https://user:TOKEN@host/owner/repo.git`): the panel moves it into the sign-in card at once, so it is never shown in the
field or passed on a command line, or **Clone in a terminal**.

A login is only ever given to the server it was typed for: if a repository has a submodule on another server, that
server gets nothing. After a clone with a token in the link, the repository's remote is rewritten without the token.

## Settings

Set them in the widget's entry in `~/.config/omarchy/shell.json`, or in Omarchy's bar settings.

| Setting | Values | Default |
|---------|--------|---------|
| `defaultFolder` | a folder, e.g. `~/Code` | empty: the folder you used last, else `~/Projects` if it exists, else your home folder |
| `afterClone` | `Nothing`, `Open folder`, `Open in terminal`, `Open in editor` | `Nothing` |

## For scripts and key bindings

```bash
omarchy-shell blackcode.omaclone toggle                       # open or close the popup
omarchy-shell omaclone fill "https://github.com/owner/repo"   # open it with a link filled in
omarchy-shell omaclone clone "https://github.com/owner/repo" ~/Code   # start cloning
omarchy-shell omaclone cancel                                 # cancel the running clone
omarchy-shell omaclone browse ~/Code                          # open the popup with the folder browser at a folder
omarchy-shell omaclone newFolder my-repos                     # make a folder in the browser's current folder
omarchy-shell omaclone useFolder                              # use the browser's current folder
```

For example, a shortcut that opens it: in `~/.config/hypr/bindings.lua`

```lua
o.bind("SUPER + ALT + G", "Clone a repository", "omarchy-shell blackcode.omaclone toggle")
```

The backend works on its own too: `omaclone parse <link>`, `omaclone check <link>`, `omaclone clone <link> <folder>`,
`omaclone ls <folder>`, `omaclone mkdir <parent> <name>` (see `omaclone --help`), printing JSON.

**Keep a password or token out of a URL you type on a command line**: other programs on this computer can read a command
line while it runs. For scripts use `omaclone parse --stdin` (the link on stdin) and `--credentials-from-stdin` (one JSON
line: `{"user": "...", "password": "...", "remember": false}`). A login that is in a link is taken out of it and used
privately, but the argument itself was already visible to the system. The panel always uses stdin.

## Privacy and safety

- **What it contacts:** only the server in the link you paste, with `git ls-remote` (and `git clone` when you press
  Clone). Nothing else, no accounts, no telemetry.
- **The clipboard** is read when the popup opens. Text that does not already look like a git link (a copied password,
  say) is dropped right there: it is never passed to anything, shown or stored.
- **Links that can run commands are refused**: `ext::…`, `fd::…`, anything starting with `-`, `file://` and local
  paths, `ftp://` and the like. Only `https`, `http`, `ssh` and `git` links are cloned, and git is told not to allow the
  `ext` and `fd` transports at all.
- **Folder names** must be one plain name: no slashes, no `..`, no leading `-` (and no leading dot for a new folder
  made in the browser). An existing folder that is not empty is never touched. The browser only lists folders and
  creates one folder at a time, inside the folder you are looking at.
- **Passwords**: what you type in the Sign in card is sent to the script over a pipe, never on a command line, and git
  reads it through a tiny helper (`omaclone-askpass`) from the environment of that one clone. It is not put in the
  link, not written to the cloned repository's config, not in the history, and cleared from the popup when the clone
  ends. Nothing is saved unless you switch on "Remember this login": then the username (never the password) is kept per server, and the login goes to your git credential helper. A token that is the whole login is never saved or shown as a username. A password or token you put in a link is taken out of it by the
  backend before anything else sees the link (the panel sends links on stdin), and travels the same private way, so it is
  never in the field, on a command line or in the repository's remote, even when you run `omaclone` yourself. A login is
  given only to the exact origin it was typed for, and an SSH key passphrase is kept apart from passwords, with its own helper.
- **History** (`~/.local/state/omaclone/history.json`, mode 0600) keeps the last 20 clones: hidden link, folder, time.
  `omaclone forget` clears it.
- SSH uses `BatchMode`: it never waits for a password or a host-key question you cannot see. An unknown server is
  reported, with the fix.

## Install

```bash
omarchy plugin add https://github.com/blackcodesh/omaclone --enable
```

That is all: nothing to set up, bind or configure. It needs `git` (already part of Omarchy) and, for the clipboard
fill, `wl-paste` (also part of Omarchy).

## Remove

```bash
omarchy plugin remove blackcode.omaclone
rm -rf ~/.local/state/omaclone        # optional: the history
```

## Tests

```bash
python3 -I -m unittest discover -s tests -p "test_*.py"
```

They parse links from many providers, check the safety refusals, test the folder browser (listing, hidden folders, huge
folders, unreadable folders, creating folders and refusing bad names), and run real clones, branches, shallow clones,
cancels and errors against a local repository standing in for a server, plus a local git server that asks for a username and
password (right and wrong logins, awkward characters, no leaks of the password), all in throwaway folders. No network is needed.

## Files

| File | What it is |
|------|------------|
| `manifest.json`, `Panel.qml`, `icon.svg` | The bar button and popup |
| `omaclone-askpass`, `omaclone-askpass-ssh` | Hand the typed https login (exact origin only) or SSH key passphrase to git/ssh for one clone |
| `omaclone` | The backend (Python): link cleanup, `git ls-remote` check, `git clone` with progress, folder suggestions, the folder browser (`ls`, `mkdir`), history |
| `tests/` | The tests |
| `poster/`, `preview.png` | Cover image |

## License

MIT, see [LICENSE](LICENSE).
