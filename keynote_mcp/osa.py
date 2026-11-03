"""Run AppleScript through osascript.

User-supplied text never goes into the script source. It is passed as argv and
referenced as (item N of argv). Only validated numbers and whitelisted keywords
are written into the source.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

# Helpers available to every script.
PRELUDE = r'''
on repl(s, a, b)
	set AppleScript's text item delimiters to a
	set parts to text items of s
	set AppleScript's text item delimiters to b
	set s to parts as text
	set AppleScript's text item delimiters to ""
	return s
end repl

on js(v)
	if v is missing value then return "null"
	set s to v as text
	set s to my repl(s, "\\", "\\\\")
	set s to my repl(s, "\"", "\\\"")
	set s to my repl(s, return & linefeed, "\\n")
	set s to my repl(s, linefeed, "\\n")
	set s to my repl(s, return, "\\n")
	set s to my repl(s, tab, "\\t")
	set s to my repl(s, character id 8232, "\\n")
	set s to my repl(s, character id 8233, "\\n")
	return "\"" & s & "\""
end js

on jn(v)
	if v is missing value then return "null"
	try
		set t to v as text
	on error
		return "null"
	end try
	return my repl(t, ",", ".")
end jn

on jb(v)
	if v is missing value then return "null"
	if v then return "true"
	return "false"
end jb

on jlist(L)
	set out to {}
	repeat with v in L
		set end of out to my jn(contents of v)
	end repeat
	set AppleScript's text item delimiters to ","
	set s to "[" & (out as text) & "]"
	set AppleScript's text item delimiters to ""
	return s
end jlist

on posixOf(f)
	if f is missing value then return missing value
	try
		return POSIX path of f
	on error
		return missing value
	end try
end posixOf

on docPath(d)
	tell application "Keynote" to set f to file of d
	return my posixOf(f)
end docPath

on findDoc(key)
	tell application "Keynote"
		if (count of documents) is 0 then error "Keynote 里没有打开的文稿。先用 create_presentation 或 open_presentation。" number 9001
		if key is "" then return front document
		repeat with d in documents
			if (name of d) is key then return contents of d
			if (name of d) & ".key" is key then return contents of d
		end repeat
	end tell
	repeat with d in documents of application "Keynote"
		if my docPath(contents of d) is key then return contents of d
	end repeat
	error "找不到已打开的文稿：" & key number 9002
end findDoc

on guardDoc(d, rootsText)
	if rootsText is "*" then return
	set p to my docPath(d)
	if p is missing value then return
	set AppleScript's text item delimiters to linefeed
	set rs to text items of rootsText
	set AppleScript's text item delimiters to ""
	repeat with r in rs
		if (length of (r as text)) > 0 and p starts with (r as text) then return
	end repeat
	error "这份文稿不在允许修改的目录里（" & p & "），为保护原文件拒绝修改。请用 open_presentation 打开它，工具会先复制一份本机副本。" number 9003
end guardDoc

on getSlide(d, n)
	tell application "Keynote"
		set c to count of slides of d
		if n < 1 or n > c then error "幻灯片编号超出范围：" & n & "（这份文稿共 " & c & " 页）" number 9004
		return slide n of d
	end tell
end getSlide

on getItem(s, k)
	tell application "Keynote"
		set c to count of iWork items of s
		if k < 1 or k > c then error "对象编号超出范围：" & k & "（这一页共 " & c & " 个对象，先用 get_slide 查看）" number 9005
		return iWork item k of s
	end tell
end getItem

on tpJSON(tp)
	tell application "Keynote"
		set e to (transition effect of tp) as text
		set du to transition duration of tp
		set dl to transition delay of tp
		set au to automatic transition of tp
	end tell
	return "{\"effect\":" & my js(e) & ",\"duration\":" & my jn(du) & ",\"delay\":" & my jn(dl) & ",\"automatic\":" & my jb(au) & "}"
end tpJSON

on itemJSON(itm, idx, isFull)
	tell application "Keynote"
		set c to (class of itm) as text
		set j to "{\"item\":" & idx & ",\"type\":" & my js(c)
		try
			set p to position of itm
			set j to j & ",\"x\":" & my jn(item 1 of p) & ",\"y\":" & my jn(item 2 of p)
		end try
		try
			set j to j & ",\"width\":" & my jn(width of itm) & ",\"height\":" & my jn(height of itm)
		end try
		if c is "text item" or c is "shape" then
			try
				set j to j & ",\"text\":" & my js((object text of itm) as text)
			end try
			if isFull then
				try
					set j to j & ",\"font\":" & my js(font of object text of itm)
				end try
				try
					set j to j & ",\"size\":" & my jn(size of object text of itm)
				end try
				try
					set j to j & ",\"color\":" & my jlist(color of object text of itm)
				end try
			end if
		end if
		if c is "image" then
			try
				set j to j & ",\"file\":" & my js(file name of itm)
			end try
			try
				set j to j & ",\"description\":" & my js(description of itm)
			end try
		end if
		if c is "movie" or c is "audio clip" then
			try
				set j to j & ",\"file\":" & my js(file name of itm)
			end try
		end if
		if c is "line" then
			try
				set j to j & ",\"start\":" & my jlist(start point of itm) & ",\"end\":" & my jlist(end point of itm)
			end try
		end if
		if c is "table" then
			try
				set rc to row count of itm
				set cc to column count of itm
				set j to j & ",\"rows\":" & rc & ",\"columns\":" & cc
				if isFull and rc ≤ 30 and cc ≤ 12 then
					set rowsJ to {}
					repeat with r from 1 to rc
						set vals to {}
						repeat with k from 1 to cc
							set v to value of cell k of row r of itm
							if v is missing value then
								set end of vals to "null"
							else
								set end of vals to my js(v as text)
							end if
						end repeat
						set AppleScript's text item delimiters to ","
						set end of rowsJ to "[" & (vals as text) & "]"
						set AppleScript's text item delimiters to ""
					end repeat
					set AppleScript's text item delimiters to ","
					set j to j & ",\"cells\":[" & (rowsJ as text) & "]"
					set AppleScript's text item delimiters to ""
				end if
			end try
		end if
		if isFull then
			try
				set j to j & ",\"rotation\":" & my jn(rotation of itm)
			end try
			try
				set j to j & ",\"opacity\":" & my jn(opacity of itm)
			end try
			try
				set j to j & ",\"locked\":" & my jb(locked of itm)
			end try
		end if
	end tell
	return j & "}"
end itemJSON

on joinList(L, sep)
	set AppleScript's text item delimiters to sep
	set s to L as text
	set AppleScript's text item delimiters to ""
	return s
end joinList

on docJSON(d)
	tell application "Keynote"
		set j to "{\"name\":" & my js(name of d) & ",\"path\":" & my js(my docPath(d)) & ",\"width\":" & (width of d) & ",\"height\":" & (height of d) & ",\"slides\":" & (count of slides of d)
		try
			set j to j & ",\"theme\":" & my js(name of document theme of d)
		end try
		set ln to name of every slide layout of d
	end tell
	set q to {}
	repeat with x in ln
		set end of q to my js(contents of x)
	end repeat
	return j & ",\"layouts\":[" & my joinList(q, ",") & "]}"
end docJSON

on blankAfter(d, n)
	-- New empty slide right after slide n, made by duplicating slide n and clearing it,
	-- so it keeps that slide's background (Keynote's scripting cannot set backgrounds).
	tell application "Keynote"
		set src to slide n of d
		duplicate src to after src
		set b to slide (n + 1) of d
		try
			set title showing of b to false
		end try
		try
			set body showing of b to false
		end try
		repeat with k from (count of iWork items of b) to 1 by -1
			try
				delete iWork item k of b
			end try
		end repeat
		set presenter notes of b to ""
		return b
	end tell
end blankAfter

on highlightText(itm, needle, col)
	-- Colour the first occurrence of needle inside the item's text (e.g. the red F-number digits).
	tell application "Keynote" to set t to (object text of itm) as text
	set o to offset of needle in t
	if o is 0 then return false
	tell application "Keynote" to set color of characters o thru (o + (length of needle) - 1) of object text of itm to col
	return true
end highlightText

on layoutOf(d, nm)
	tell application "Keynote"
		set ln to name of every slide layout of d
		if ln does not contain nm then error "这个主题里没有版式「" & nm & "」。可用版式：" & my joinList(ln, "、") number 9006
		return slide layout nm of d
	end tell
end layoutOf
'''


class KeynoteError(Exception):
    """An error reported by Keynote or by our own AppleScript checks."""


_ERR_RE = re.compile(r"execution error: (.*?)(?: \((-?\d+)\))?\s*$", re.S)


def _clean_error(stderr):
    text = stderr.strip().splitlines()
    text = text[-1] if text else "osascript failed"
    m = _ERR_RE.search(text)
    if not m:
        return text
    msg, code = m.group(1), m.group(2)
    msg = msg.replace("Keynote got an error: ", "Keynote 报错：")
    if code == "-1743":
        msg += "（没有自动化权限：到 系统设置 > 隐私与安全性 > 自动化，允许当前程序控制 Keynote）"
    elif code == "-1712":
        msg += "（Keynote 响应超时，可能有对话框挡着，或者正在导出）"
    return msg


def run(body, args=(), timeout=120, as_json=True):
    """Run an AppleScript body inside `on run argv`. Returns parsed JSON or text."""
    secs = int(timeout) + 10
    script = (
        PRELUDE
        + "\non run argv\n"
        + "with timeout of %d seconds\n" % secs
        + body
        + "\nend timeout\nend run\n"
    )
    try:
        p = subprocess.run(
            ["osascript", "-"] + [str(a) for a in args],
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired:
        raise KeynoteError("Keynote 操作超时（%d 秒）" % timeout)
    if p.returncode != 0:
        if os.environ.get("KEYNOTE_MCP_DEBUG"):
            sys.stderr.write("---- failed AppleScript ----\n%s\nargv=%r\n%s\n" % (body, list(args), p.stderr))
        raise KeynoteError(_clean_error(p.stderr))
    out = p.stdout.rstrip("\n")
    if as_json and out[:1] in ("{", "["):
        try:
            return json.loads(out, strict=False)
        except ValueError:
            raise KeynoteError("无法解析 AppleScript 输出：" + out[:300])
    return out


def popen(body, args=(), timeout=7200):
    """Start a long AppleScript (e.g. movie export) in the background."""
    script = (
        PRELUDE
        + "\non run argv\n"
        + "with timeout of %d seconds\n" % int(timeout)
        + body
        + "\nend timeout\nend run\n"
    )
    fd, spath = tempfile.mkstemp(suffix=".applescript")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(script)
    out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    p = subprocess.Popen(["osascript", spath] + [str(a) for a in args], stdout=out, stderr=err)
    p.kn_files = (spath, out, err)
    return p


def collect(p):
    """After a popen() process has exited: return (stdout, stderr) and clean up."""
    spath, out, err = p.kn_files
    texts = []
    for fobj in (out, err):
        fobj.seek(0)
        texts.append(fobj.read().decode("utf-8", "replace"))
        fobj.close()
    try:
        os.remove(spath)
    except OSError:
        pass
    return texts[0], texts[1]


def clean_error(stderr):
    return _clean_error(stderr)
