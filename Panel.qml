import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui

// OmaClone: paste a git link, pick a folder, clone. The bar button opens a popup; the work is done by the `omaclone`
// script next to this file (plain `git clone` underneath, so every git server works).
Panel {
  id: root
  moduleName: "blackcode.omaclone"
  ipcTarget: "blackcode.omaclone"
  manageIpc: true

  // ---- Settings (shell.json -> this widget's bar entry; see manifest schema) ----
  readonly property string defaultFolderSetting: String(setting("defaultFolder", ""))
  readonly property string afterClone: String(setting("afterClone", "Nothing"))

  readonly property string toolPath: decodeURIComponent(String(Qt.resolvedUrl("omaclone")).replace(/^file:\/\//, ""))
  readonly property color fg: bar ? bar.foreground : Commons.Color.foreground
  readonly property color muted: Qt.darker(fg, 1.45)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // ---- Form state ----
  property string url: ""
  property var parsed: null            // omaclone parse
  property var checkResult: null       // omaclone check
  property bool checking: false
  property string folder: ""
  property string folderName: ""
  property bool nameEdited: false
  property string branch: ""
  property bool branchEdited: false
  property bool shallow: false
  property bool submodules: false
  property var suggestions: []
  property bool folderOk: true
  property var recentFolders: []
  property var history: []
  property string gitVersion: ""
  property bool fromClipboard: false

  // ---- Sign in (private repositories) ----
  // The password lives only in these properties while the popup is in use: it is sent to the script over a pipe
  // (never as an argument), and cleared when a clone finishes, is cancelled, or the popup closes.
  property bool signinOpen: false
  property string signinUser: ""
  property bool signinUserEdited: false
  property string signinHost: ""            // host of the SSH key passphrase (keyPass)
  property string signinOrigin: ""          // scheme://host[:port] the typed web login belongs to: it never leaves it
  property bool signinTokenAsUser: false    // the login is one token: it is sent as both user and password, never shown or saved as a username
  property int signinTicket: 0              // changes whenever the target changes, which cancels a sign-in still in flight
  property string signinPass: ""        // password or token for an https server
  property string keyPass: ""           // passphrase of an SSH key: a separate variable, so one can never be sent as the other
  property bool signinRemember: false
  property string signinError: ""
  property bool signinBusy: false
  property bool credHelper: false
  property var knownUsers: ({})
  property var knownPorts: ({})
  // ---- HTTPS or SSH ----
  property string protocol: "https"      // https | ssh | git (a git:// link is used as it is)
  property string sshPort: ""
  property bool trustHost: false
  readonly property bool portOk: /^[0-9]{1,5}$/.test(sshPort) && Number(sshPort) > 0 && Number(sshPort) < 65536
  readonly property bool canChoose: urlOk && parsed.sshUrl !== undefined && parsed.sshUrl !== null && parsed.scheme !== "file"
  readonly property string effectiveUrl: {
    if (!urlOk) return ""
    if (!canChoose || protocol === "git") return parsed.cloneUrl
    if (protocol === "ssh") return portOk ? "ssh://" + parsed.sshUser + "@" + parsed.host + ":" + sshPort + "/" + parsed.sshPath : parsed.sshUrl
    return parsed.webUrl
  }
  readonly property string effectiveDisplay: {
    if (!urlOk) return ""
    if (!canChoose || protocol === "git") return parsed.display
    return protocol === "ssh" ? effectiveUrl : parsed.webDisplay
  }
  readonly property bool canSignIn: urlOk && (!canChoose ? (parsed.scheme === "https" || parsed.scheme === "http") : protocol === "https")
  readonly property bool isSsh: urlOk && canChoose && protocol === "ssh"     // SSH: the login card asks for the key's passphrase
  readonly property bool askLogin: canSignIn || isSsh
  readonly property bool authNeeded: askLogin && checkResult !== null && checkResult.status === "auth"
  readonly property bool signinVisible: askLogin && (signinOpen || authNeeded) && phase !== "cloning" && phase !== "done"
  readonly property string loginOrigin: (urlOk && !isSsh && canChoose) ? parsed.webOrigin : ""
  readonly property bool hasLogin: isSsh ? keyPass !== "" : (signinUser.trim() !== "" || signinPass !== "")
  readonly property string loginJson: JSON.stringify({ user: isSsh ? "" : signinUser.trim(), password: isSsh ? keyPass : signinPass, remember: !isSsh && signinRemember && credHelper,
                                                                tokenAsUser: !isSsh && signinTokenAsUser && signinUser.trim() === "" })

  // ---- Folder browser ----
  property bool browseOpen: false
  property string browseDir: ""
  property string browseShown: ""
  property string browseParent: ""
  property string browseHome: ""
  property var browseItems: []
  property bool browseWritable: true
  property bool browseMore: false
  property bool browseHidden: false
  property string browseError: ""
  property bool newFolderOpen: false
  property string newFolderName: ""
  property string newFolderError: ""
  property int browseSerial: 0

  // ---- Clone state ----
  property string phase: "idle"        // idle | cloning | done | failed
  property real percent: 0
  property string phaseText: ""
  property string phaseDetail: ""
  property string errorCode: ""
  property string errorText: ""
  property string hintText: ""
  property var doneInfo: null

  readonly property bool urlOk: parsed !== null && parsed.ok === true
  readonly property bool nameOk: folderName.trim() !== "" && folderName.indexOf("/") === -1 && folderName.indexOf("\\") === -1
    && folderName !== "." && folderName !== ".." && folderName.charAt(0) !== "-"
  readonly property bool canClone: phase !== "cloning" && urlOk && folder.trim() !== "" && nameOk
  readonly property string targetPath: folder.replace(/\/+$/, "") + "/" + folderName
  property int parseSerial: 0
  property int checkSerial: 0
  property int suggestSerial: 0

  // ---- Running the script ----
  Component {
    id: oneShot
    Process {
      id: shot
      property var callback: null
      property int serial: 0
      property string stdinText: ""
      stdinEnabled: stdinText !== ""
      onStarted: if (shot.stdinText !== "") shot.write(shot.stdinText)
      stdout: StdioCollector {
        id: collector
        waitForEnd: true
        onStreamFinished: if (shot.callback) shot.callback(collector.text, shot.serial)
      }
      onExited: Qt.callLater(function() { shot.destroy() })
    }
  }

  function run(command, callback, serial, stdinText) {
    var p = oneShot.createObject(root, { command: command, callback: callback, serial: serial || 0, stdinText: stdinText || "" })
    p.running = true
  }

  function tool(args, callback, serial, stdinText) { run(["python3", "-I", root.toolPath].concat(args), callback, serial, stdinText) }

  function json(text) {
    try { return JSON.parse(String(text).trim().split("\n").pop()) } catch (e) { return null }
  }

  // ---- Reading the link ----
  onUrlChanged: {
    if (phase === "done" || phase === "failed") phase = "idle"
    parseTimer.restart()
  }

  Timer {
    id: parseTimer
    interval: 220
    onTriggered: root.parseNow()
  }

  function parseNow() {
    var text = root.url.trim()
    if (text === "") {
      root.parsed = null
      root.checkResult = null
      root.checking = false
      return
    }
    root.parseSerial += 1
    root.tool(["parse", "--stdin"], function(out, serial) {
      if (serial !== root.parseSerial) return
      var p = root.json(out)
      if (p && (p.login || p.wipe)) { root.applyParsed(p, text); return }     // the field changes, which parses again
      root.parsed = p
      root.checkResult = null
      if (p && p.ok) {
        root.trustHost = false
        root.protocol = (p.scheme === "ssh") ? "ssh" : (p.scheme === "git" ? "git" : "https")
        root.sshPort = (p.scheme === "ssh" && p.port) ? String(p.port) : (root.knownPorts[p.host] ? String(root.knownPorts[p.host]) : "")
        if (p.host !== root.signinHost) { root.signinHost = p.host; root.keyPass = "" }
        if (root.signinOrigin !== "" && p.webOrigin !== root.signinOrigin) root.clearWebLogin()
        if (!root.signinUserEdited) root.signinUser = p.user || root.knownUsers[p.host] || ""
        if (!root.nameEdited) root.folderName = p.name
        if (!root.branchEdited) root.branch = p.branch || ""
        root.checking = true
        checkTimer.restart()
      } else {
        root.checking = false
      }
    }, root.parseSerial, text + "\n")
  }

  Timer {
    id: checkTimer
    interval: 350
    onTriggered: root.checkNow()
  }

  function chooseProtocol(name) {
    if (!root.canChoose || root.protocol === name || root.phase === "cloning") return
    root.protocol = name
    root.signinPass = ""
    root.keyPass = ""
    root.trustHost = false
    root.signinError = ""
    root.checkResult = null
    root.checking = true
    checkTimer.restart()
  }

  function checkNow() {
    if (!root.urlOk) return
    root.checkSerial += 1
    root.checking = true
    root.tool(["check", root.effectiveUrl, root.parsed.refPath || ""].concat(root.trustHost ? ["--trust-new-host"] : []), function(out, serial) {
      if (serial !== root.checkSerial) return
      root.checking = false
      var c = root.json(out)
      root.checkResult = c
      // A link to a branch page: use the branch the server really has, else the default.
      if (c && c.status === "ok" && !root.branchEdited) root.branch = c.ref || ""
    }, root.checkSerial)
  }

  // ---- Folder field ----
  onFolderChanged: suggestTimer.restart()

  Timer {
    id: suggestTimer
    interval: 120
    onTriggered: root.suggestNow()
  }

  function suggestNow() {
    root.suggestSerial += 1
    root.tool(["suggest", root.folder], function(out, serial) {
      if (serial !== root.suggestSerial) return
      var s = root.json(out)
      if (!s) return
      root.suggestions = s.items || []
      root.folderOk = s.exists ? s.writable : true
    }, root.suggestSerial)
  }

  // ---- Folder browser ----
  function browseTo(path) {
    root.browseSerial += 1
    root.tool(["ls", path].concat(root.browseHidden ? ["--hidden"] : []), function(out, serial) {
      if (serial !== root.browseSerial) return
      var r = root.json(out)
      if (!r) return
      root.browseDir = r.path
      root.browseShown = r.shown
      root.browseParent = r.parent
      root.browseHome = r.home
      root.browseItems = r.items || []
      root.browseWritable = r.writable
      root.browseMore = r.more === true
      root.browseError = r.error || ""
    }, root.browseSerial)
  }

  function openBrowser(path) {
    root.browseOpen = true
    root.newFolderOpen = false
    root.newFolderError = ""
    root.browseTo(path !== undefined && path !== "" ? path : (root.folder.trim() !== "" ? root.folder : "~"))
  }

  function closeBrowser() {
    root.browseOpen = false
    root.newFolderOpen = false
  }

  function useBrowsed() {
    if (root.browseDir === "") return
    root.folder = root.browseDir
    closeBrowser()
  }

  function createFolder(name) {
    var wanted = String(name !== undefined ? name : root.newFolderName).trim()
    if (wanted === "") { root.newFolderError = "Type a name for the new folder"; return }
    root.tool(["mkdir", root.browseDir, wanted], function(out) {
      var r = root.json(out)
      if (r && r.ok) {
        root.newFolderOpen = false
        root.newFolderName = ""
        root.newFolderError = ""
        root.browseTo(r.path)
      } else {
        root.newFolderError = r && r.message ? r.message : "Could not create the folder"
      }
    })
  }

  // ---- Opening ----
  function refreshState() {
    root.tool(["state"].concat(root.defaultFolderSetting !== "" ? ["--default", root.defaultFolderSetting] : []), function(out) {
      var s = root.json(out)
      if (!s) return
      root.credHelper = s.credentialHelper === true
      root.knownUsers = s.users || {}
      root.knownPorts = s.ports || {}
      root.recentFolders = s.folders || []
      root.history = s.history || []
      root.gitVersion = s.git || ""
      if (root.folder === "") root.folder = s.defaultFolder || "~"
    })
  }

  // The backend is the only thing that reads a link. It takes a password or token out of it (any link form) and hands it
  // back as `login`, with the link cleaned (`cleanText`). The link goes to it on stdin, never as a command-line argument,
  // and the field only ever keeps the cleaned text.
  property int acceptSerial: 0
  property string linkError: ""

  function applyParsed(p, text) {
    if (p && p.wipe) {                               // a password that cannot be told from a path: refuse, show nothing of it
      root.url = ""
      root.linkError = p.message || ""
      urlField.sync()
      return
    }
    root.linkError = ""
    if (p && p.ok && p.login) {
      root.signinHost = String(p.host || "").toLowerCase()
      root.signinOrigin = p.webOrigin
      root.signinTokenAsUser = p.login.tokenAsUser === true
      root.signinUser = p.login.user
      root.signinUserEdited = true
      root.signinPass = p.login.password
      root.signinOpen = true
    }
    root.url = (p && p.cleanText !== undefined) ? p.cleanText : text
    urlField.sync()
  }

  // Set the link field from anything: typing, the clipboard, a script. Text without an @ cannot hold a login.
  function acceptLink(text, then) {
    if (text.indexOf("@") < 0) {
      root.linkError = ""
      root.url = text
      if (then) then()
      return
    }
    root.acceptSerial += 1
    root.tool(["parse", "--stdin"], function(out, serial) {
      if (serial !== root.acceptSerial) return
      root.applyParsed(root.json(out), text)
      if (then) then()
    }, root.acceptSerial, text + "\n")
  }

  function looksLikeLink(text) {
    return /^(git\s+clone\s+(-\S+\s+(\S+\s+)?)*)?(https?:\/\/|ssh:\/\/|git:\/\/|git\+ssh:\/\/|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:|[A-Za-z0-9.-]+\.[A-Za-z]{2,}\/)\S/.test(text)
  }

  // The clipboard: if it holds a git link, it is already in the field when the popup opens.
  function readClipboard() {
    root.run(["wl-paste", "--no-newline"], function(out) {
      var text = String(out).trim().split("\n")[0]
      if (text === "" || text.length > 600 || root.url.trim() !== "" || root.phase === "cloning") return
      // Only text that already looks like a git link is looked at: a copied password never reaches a command line.
      if (!root.looksLikeLink(text)) return
      root.tool(["parse", "--stdin"], function(parsedOut) {
        var p = root.json(parsedOut)
        if (p && p.ok && root.url.trim() === "") {
          root.fromClipboard = true
          root.applyParsed(p, text)
        }
      }, 0, text + "\n")
    })
  }

  onOpenedChanged: {
    if (!opened) clearSecrets()
    if (opened) {
      closeBrowser()
      refreshState()
      if (phase === "done" || phase === "failed") resetForm()
      if (root.url.trim() === "") readClipboard()
    }
  }

  function clearWebLogin() {
    root.signinPass = ""
    root.signinUser = ""
    root.signinUserEdited = false
    root.signinTokenAsUser = false
    root.signinOrigin = ""
    root.signinError = ""
  }

  // The typed login belongs to ONE origin. If the scheme, host or port changes, it is dropped; and any sign-in that is
  // still on its way to the server is cancelled when the target (link, protocol, port) changes.
  onLoginOriginChanged: {
    if (loginOrigin !== "" && signinOrigin !== "" && loginOrigin !== signinOrigin) clearWebLogin()
    signinTicket += 1
  }
  onEffectiveUrlChanged: { signinTicket += 1; signinBusy = false }

  function clearSecrets() {
    root.signinPass = ""
    root.keyPass = ""
  }

  function resetForm() {
    clearSecrets()
    root.linkError = ""
    root.signinOpen = false
    root.signinError = ""
    root.signinBusy = false
    root.signinUser = ""
    root.signinUserEdited = false
    root.signinOrigin = ""
    root.signinTokenAsUser = false
    root.url = ""
    root.parsed = null
    root.checkResult = null
    root.folderName = ""
    root.nameEdited = false
    root.branch = ""
    root.branchEdited = false
    root.fromClipboard = false
    root.phase = "idle"
    root.percent = 0
    root.doneInfo = null
    root.errorText = ""
    root.hintText = ""
  }

  Component.onCompleted: refreshState()

  // ---- Cloning ----
  // The Clone button: with a login typed in the sign-in card, sign in first.
  function cloneClicked() {
    if (root.signinVisible && root.hasLogin) root.signInAndClone()
    else root.startClone(false)
  }

  // Check the login against the server (this also finds the real branch), then clone with it.
  function signInAndClone() {
    if (!root.canClone || !root.hasLogin || root.signinBusy) return
    root.signinBusy = true
    root.signinError = ""
    var login = root.loginJson
    var ticket = root.signinTicket
    root.tool(["check", root.effectiveUrl, root.parsed.refPath || "", "--credentials-from-stdin"], function(out) {
      if (ticket !== root.signinTicket) { root.signinBusy = false; return }      // the target changed meanwhile: never clone it
      root.signinBusy = false
      var c = root.json(out)
      if (c && c.status === "ok") {
        root.checkResult = c
        if (!root.branchEdited) root.branch = c.ref || ""
        root.startClone(true)
      } else {
        root.signinError = c ? (c.hint || c.message || "Could not sign in") : "Could not sign in"
      }
    }, 0, login + "\n")
  }

  function startClone(withLogin) {
    if (!root.canClone) return
    root.phase = "cloning"
    root.percent = 0
    root.phaseText = "Connecting"
    root.phaseDetail = ""
    root.errorText = ""
    root.hintText = ""
    root.doneInfo = null
    var args = ["clone", root.effectiveUrl, root.folder, "--name", root.folderName.trim()]
    if (root.trustHost) args.push("--trust-new-host")
    if (root.branch.trim() !== "") args = args.concat(["--branch", root.branch.trim()])
    if (root.shallow) args.push("--shallow")
    if (root.submodules) args.push("--submodules")
    cloneProc.pendingLogin = ""
    if (withLogin === true) {
      args.push("--credentials-from-stdin")
      cloneProc.pendingLogin = root.loginJson + "\n"
    }
    cloneProc.command = ["python3", "-I", root.toolPath].concat(args)
    cloneProc.running = true
  }

  function cancelClone() {
    if (cloneProc.running) cloneProc.signal(15)
  }

  function onCloneEvent(line) {
    var e = root.json(line)
    if (!e) return
    if (e.event === "progress") {
      root.percent = e.percent
      root.phaseText = e.phase
      root.phaseDetail = e.detail || ""
    } else if (e.event === "done") {
      root.clearSecrets()
      root.signinOpen = false
      root.doneInfo = e
      root.percent = 100
      root.phase = "done"
      root.refreshState()
      root.afterDone(e.path)
    } else if (e.event === "error" && e.code === "auth" && root.askLogin) {
      // a login is needed (or was wrong): stay in the form, with the sign-in card open
      root.phase = "idle"
      root.signinOpen = true
      root.signinError = e.signedIn ? (e.hint || e.message) : ""
    } else if (e.event === "error") {
      root.phase = "failed"
      root.errorCode = e.code || "other"
      root.errorText = e.message || "The clone failed"
      root.hintText = e.hint || ""
    } else if (e.event === "cancelled") {
      root.clearSecrets()
      root.phase = "idle"
    }
  }

  function afterDone(path) {
    if (root.afterClone === "Open folder") openFolder(path)
    else if (root.afterClone === "Open in terminal") openTerminal(path)
    else if (root.afterClone === "Open in editor") openEditor(path)
  }

  function openFolder(path) { Quickshell.execDetached(["xdg-open", path]) }
  function openTerminal(path) {
    Quickshell.execDetached(["xdg-terminal-exec", "bash", "-lc", 'cd "$1" && exec "${SHELL:-bash}"', "_", path])
  }
  function openEditor(path) { Quickshell.execDetached(["omarchy-launch-editor", path]) }

  // For a repository that needs a password: let git ask in a terminal, where it can.
  function cloneInTerminal() {
    if (!root.urlOk) return
    var args = ["xdg-terminal-exec", "bash", "-lc",
      'mkdir -p "$(dirname "$2")" && git clone -- "$1" "$2"; echo; read -n 1 -s -r -p "Press any key to close"',
      "_", root.effectiveUrl, root.targetPath.replace(/^~/, Quickshell.env("HOME"))]
    Quickshell.execDetached(args)
  }

  Process {
    id: cloneProc
    property string pendingLogin: ""
    stdinEnabled: true
    onStarted: if (cloneProc.pendingLogin !== "") { cloneProc.write(cloneProc.pendingLogin); cloneProc.pendingLogin = "" }
    stdout: SplitParser { onRead: function(line) { root.onCloneEvent(line) } }
    stderr: StdioCollector { id: cloneErr }
    onExited: function(code) {
      if (root.phase === "cloning") {
        root.phase = "failed"
        root.errorCode = "other"
        root.errorText = "The clone stopped unexpectedly"
        root.hintText = ""
      }
    }
  }

  // For scripts and key bindings:  omarchy-shell omaclone fill <link>,  omarchy-shell omaclone clone <link> <folder>  and  omarchy-shell omaclone cancel
  IpcHandler {
    target: "omaclone"
    function fill(text: string): string {
      root.resetForm()
      root.acceptLink(text)
      root.open()
      return "ok"
    }
    function browse(path: string): string {
      root.open()
      root.openBrowser(path)
      return "ok"
    }
    function newFolder(name: string): string {
      root.createFolder(name)
      return "ok"
    }
    function useFolder(): string {
      root.useBrowsed()
      return "ok"
    }
    function protocol(name: string): string {
      root.chooseProtocol(name === "ssh" ? "ssh" : "https")
      return root.protocol
    }
    function cancel(): string {
      root.cancelClone()
      return "ok"
    }
    function clone(text: string, dir: string): string {
      root.resetForm()
      root.open()
      root.acceptLink(text, function() {
        if (dir !== "") root.folder = dir
        parseTimer.stop()
        root.parseNow()
        cloneWhenReady.restart()
      })
      return "ok"
    }
  }

  Timer {
    id: cloneWhenReady
    interval: 400
    repeat: true
    property int tries: 0
    onTriggered: {
      tries += 1
      if (root.urlOk && root.nameOk && root.folder !== "") { stop(); tries = 0; if (root.hasLogin && root.canSignIn) root.signInAndClone(); else root.startClone() }
      else if (tries > 20) { stop(); tries = 0 }
    }
  }

  // ---- Bar button ----
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰊢"
    labelVisible: false
    fixedWidth: vertical ? -1 : Math.round(Style.font.caption * 1.35) + Style.spaceReal(6) * 2
    fontSize: Style.font.caption
    horizontalMargin: 6
    tooltipText: root.phase === "cloning" ? "Cloning… " + Math.round(root.percent) + "%" : "Clone a git repository"
    onPressed: function(b) { root.toggle() }
    Image {
      anchors.centerIn: parent
      source: Qt.resolvedUrl("icon.svg")
      width: Math.round(Style.font.caption * 1.35)
      height: width
      sourceSize: Qt.size(width * 2, height * 2)
      fillMode: Image.PreserveAspectFit
      smooth: true
      property real pulse: 1
      opacity: root.phase === "cloning" ? pulse : 1
      SequentialAnimation on pulse {
        running: root.phase === "cloning"
        loops: Animation.Infinite
        NumberAnimation { to: 0.4; duration: 650; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1; duration: 650; easing.type: Easing.InOutSine }
      }
    }
  }

  // ---- Small building blocks ----
  component Caption: Text {
    textFormat: Text.PlainText
    color: root.muted
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    font.bold: true
    font.letterSpacing: 1
  }

  component Body: Text {
    textFormat: Text.PlainText
    color: root.fg
    font.family: root.fontFamily
    font.pixelSize: Style.font.body
    wrapMode: Text.Wrap
  }

  component Note: Text {
    textFormat: Text.PlainText
    color: root.muted
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
    wrapMode: Text.Wrap
  }

  // A text field that follows `value` even after the person has typed in it (a plain `text:` binding is lost on
  // the first keystroke, so a pasted or reset link would no longer show).
  component Input: TextField {
    property string value: ""
    signal edited(string typed)
    function sync() { if (text !== value) text = value }
    onValueChanged: sync()
    Component.onCompleted: text = value
    onTextEdited: edited(text)
  }

  component Chip: Rectangle {
    property string label: ""
    property color tint: Commons.Color.accent
    implicitWidth: chipText.implicitWidth + Style.space(12)
    implicitHeight: chipText.implicitHeight + Style.space(4)
    radius: height / 2
    color: Qt.rgba(tint.r, tint.g, tint.b, 0.16)
    border.width: 1
    border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.45)
    Text {
      id: chipText
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: parent.label
      color: parent.tint
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      font.bold: true
    }
  }

  // ---- Popup ----
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: urlField
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(column.implicitHeight, Style.space(900))

    Column {
      id: column
      anchors.fill: parent
      spacing: Style.space(10)

      // Header
      Row {
        spacing: Style.space(10)
        Image {
          source: Qt.resolvedUrl("icon.svg")
          width: Math.round(Style.font.display * 1.15)
          height: width
          sourceSize: Qt.size(width * 2, height * 2)
          anchors.verticalCenter: parent.verticalCenter
        }
        Column {
          anchors.verticalCenter: parent.verticalCenter
          Text { textFormat: Text.PlainText; text: "OmaClone"; color: root.fg; font.family: root.fontFamily; font.pixelSize: Style.font.title; font.bold: true }
          Caption { text: "CLONE ANY GIT REPOSITORY" }
        }
      }

      // Link
      Column {
        width: parent.width
        spacing: Style.space(4)
        Row {
          spacing: Style.space(8)
          Caption { text: "REPOSITORY LINK" }
          Caption { visible: root.fromClipboard && root.urlOk; text: "· FROM CLIPBOARD"; color: Commons.Color.accent }
        }
        Input {
          id: urlField
          width: parent.width
          placeholderText: "Paste a link from GitHub, Forgejo, Codeberg, GitLab…"
          value: root.url
          enabled: root.phase !== "cloning"
          onEdited: function(t) { root.fromClipboard = false; root.acceptLink(t) }
          onAccepted: folderField.forceActiveFocus()
        }
        // what the link was understood as
        Row {
          visible: root.urlOk
          spacing: Style.space(8)
          Chip { label: root.urlOk ? root.parsed.provider : "" }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: root.urlOk ? root.effectiveDisplay.replace(/^[a-z+]+:\/\//, "") : ""
            elide: Text.ElideMiddle
            width: Math.min(implicitWidth, column.width - Style.space(110))
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
        Button {
          visible: root.checkResult !== null && root.checkResult.status === "host-key" && !root.trustHost
          text: "Trust this server"
          bordered: true
          selected: true
          onClicked: { root.trustHost = true; root.checkResult = null; root.checking = true; checkTimer.restart() }
        }
        // HTTPS or SSH
        Row {
          visible: root.canChoose
          spacing: Style.space(8)
          Caption { anchors.verticalCenter: parent.verticalCenter; text: "CONNECT WITH" }
          Button { text: "HTTPS"; bordered: true; selected: root.protocol === "https"; enabled: root.phase !== "cloning"; onClicked: root.chooseProtocol("https") }
          Button { text: "SSH"; bordered: true; selected: root.protocol === "ssh"; enabled: root.phase !== "cloning"; onClicked: root.chooseProtocol("ssh") }
          Input {
            id: portField
            visible: root.protocol === "ssh"
            width: Style.space(120)
            placeholderText: "SSH port (22)"
            value: root.sshPort
            enabled: root.phase !== "cloning"
            onEdited: function(t) { root.sshPort = t.replace(/[^0-9]/g, "").slice(0, 5); if (root.portOk || root.sshPort === "") { root.checkResult = null; root.checking = true; checkTimer.restart() } }
          }
        }
        Button {
          visible: root.canSignIn && root.checkResult !== null && root.checkResult.status === "notfound" && !root.signinOpen
          text: "Private repository? Sign in"
          bordered: true
          onClicked: root.signinOpen = true
        }
        // what the server said
        Note {
          width: parent.width
          visible: text !== ""
          text: {
            if (root.linkError !== "") return root.linkError
            if (root.url.trim() === "") return "Copy a repository link in your browser, then open this: it is filled in for you."
            if (root.parsed && !root.parsed.ok) return root.parsed.message
            if (root.checking) return "Checking the link…"
            var c = root.checkResult
            if (!c) return ""
            if (c.status === "ok") return "Found" + (c.defaultBranch ? " · default branch " + c.defaultBranch : "") + (c.refs ? " · " + c.refs + " branches and tags" : "") + (c.note ? "\n" + c.note : "")
            return (c.status === "auth" ? "Needs sign-in. " : c.status === "notfound" ? "Not found. " : c.status === "network" ? "Cannot reach it. " : "") + (c.hint || c.message || "")
          }
          color: {
            if (root.linkError !== "") return Commons.Color.urgent
            if (root.parsed && !root.parsed.ok) return Commons.Color.urgent
            var c = root.checkResult
            if (c && c.status === "ok") return Commons.Color.accent
            if (c && c.status !== "ok") return Commons.Color.urgent
            return root.muted
          }
        }
      }

      // Sign in: for a repository that needs a username and password (or a token)
      Rectangle {
        width: parent.width
        visible: root.signinVisible
        implicitHeight: signinColumn.implicitHeight + Style.space(20)
        radius: Style.space(8)
        color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.05)
        border.width: 1
        border.color: Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.5)
        Column {
          id: signinColumn
          anchors.fill: parent
          anchors.margins: Style.space(10)
          spacing: Style.space(6)
          Caption { text: root.isSsh ? "UNLOCK YOUR SSH KEY" : (root.canSignIn ? "SIGN IN TO " + root.parsed.host.toUpperCase() : "SIGN IN"); color: Commons.Color.accent }
          Note {
            width: parent.width
            text: root.isSsh ? "SSH uses your own key. If the key has a passphrase, type it here (no ssh-agent needed). It is used for this clone only and never saved. If it still fails, the key may not be added to your account on this server."
                             : "Use your username and password, or an access token (many servers ask for a token). It is sent only to this server, and never saved unless you choose to."
          }
          Note {
            width: parent.width
            visible: root.canSignIn && root.parsed.scheme === "http"
            text: "This link is not encrypted (http): the password would travel in the clear."
            color: Commons.Color.urgent
          }
          Row {
            width: parent.width
            spacing: Style.space(10)
            Column {
              visible: !root.isSsh
              width: (parent.width - Style.space(10)) * 0.42
              spacing: Style.space(4)
              Caption { text: "USERNAME" }
              Input {
                id: userField
                width: parent.width
                placeholderText: root.signinTokenAsUser ? "(the token is the login)" : "username"
                value: root.signinUser
                enabled: root.phase !== "cloning" && !root.signinBusy
                onEdited: function(t) { root.signinUserEdited = true; root.signinUser = t; if (t !== "") root.signinTokenAsUser = false; root.signinOrigin = root.loginOrigin; root.signinError = "" }
                onAccepted: passField.forceActiveFocus()
              }
            }
            Column {
              width: root.isSsh ? parent.width : (parent.width - Style.space(10)) * 0.58
              spacing: Style.space(4)
              Caption { text: root.isSsh ? "KEY PASSPHRASE" : "PASSWORD OR TOKEN" }
              Input {
                id: passField
                width: parent.width
                password: true
                placeholderText: root.isSsh ? "passphrase of your SSH key" : "password or access token"
                value: root.isSsh ? root.keyPass : root.signinPass
                enabled: root.phase !== "cloning" && !root.signinBusy
                onEdited: function(t) { if (root.isSsh) root.keyPass = t; else { root.signinPass = t; root.signinOrigin = root.loginOrigin } root.signinError = "" }
                onAccepted: root.cloneClicked()
              }
            }
          }
          Toggle {
            width: parent.width
            visible: root.credHelper && !root.isSsh
            label: "Remember this login"
            description: "Kept by your git credential helper, so git pull works later"
            checked: root.signinRemember
            onClicked: root.signinRemember = !root.signinRemember
          }
          Note {
            width: parent.width
            visible: !root.credHelper && !root.isSsh
            text: "No git credential helper is set up, so nothing is remembered: git will ask again when you pull."
          }
          Note { width: parent.width; visible: root.signinError !== ""; text: root.signinError; color: Commons.Color.urgent }
          Row {
            spacing: Style.space(8)
            Button {
              text: root.signinBusy ? (root.isSsh ? "Unlocking…" : "Signing in…") : (root.isSsh ? "Unlock and clone" : "Sign in and clone")
              bordered: true
              selected: root.hasLogin && root.canClone
              enabled: root.hasLogin && root.canClone && !root.signinBusy
              onClicked: root.signInAndClone()
            }
          }
        }
      }

      // Branch and folder name
      Row {
        width: parent.width
        spacing: Style.space(10)
        Column {
          width: (parent.width - Style.space(10)) * 0.4
          spacing: Style.space(4)
          Caption { text: "BRANCH OR TAG" }
          Input {
            width: parent.width
            placeholderText: root.checkResult && root.checkResult.defaultBranch ? "default (" + root.checkResult.defaultBranch + ")" : "default"
            value: root.branch
            enabled: root.phase !== "cloning"
            onEdited: function(t) { root.branchEdited = true; root.branch = t }
          }
        }
        Column {
          width: (parent.width - Style.space(10)) * 0.6
          spacing: Style.space(4)
          Caption { text: "FOLDER NAME" }
          Input {
            width: parent.width
            placeholderText: "name"
            value: root.folderName
            enabled: root.phase !== "cloning"
            onEdited: function(t) { root.nameEdited = true; root.folderName = t }
          }
        }
      }

      // Where
      Column {
        width: parent.width
        spacing: Style.space(4)
        Caption { text: "DOWNLOAD TO" }
        Row {
          width: parent.width
          spacing: Style.space(8)
          Input {
            id: folderField
            width: parent.width - browseButton.implicitWidth - Style.space(8)
            placeholderText: "~/Projects"
            value: root.folder
            enabled: root.phase !== "cloning"
            onEdited: function(t) { root.folder = t }
            onAccepted: root.cloneClicked()
          }
          Button {
            id: browseButton
            text: root.browseOpen ? "Close" : "Browse…"
            iconText: "󰉋"
            bordered: true
            enabled: root.phase !== "cloning"
            onClicked: root.browseOpen ? root.closeBrowser() : root.openBrowser()
          }
        }
        // ---- the folder browser ----
        Rectangle {
          id: browser
          width: parent.width
          visible: root.browseOpen
          implicitHeight: browserColumn.implicitHeight + Style.space(16)
          radius: Style.space(8)
          color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.05)
          border.width: 1
          border.color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.18)
          Column {
            id: browserColumn
            anchors.fill: parent
            anchors.margins: Style.space(8)
            spacing: Style.space(6)
            Row {
              spacing: Style.space(6)
              Button { text: "↑ Up"; bordered: true; enabled: root.browseParent !== ""; onClicked: root.browseTo(root.browseParent) }
              Button { text: "Home"; bordered: true; onClicked: root.browseTo("~") }
              Button { text: "New folder"; bordered: true; selected: root.newFolderOpen; enabled: root.browseWritable; onClicked: { root.newFolderOpen = !root.newFolderOpen; root.newFolderError = ""; if (root.newFolderOpen) newFolderField.forceActiveFocus() } }
              Button { text: root.browseHidden ? "Hidden: on" : "Hidden: off"; bordered: true; onClicked: { root.browseHidden = !root.browseHidden; root.browseTo(root.browseDir) } }
            }
            Text {
              width: parent.width
              textFormat: Text.PlainText
              text: root.browseShown
              elide: Text.ElideMiddle
              color: Commons.Color.accent
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
            }
            Note { width: parent.width; visible: root.browseError !== ""; text: "Cannot open this folder: " + root.browseError; color: Commons.Color.urgent }
            // the folders in here
            ListView {
              id: folderList
              width: parent.width
              height: Math.min(contentHeight, Style.space(176))
              clip: true
              boundsBehavior: Flickable.StopAtBounds
              model: root.browseItems
              delegate: Rectangle {
                id: folderRow
                required property var modelData
                width: folderList.width
                height: Style.space(28)
                radius: Style.space(5)
                color: rowHover.hovered ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12) : "transparent"
                Row {
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.left: parent.left
                  anchors.leftMargin: Style.space(8)
                  spacing: Style.space(8)
                  Text { textFormat: Text.PlainText; text: "󰉋"; color: Commons.Color.accent; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                  Text { textFormat: Text.PlainText; text: folderRow.modelData.name; color: root.fg; font.family: root.fontFamily; font.pixelSize: Style.font.body; elide: Text.ElideRight; width: folderList.width - Style.space(48) }
                }
                HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: root.browseTo(folderRow.modelData.path) }
              }
            }
            Note {
              width: parent.width
              visible: root.browseItems.length === 0 && root.browseError === ""
              text: "No folders in here. You can clone here, or make a new folder."
            }
            Note { width: parent.width; visible: root.browseMore; text: "Only the first folders are shown. Type the path to go further." }
            // make a folder
            Column {
              width: parent.width
              spacing: Style.space(4)
              visible: root.newFolderOpen
              Row {
                spacing: Style.space(6)
                Input {
                  id: newFolderField
                  width: parent.parent.width - createButton.implicitWidth - Style.space(6)
                  placeholderText: "New folder name"
                  value: root.newFolderName
                  onEdited: function(t) { root.newFolderName = t; root.newFolderError = "" }
                  onAccepted: root.createFolder()
                }
                Button { id: createButton; text: "Create"; bordered: true; selected: root.newFolderName.trim() !== ""; onClicked: root.createFolder() }
              }
              Note { width: parent.width; visible: root.newFolderError !== ""; text: root.newFolderError; color: Commons.Color.urgent }
            }
            Row {
              spacing: Style.space(8)
              Button { text: "Use this folder"; bordered: true; selected: true; enabled: root.browseWritable; onClicked: root.useBrowsed() }
              Note { anchors.verticalCenter: parent.verticalCenter; visible: !root.browseWritable; text: "You cannot write in this folder"; color: Commons.Color.urgent }
            }
          }
        }
        // folders matching what is typed
        Flow {
          width: parent.width
          spacing: Style.space(6)
          visible: root.suggestions.length > 0 && folderField.activeFocus && !root.browseOpen
          Repeater {
            model: root.suggestions
            delegate: Rectangle {
              required property var modelData
              implicitWidth: sText.implicitWidth + Style.space(14)
              implicitHeight: sText.implicitHeight + Style.space(6)
              radius: Style.space(5)
              color: sHover.hovered ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14) : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.07)
              Text { id: sText; anchors.centerIn: parent; textFormat: Text.PlainText; text: parent.modelData; color: root.fg; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              HoverHandler { id: sHover; cursorShape: Qt.PointingHandCursor }
              TapHandler { onTapped: { root.folder = parent.modelData + "/"; folderField.forceActiveFocus() } }
            }
          }
        }
        // recent folders
        Flow {
          width: parent.width
          spacing: Style.space(6)
          visible: root.recentFolders.length > 0 && !root.browseOpen && !(root.suggestions.length > 0 && folderField.activeFocus)
          Caption { text: "RECENT" }
          Repeater {
            model: root.recentFolders
            delegate: Rectangle {
              required property var modelData
              implicitWidth: rText.implicitWidth + Style.space(14)
              implicitHeight: rText.implicitHeight + Style.space(6)
              radius: Style.space(5)
              color: rHover.hovered ? Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.14) : Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.07)
              border.width: parent.modelData === root.folder ? 1 : 0
              border.color: Commons.Color.accent
              Text { id: rText; anchors.centerIn: parent; textFormat: Text.PlainText; text: parent.modelData; color: root.fg; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              HoverHandler { id: rHover; cursorShape: Qt.PointingHandCursor }
              TapHandler { onTapped: root.folder = parent.modelData }
            }
          }
        }
        Note {
          width: parent.width
          visible: root.urlOk && root.nameOk && root.folder.trim() !== ""
          text: (root.folderOk ? "→ " : "Cannot write there: ") + root.targetPath
          color: root.folderOk ? root.muted : Commons.Color.urgent
        }
      }

      // Options
      Row {
        width: parent.width
        spacing: Style.space(10)
        Toggle {
          width: (parent.width - Style.space(10)) / 2
          label: "Latest only"
          description: "Faster, no history"
          checked: root.shallow
          enabled: root.phase !== "cloning"
          onClicked: root.shallow = !root.shallow
        }
        Toggle {
          width: (parent.width - Style.space(10)) / 2
          label: "Submodules"
          description: "Nested repositories"
          checked: root.submodules
          enabled: root.phase !== "cloning"
          onClicked: root.submodules = !root.submodules
        }
      }

      // Progress
      Column {
        width: parent.width
        spacing: Style.space(6)
        visible: root.phase === "cloning"
        Item {
          width: parent.width
          height: Style.space(8)
          Rectangle { anchors.fill: parent; radius: height / 2; color: Qt.rgba(root.fg.r, root.fg.g, root.fg.b, 0.12) }
          Rectangle {
            width: Math.max(parent.height, parent.width * root.percent / 100)
            height: parent.height
            radius: height / 2
            color: Commons.Color.accent
            Behavior on width { NumberAnimation { duration: Style.duration(160); easing.type: Easing.OutCubic } }
          }
        }
        Row {
          width: parent.width
          spacing: Style.space(8)
          Note { text: root.phaseText; color: root.fg }
          Note { text: root.phaseDetail; elide: Text.ElideRight; width: Math.max(0, parent.width - Style.space(150)) }
        }
      }

      // Failed
      Rectangle {
        width: parent.width
        visible: root.phase === "failed"
        implicitHeight: failedColumn.implicitHeight + Style.space(20)
        radius: Style.space(8)
        color: Qt.rgba(Commons.Color.urgent.r, Commons.Color.urgent.g, Commons.Color.urgent.b, 0.10)
        border.width: 1
        border.color: Qt.rgba(Commons.Color.urgent.r, Commons.Color.urgent.g, Commons.Color.urgent.b, 0.4)
        Column {
          id: failedColumn
          anchors.fill: parent
          anchors.margins: Style.space(10)
          spacing: Style.space(6)
          Body { width: parent.width; text: root.errorText; color: Commons.Color.urgent }
          Note { width: parent.width; visible: root.hintText !== ""; text: root.hintText }
          Row {
            spacing: Style.space(8)
            Button {
              visible: root.errorCode === "auth" || root.errorCode === "host-key" || root.errorCode === "other"
              text: "Clone in a terminal"
              bordered: true
              onClicked: root.cloneInTerminal()
            }
            Button { text: "Try again"; bordered: true; onClicked: root.phase = "idle" }
          }
        }
      }

      // Done
      Rectangle {
        width: parent.width
        visible: root.phase === "done" && root.doneInfo !== null
        implicitHeight: doneColumn.implicitHeight + Style.space(20)
        radius: Style.space(8)
        color: Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.10)
        border.width: 1
        border.color: Qt.rgba(Commons.Color.accent.r, Commons.Color.accent.g, Commons.Color.accent.b, 0.45)
        Column {
          id: doneColumn
          anchors.fill: parent
          anchors.margins: Style.space(10)
          spacing: Style.space(6)
          Body { width: parent.width; text: root.doneInfo ? "Cloned to " + root.doneInfo.shown : ""; color: Commons.Color.accent; font.bold: true }
          Note { width: parent.width; text: root.doneInfo ? root.doneInfo.branch + " @ " + root.doneInfo.commit + " · " + root.doneInfo.seconds + " s" : "" }
          Row {
            spacing: Style.space(8)
            Button { text: "Open folder"; bordered: true; onClicked: root.openFolder(root.doneInfo.path) }
            Button { text: "Terminal"; bordered: true; onClicked: root.openTerminal(root.doneInfo.path) }
            Button { text: "Editor"; bordered: true; onClicked: root.openEditor(root.doneInfo.path) }
            Button { text: "Clone another"; bordered: true; onClicked: { root.resetForm(); urlField.forceActiveFocus(); root.readClipboard() } }
          }
        }
      }

      // Actions
      Row {
        spacing: Style.space(8)
        Button {
          text: root.phase === "cloning" ? "Cloning…" : root.signinBusy ? "Signing in…" : (root.signinVisible && root.hasLogin ? (root.isSsh ? "Unlock and clone" : "Sign in and clone") : "Clone")
          bordered: true
          selected: root.canClone
          enabled: root.canClone && !root.signinBusy
          onClicked: root.cloneClicked()
        }
        Button {
          visible: root.phase === "cloning"
          text: "Cancel"
          bordered: true
          onClicked: root.cancelClone()
        }
      }

      // Recent clones
      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: root.history.length > 0 && root.phase !== "cloning"
        Caption { text: "RECENT CLONES" }
        Repeater {
          model: root.history.slice(0, 3)
          delegate: Item {
            required property var modelData
            width: column.width
            implicitHeight: hRow.implicitHeight + Style.space(6)
            Row {
              id: hRow
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(8)
              Text { textFormat: Text.PlainText; text: parent.parent.modelData.name; color: root.fg; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true }
              Text {
                textFormat: Text.PlainText
                text: parent.parent.modelData.path
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: Style.font.caption
                elide: Text.ElideMiddle
                width: Math.min(implicitWidth, column.width - Style.space(150))
              }
            }
            Text {
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              textFormat: Text.PlainText
              text: "Open"
              color: hHover.hovered ? Commons.Color.accent : root.muted
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
            HoverHandler { id: hHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: root.openFolder(parent.modelData.path.replace(/^~/, Quickshell.env("HOME"))) }
          }
        }
      }
    }
  }
}
