"""Local fixture server for Track A. Never contacts coa.gov.in."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pipeline import mock_html

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        target=parse_qs(urlparse(self.path).query).get("target",["demo"])[0]
        if "fail" in target.lower():
            self.send_response(503); self.end_headers(); self.wfile.write(b"simulated transient failure"); return
        if "captcha" in target.lower():
            body=b"<html><title>Verification required</title><p>Simulated CAPTCHA-required response</p></html>"
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body); return
        body=mock_html(target).encode()
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("--port",type=int,default=8765); a=p.parse_args()
    print(f"Local mock fixture server at http://127.0.0.1:{a.port}/result?target=demo")
    ThreadingHTTPServer(("127.0.0.1",a.port),Handler).serve_forever()
