"""Settings: which folders may be written, Windows path mapping, optional script tool."""
import os
import re
import shutil

DEFAULT_WORKSPACE = os.path.expanduser("~/KeynoteMCP")


def _norm_dir(p):
    p = os.path.abspath(os.path.expanduser(p))
    return p.rstrip("/") + "/"


class Config:
    def __init__(self, roots=None, path_map=None, allow_script=False, allow_movie=False):
        roots = [r for r in (roots or []) if r]
        if not roots:
            roots = [DEFAULT_WORKSPACE]
        self.workspace = os.path.abspath(os.path.expanduser(roots[0]))
        self.roots = []
        for r in roots:
            for form in (_norm_dir(r), _norm_dir(os.path.realpath(os.path.expanduser(r)))):
                if form not in self.roots:
                    self.roots.append(form)
        # [(client_prefix, mac_prefix)], e.g. ("Z:\\", "/Users/me/KeynoteMCP/")
        self.path_map = path_map or []
        self.allow_script = allow_script
        # Movie export is off by default: a person reviews the deck and exports by hand.
        self.allow_movie = allow_movie

    @classmethod
    def from_env(cls, argv=None):
        roots, maps, allow = [], [], False
        env_roots = os.environ.get("KEYNOTE_MCP_ROOTS", "")
        if env_roots:
            roots += [r for r in env_roots.split(os.pathsep) if r.strip()]
        env_map = os.environ.get("KEYNOTE_MCP_PATH_MAP", "")
        if env_map:
            maps += [m for m in env_map.split(";") if "=" in m]
        allow = os.environ.get("KEYNOTE_MCP_ALLOW_SCRIPT", "") in ("1", "true", "yes")
        movie = os.environ.get("KEYNOTE_MCP_ALLOW_MOVIE", "") in ("1", "true", "yes")
        args = list(argv or [])
        i = 0
        while i < len(args):
            a = args[i]
            if a == "--root" and i + 1 < len(args):
                roots.append(args[i + 1])
                i += 1
            elif a == "--path-map" and i + 1 < len(args):
                maps.append(args[i + 1])
                i += 1
            elif a == "--allow-script":
                allow = True
            elif a == "--allow-movie":
                movie = True
            i += 1
        pm = []
        for m in maps:
            client, mac = m.split("=", 1)
            pm.append((client.strip(), _norm_dir(mac.strip())))
        return cls(roots=roots, path_map=pm, allow_script=allow, allow_movie=movie)

    def ensure_workspace(self):
        os.makedirs(self.workspace, exist_ok=True)
        return self.workspace

    # ---- paths -----------------------------------------------------------
    def to_local(self, path):
        """Turn a path from the client (maybe a Windows path) into a Mac path."""
        if path is None:
            return None
        p = str(path).strip()
        for client, mac in self.path_map:
            c = client.replace("/", "\\").rstrip("\\")
            q = p.replace("/", "\\")
            if q.lower() == c.lower() or q.lower().startswith(c.lower() + "\\"):
                rest = q[len(c):].lstrip("\\").replace("\\", "/")
                return os.path.join(mac, rest) if rest else mac.rstrip("/")
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            p = os.path.join(self.workspace, p)
        return os.path.abspath(p)

    def map_in(self, value):
        """Map only values that start with a configured client prefix (used for `document`)."""
        if not isinstance(value, str) or not self.path_map:
            return value
        q = value.strip().replace("/", "\\").lower()
        for client, _mac in self.path_map:
            c = client.replace("/", "\\").rstrip("\\").lower()
            if q == c or q.startswith(c + "\\"):
                return self.to_local(value)
        return value

    def to_client(self, path):
        if not path or not self.path_map or not isinstance(path, str):
            return path
        for client, mac in self.path_map:
            if path.startswith(mac) or path + "/" == mac:
                rest = path[len(mac):]
                sep = "\\" if re.match(r"^[A-Za-z]:", client) or "\\" in client else "/"
                base = client.rstrip("\\/")
                return base + sep + rest.replace("/", sep) if rest else base + sep
        return path

    def in_roots(self, path):
        p = os.path.abspath(path)
        rp = os.path.realpath(p)
        for r in self.roots:
            if (p + "/").startswith(r) or (rp + "/").startswith(r):
                return True
        return False

    def check_writable(self, path):
        if not self.in_roots(path):
            raise PermissionError(
                "不允许写到这个位置：%s。允许的目录：%s。需要的话在配置里用 KEYNOTE_MCP_ROOTS 添加目录。"
                % (path, ", ".join(sorted(set(r.rstrip('/') for r in self.roots))))
            )
        return path

    def roots_text(self):
        return "\n".join(self.roots)

    def free_bytes(self, path):
        d = path if os.path.isdir(path) else os.path.dirname(path) or "/"
        while not os.path.exists(d):
            d = os.path.dirname(d)
        return shutil.disk_usage(d).free
