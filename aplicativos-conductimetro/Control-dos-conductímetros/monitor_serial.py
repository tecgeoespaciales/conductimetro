from pathlib import Path
import datetime as dt
import serial, csv
import matplotlib.pyplot as plt

PORT = "/dev/ttyUSB0"
BAUD = 115200

BASE = Path.home() / "proyecto_sensores" / "experimentos"
RUN  = BASE / dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
DATA = RUN / "datos"; FIGS = RUN / "graficas"
DATA.mkdir(parents=True, exist_ok=True); FIGS.mkdir(parents=True, exist_ok=True)
CSV_OUT = DATA / "captura_serial.csv"
RAW_LOG = DATA / "raw.log"

ser = serial.Serial(PORT, BAUD, timeout=1)
try:
    ser.dtr = False; ser.rts = False
except Exception:
    pass
ser.reset_input_buffer()

with open(CSV_OUT, "w", newline="") as f:
    csv.writer(f).writerow(["idx","valor","t_pc"])

plt.ion(); plt.figure()
xs = []; ys = []; samples = 0
print(f"[INFO] Grabando en: {CSV_OUT}")

try:
    while True:
        raw = ser.readline().decode(errors="ignore").strip()
        if raw:
            print("[RAW]", raw)
            with open(RAW_LOG, "a") as rf:
                rf.write(raw + "\n")
        else:
            continue

        if "," not in raw:
            continue
        p = raw.split(",")
        if len(p) < 2:
            continue
        try:
            idx = int(p[0].strip())
            val = float(p[1].strip())
        except:
            continue

        tpc = dt.datetime.now().isoformat(timespec="seconds")
        with open(CSV_OUT, "a", newline="") as f:
            csv.writer(f).writerow([idx, val, tpc])

        xs.append(idx); ys.append(val); samples += 1
        if samples % 5 == 0:
            plt.clf(); plt.plot(xs[-500:], ys[-500:])
            plt.title("Conductividad (idx vs valor)")
            plt.xlabel("idx"); plt.ylabel("uS")
            plt.tight_layout(); plt.pause(0.05)
        if samples % 600 == 0:
            plt.savefig(FIGS / f"graf_{samples:05d}.png", dpi=120)
except KeyboardInterrupt:
    print("\n[INFO] stop")
finally:
    ser.close()