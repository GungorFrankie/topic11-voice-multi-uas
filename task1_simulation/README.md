# Task 1 · First multi-drone flight

`my_mission.py` checks the status of cf231–cf233, flies each drone on its own altitude layer
(0.5 / 0.8 / 1.1 m) out to a point and back, lands them, logs positions to CSV, prints a summary
(duration, smallest distance between flying drones) and saves a plot.

```bash
python3 my_mission.py      # simulator must show READY
```

All waiting uses the simulation clock (`sim_time` from `/status`), so the script also works when the
simulation runs slower than real time. Set-up and pitfalls: [../docs/01_setup.md](../docs/01_setup.md).
Result: [../figures/task1_three_drones.png](../figures/task1_three_drones.png).
