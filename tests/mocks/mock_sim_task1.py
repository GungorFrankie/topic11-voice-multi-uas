# Minimal stand-in for the control services, only to test my_mission.py logic.
import json, math, threading, time, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
T0=time.time()
STARTS={5000:(0.5,1.0),5001:(-0.5,1.0),5002:(-1.0,0.5)}
class D:
    def __init__(s,port):
        s.pos=[*STARTS[port],0.135]; s.state="active"; s.target=None; s.id=f"cf{231+port-5000}"
    def step(s,dt):
        if s.target:
            d=[t-p for t,p in zip(s.target,s.pos)]; n=math.sqrt(sum(v*v for v in d))
            if n<0.02:
                s.pos=list(s.target); s.target=None
                s.state="active" if s.state=="landing" else "hovering"
            else:
                k=min(1,0.6*dt/n); s.pos=[p+v*k for p,v in zip(s.pos,d)]
def serve(port):
    d=D(port)
    def loop():
        while True: d.step(0.02); time.sleep(0.02)
    threading.Thread(target=loop,daemon=True).start()
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def out(self,code,obj):
            b=json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        def do_GET(self):
            self.out(200,{"drone_id":d.id,"connected":True,"error":None,"state":d.state,"sim_time":round((time.time()-T0)*0.5,3),"telemetry":{"battery":4.2,"position":d.pos}})
        def do_POST(self):
            n=int(self.headers.get("Content-Length",0)); body=json.loads(self.rfile.read(n) or b"null") if n else None
            p=self.path.strip("/")
            if p=="activate_idle":
                if d.state=="landing": return self.out(409,{"error":"busy"})
                d.state="idle"
            elif p=="begin_takeoff":
                if d.state!="idle": return self.out(409,{"error":"not idle"})
                d.state="flying"; d.target=[d.pos[0],d.pos[1],0.4]
            elif p.startswith("navigate"):
                parts=p.split("/")
                d.target=[float(v) for v in parts[1:4]] if len(parts)==4 else [body["x"],body["y"],body["z"]]; d.state="flying"
            elif p=="begin_landing":
                d.state="landing"; d.target=[d.pos[0],d.pos[1],0.135]
            else: return self.out(404,{})
            self.out(200,{"state":d.state})
    ThreadingHTTPServer(("127.0.0.1",port),H).serve_forever()
for p in STARTS: threading.Thread(target=serve,args=(p,),daemon=True).start()
time.sleep(float(sys.argv[1]) if len(sys.argv)>1 else 300)
