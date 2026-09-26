"""Inspect CoA search-form HTML and save its dropdown value/label tables (GET only)."""
import json
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE="https://coa.gov.in/search_arch2.php?lang=1&level=1&lid=289&searCat={}"

class Forms(HTMLParser):
    def __init__(self): super().__init__(); self.select=None; self.items={}; self.fields=[]; self.option=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ("input","select","textarea"):
            self.fields.append({"tag":tag,"name":a.get("name"),"type":a.get("type", "select" if tag=="select" else "text"),"id":a.get("id"),"validation":a.get("class")})
        if tag=="select": self.select=a.get("name"); self.items.setdefault(self.select,[])
        elif tag=="option" and self.select is not None: self.option=[a.get("value", ""),""]
    def handle_data(self,data):
        if self.option is not None: self.option[1]+=data
    def handle_endtag(self,tag):
        if tag=="option" and self.option is not None:
            self.items[self.select].append([self.option[0]," ".join(self.option[1].split())]); self.option=None
        elif tag=="select": self.select=None

def main():
    result={"source":"CoA form GETs; no search POSTs submitted","forms":{}}
    for n in range(1,7):
        req=urllib.request.Request(BASE.format(n),headers={"User-Agent":"CoA directory pipeline research; contact project owner"})
        with urllib.request.urlopen(req,timeout=30) as response: html=response.read().decode("utf-8","replace")
        parser=Forms(); parser.feed(html)
        result["forms"][str(n)]={"fields":parser.fields,"selects":parser.items,"html_contains_captcha":"captcha" in html.lower()}
        print(f"searCat={n}: fields={', '.join(str(f['name']) for f in parser.fields if f['name'])}; selects={', '.join(str(x) for x in parser.items)}")
    out=Path(__file__).with_name("form_lookups.json"); out.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(f"Saved dropdown tables to {out}")
if __name__=="__main__": main()
