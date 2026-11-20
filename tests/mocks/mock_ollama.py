# Stand-in for Ollama: answers like qwen3:4b did in run v2, and reacts to feedback in simple ways.
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
T=lambda i,u,a,**k: dict(id=f"t{i}",uas=u,action=a,**k)
def deliv(u,p,dep,dest,start=1,after=None,hold=False):
    seq=[("takeoff",{}),("goto",{"target":dep}),("pickup",{"parcel":p}),("goto",{"target":dest})]+([("hold",{"duration_s":10})] if hold else [])+[("dropoff",{"parcel":p}),("land",{})]
    out=[]
    for k,(a,kw) in enumerate(seq):
        t=T(start+k,u,a,**kw)
        if k==0 and after: t["after"]=after
        out.append(t)
    return out
def answer(req, fb):
    if "then" in req: return {"tasks":deliv("cf233","p1","depot_a","hospital")+deliv("cf231","p2","depot_b","lab",7,["t5"])}
    if "same time" in req:
        if fb and "must end with land" in fb: return {"tasks":deliv("cf233","p1","depot_a","lab")+deliv("cf231","p2","depot_b","hospital",7)}
        t=deliv("cf233","p1","depot_a","lab")[:5]+deliv("cf231","p2","depot_b","hospital",6); t.append(T(12,"cf231","land")); return {"tasks":t}
    if "heavy" in req:
        if fb and "payload" in fb: return {"reasoning":"p3 is too heavy for every UAS.","tasks":[]}
        return {"tasks":deliv("cf233","p3","depot_a","hospital")}
    if "warehouse" in req: return {"tasks":deliv("cf231","p2","depot_b","warehouse")}   # stubborn: never fixes it
    if "cf232" in req:
        if fb and "battery" in fb: return {"tasks":deliv("cf231","p2","depot_b","lab")}  # picks another drone
        return {"tasks":deliv("cf232","p2","depot_b","lab")}
    if "wait" in req: return {"tasks":deliv("cf231","p2","depot_b","hospital",hold=True)}
    return {"tasks":deliv("cf233","p1","depot_a","hospital")}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,o):
        b=json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self): self.send({"version":"mock"})
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs=body["messages"]; req=msgs[1]["content"].rsplit("REQUEST\n",1)[1]
        fb=msgs[-1]["content"] if len(msgs)>2 else None
        a=answer(req,fb); a.setdefault("reasoning","r"); a["mission_id"]="m"
        self.send({"message":{"role":"assistant","content":json.dumps(a)},"load_duration":0,"eval_count":280,"eval_duration":28e9})
HTTPServer(("127.0.0.1",11434),H).serve_forever()
