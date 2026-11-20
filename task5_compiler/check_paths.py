"""Independent separation check for 4DPlanning path files (also *_outbound / *_return).
Assumes: cell k of an agent is occupied at time step (start_time + k);
after its last cell an agent stays at its goal (planner semantics).
Usage: python3 check_paths.py PATHS_FILE"""
import re, sys, math, itertools

def load(path):
    agents = []
    for line in open(path):
        m = re.match(r"Agent (\d+) \[Radius: ([\d.]+), Speed: ([\d.]+), Start Time: (\d+)\][^:]*:(.*)", line.strip())
        if not m:
            continue
        cells = [tuple(map(int, c)) for c in re.findall(r"\((-?\d+),(-?\d+),(-?\d+)\)", m.group(5))]
        agents.append(dict(id=int(m.group(1)), r=float(m.group(2)), v=float(m.group(3)),
                           t0=int(m.group(4)), cells=cells))
    return agents

def pos(a, t):
    k = t - a["t0"]
    if k < 0:
        return None                      # not yet started
    return a["cells"][min(k, len(a["cells"]) - 1)]

agents = load(sys.argv[1])
if not agents:
    sys.exit("no agent lines found")
t_end = max(a["t0"] + len(a["cells"]) for a in agents)
print(f"{len(agents)} agents, horizon {t_end} steps")
bad = 0
for a, b in itertools.combinations(agents, 2):
    worst = (math.inf, None)
    for t in range(t_end):
        p, q = pos(a, t), pos(b, t)
        if p is None or q is None:
            continue
        d = math.dist(p, q)
        if d < worst[0]:
            worst = (d, t)
        if d < a["r"] + b["r"]:
            bad += 1
            print(f"  CONFLICT agents {a['id']}-{b['id']} at t={t}: {p} vs {q}, distance {d:.2f} < {a['r']+b['r']:.2f}")
    print(f"agents {a['id']}-{b['id']}: minimum distance {worst[0]:.2f} cubes at t={worst[1]}")
print("RESULT:", "OK, no conflicts" if bad == 0 else f"{bad} conflict steps")
