import http.server, importlib.util, json, sys, tempfile, threading, urllib.parse, urllib.request, hashlib, base64
from pathlib import Path
spec = importlib.util.spec_from_file_location("spotipunk_fm", sys.argv[1]); fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
got = {}
class Acc(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        got.update({k: v[0] for k, v in form.items()})
        b = json.dumps({"access_token": "A", "refresh_token": "R", "expires_in": 3600}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
srv = http.server.HTTPServer(("127.0.0.1", 0), Acc); threading.Thread(target=srv.serve_forever, daemon=True).start()
tmp = Path(tempfile.mkdtemp()); fm.LOG_PATH = tmp / "log"
sp = fm.Spotify("c"*32, 18898, tokens_path=tmp/"t.json", accounts_url="http://127.0.0.1:%d" % srv.server_address[1])
auth = {}
def fake_browser(url):
    q = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(url).query).items()}; auth.update(q)
    def hit(): urllib.request.urlopen(q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": "CODE", "state": q["state"]})).read()
    threading.Timer(0.3, hit).start()
fm.webbrowser.open = fake_browser
sp.login()
ch = base64.urlsafe_b64encode(hashlib.sha256(got["code_verifier"].encode()).digest()).rstrip(b"=").decode()
ok = (auth["redirect_uri"] == "http://127.0.0.1:18898/callback" and auth["code_challenge"] == ch and got["code"] == "CODE"
      and "user-modify-playback-state" in auth["scope"] and json.loads((tmp/"t.json").read_text())["refresh_token"] == "R" and sp.has_login())
print("PKCE login flow:", "ok" if ok else "FAIL", auth.get("redirect_uri")); sys.exit(0 if ok else 1)
