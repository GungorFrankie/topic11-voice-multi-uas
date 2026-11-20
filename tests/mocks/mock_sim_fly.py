# Stand-in for the control services (phases, tasks, timed navigate) to test fly_mission.py
import json, math, threading, time, sys, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
T0=time.time(); RTF=4.0
STARTS={5000:(0.5,1.0),5001:(-0.5,1.0),5002:(-1.0,0.5),5003:(-1.0,-0.5),5004:(-0.5,-1.0),5005:(0.5,-1.0),5006:(1.0,-0.5),5007:(1.0,0.5)}
PUB={"taking_off":"flying","navigating":"flying","landed":"landing"}
def now(): return (time.time()-T0)*RTF
class D:
    def __init__(s,port): s.pos=[*STARTS[port],0.0]; s.phase="active"; s.tasks={}; s.cur=None; s.id=f"cf{231+port-5000}"; s.lock=threading.Lock()
    def move(s,tid,target,dur,endphase):
        p0=list(s.pos); t0=now(); s.tasks[tid]["status"]="running"
        while True:
            k=min(1,(now()-t0)/max(dur,0.01)); s.pos=[a+(b-a)*k for a,b in zip(p0,target)]
            if k>=1: break
            time.sleep(0.01)
        s.phase=endphase; s.tasks[tid]["status"]="completed"
    def submit(s,phase,target,dur,endphase):
        if s.cur and s.tasks[s.cur]["status"] in("queued","running"): raise ValueError("Drone already has a motion task")
        tid=uuid.uuid4().hex; s.tasks[tid]={"task_id":tid,"status":"queued","error":None}; s.cur=tid; s.phase=phase
        threading.Thread(target=s.move,args=(tid,target,dur,endphase),daemon=True).start(); return tid
def serve(port):
    d=D(port)
    class H(BaseHTTPRequestHandler):
        def log_message(self,*a): pass
        def out(self,code,obj):
            b=json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        def snap(self): return {"drone_id":d.id,"connected":True,"error":None,"state":PUB.get(d.phase,d.phase),"phase":d.phase,"sim_time":round(now(),3),"telemetry":{"battery":4.2,"position":d.pos}}
        def do_GET(self):
            if self.path.startswith("/tasks/"):
                t=d.tasks.get(self.path.split("/")[-1]); return self.out(200,t) if t else self.out(404,{"error":"Unknown task"})
            self.out(200,self.snap())
        def do_POST(self):
            n=int(self.headers.get("Content-Length",0)); body=(json.loads(self.rfile.read(n)) if n else {}) or {}
            p=self.path.strip("/")
            try:
                tid=None
                if p=="activate_idle":
                    if d.cur and d.tasks[d.cur]["status"] in("queued","running"): raise ValueError("Cannot activate idle during a task")
                    d.phase="idle"
                elif p=="begin_takeoff":
                    if d.phase not in("idle","landed"): raise ValueError("Takeoff requires idle or landed state")
                    tid=d.submit("taking_off",[d.pos[0],d.pos[1],body.get("height",0.5)],body.get("duration",2.0),"hovering")
                elif p=="navigate":
                    if d.phase not in("hovering","navigating"): raise ValueError("Navigation requires hovering or navigating state")
                    tid=d.submit("navigating",[body["x"],body["y"],body["z"]],body.get("duration",10.0),"hovering")
                elif p=="begin_landing": tid=d.submit("landing",[d.pos[0],d.pos[1],0.02],body.get("duration",2.0),"landed")
                elif p=="stop_controller": pass
                else: return self.out(404,{"error":"not found"})
                r=self.snap(); r["task_id"]=tid; self.out(200,r)
            except (ValueError,KeyError) as e: self.out(409,{"error":str(e)})
    ThreadingHTTPServer(("127.0.0.1",port),H).serve_forever()
for p in STARTS: threading.Thread(target=serve,args=(p,),daemon=True).start()
time.sleep(float(sys.argv[1]) if len(sys.argv)>1 else 300)
