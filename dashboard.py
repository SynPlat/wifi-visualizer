import tkinter as tk
from tkinter import ttk
import subprocess
import re
import threading
import time
from collections import deque, defaultdict

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


# ============================================================
# SETTINGS
# ============================================================

UPDATE_MS = 2000
PING_INTERVAL = 3
PING_HOST = "1.1.1.1"
MAX_POINTS = 30

signal_history = deque(maxlen=MAX_POINTS)
noise_history = deque(maxlen=MAX_POINTS)
elapsed_history = deque(maxlen=MAX_POINTS)

ping_history = deque(maxlen=30)
ping_total = 0
ping_success = 0
start_time = time.time()


# ============================================================
# WIFI DATA
# ============================================================

def get_wifi_info():
    try:
        result = subprocess.run(
            ["sudo", "-n", "/usr/bin/wdutil", "info"],
            capture_output=True,
            text=True,
            timeout=5
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    output = result.stdout

    def value(name):
        m = re.search(
            rf"^\s*{re.escape(name)}\s*:\s*(.+)$",
            output,
            re.MULTILINE
        )
        return m.group(1).strip() if m else "Unknown"

    return {
        "rssi": value("RSSI"),
        "noise": value("Noise"),
        "channel": value("Channel"),
        "tx": value("Tx Rate"),
        "phy": value("PHY Mode")
    }


# ============================================================
# SIGNAL HELPERS
# ============================================================

def signal_percent(rssi):
    if rssi >= -50:
        return 100
    if rssi >= -60:
        return int(85 + (rssi + 60) * 1.5)
    if rssi >= -70:
        return int(65 + (rssi + 70) * 2)
    if rssi >= -80:
        return int(40 + (rssi + 80) * 2.5)
    if rssi >= -90:
        return int(10 + (rssi + 90) * 3)
    return 0


def noise_percent(noise):
    if noise <= -90:
        return 100
    if noise <= -80:
        return int(70 + (-80 - noise) * 3)
    if noise <= -70:
        return int(40 + (-70 - noise) * 3)
    if noise <= -60:
        return int(10 + (-60 - noise) * 3)
    return 0


def snr_percent(snr):
    return max(0, min(100, int(snr / 30 * 100)))


def quality(rssi):
    if rssi >= -50:
        return "EXCELLENT", "🟢"
    if rssi >= -60:
        return "GOOD", "🟢"
    if rssi >= -70:
        return "FAIR", "🟡"
    if rssi >= -80:
        return "WEAK", "🟠"
    return "POOR", "🔴"


def snr_quality(snr):
    if snr >= 30:
        return "EXCELLENT", "🟢"
    if snr >= 20:
        return "GOOD", "🟢"
    if snr >= 10:
        return "FAIR", "🟡"
    return "POOR", "🔴"


def band_from_channel(channel):
    if channel.startswith("2g"):
        return "2.4 GHz"
    if channel.startswith("5g"):
        return "5 GHz"
    if channel.startswith("6g"):
        return "6 GHz"
    return "Unknown"


# ============================================================
# PING
# ============================================================

def ping_worker():
    global ping_total, ping_success

    while True:
        try:
            result = subprocess.run(
                ["ping", "-c", "1", "-W", "1000", PING_HOST],
                capture_output=True,
                text=True,
                timeout=4
            )

            ping_total += 1

            if result.returncode == 0:
                match = re.search(
                    r"time[=<]([\d.]+)\s*ms",
                    result.stdout
                )
                if match:
                    ping_history.append(float(match.group(1)))
                    ping_success += 1

        except Exception:
            ping_total += 1

        time.sleep(PING_INTERVAL)


threading.Thread(target=ping_worker, daemon=True).start()


# ============================================================
# SCANNER
# ============================================================

def scan_networks():
    try:
        result = subprocess.run(
            ["system_profiler", "SPAirPortDataType"],
            capture_output=True,
            text=True,
            timeout=15
        )
    except Exception:
        return []

    if result.returncode != 0:
        return []

    networks = []
    current = None
    inside = False

    for raw in result.stdout.splitlines():
        stripped = raw.strip()

        if stripped == "Other Local Wi-Fi Networks:":
            inside = True
            continue

        if not inside:
            continue

        if stripped.startswith("Current Network Information:"):
            break

        # Network name lines in system_profiler output are indented
        if (
            raw.startswith("        ")
            and stripped.endswith(":")
            and not stripped.startswith((
                "PHY Mode:",
                "Channel:",
                "Network Type:",
                "Security:",
                "Signal / Noise:",
                "Transmit Rate:"
            ))
        ):
            if current:
                networks.append(current)

            current = {
                "ssid": stripped[:-1],
                "channel": "?",
                "band": "?",
                "signal": None,
                "security": "?"
            }
            continue

        if current is None:
            continue

        if stripped.startswith("Channel:"):
            ch = stripped.split(":", 1)[1].strip()
            current["channel"] = ch

            if "2GHz" in ch:
                current["band"] = "2.4 GHz"
            elif "5GHz" in ch:
                current["band"] = "5 GHz"
            elif "6GHz" in ch:
                current["band"] = "6 GHz"

        elif stripped.startswith("Signal / Noise:"):
            nums = re.findall(
                r"-?\d+",
                stripped.split(":", 1)[1]
            )
            if nums:
                current["signal"] = int(nums[0])

        elif stripped.startswith("Security:"):
            current["security"] = stripped.split(
                ":", 1
            )[1].strip()

    if current:
        networks.append(current)

    return [
        n for n in networks
        if n["signal"] is not None
    ]


def channel_analysis(networks):
    channels = defaultdict(list)

    for n in networks:
        match = re.search(r"\b(\d+)\b", n["channel"])
        if match:
            channels[int(match.group(1))].append(n)

    return channels


def recommended_channel(networks):
    channels = channel_analysis(networks)

    if not channels:
        return None

    scores = {}

    for ch, nets in channels.items():
        score = len(nets) * 5
        for n in nets:
            if n["signal"] >= -60:
                score += 6
            elif n["signal"] >= -70:
                score += 4
            elif n["signal"] >= -80:
                score += 2
        scores[ch] = score

    return min(scores, key=scores.get)


# ============================================================
# UI HELPERS
# ============================================================

BG = "#0d1117"
PANEL = "#151b21"
CYAN = "#00e5ff"
WHITE = "#f2f2f2"
MUTED = "#858b92"
BAR_BG = "#252c33"


def label(parent, text="", size=10, bold=False, fg=WHITE, **kwargs):
    return tk.Label(
        parent,
        text=text,
        font=("Helvetica", size, "bold" if bold else "normal"),
        fg=fg,
        bg=PANEL,
        **kwargs
    )


# ============================================================
# MAIN WINDOW
# ============================================================

root = tk.Tk()
root.title("📡 Wi-Fi Visualizer")
root.geometry("1250x900")
root.minsize(1050, 780)
root.configure(bg=BG)


title = tk.Label(
    root,
    text="📡 WI-FI VISUALIZER",
    font=("Helvetica", 27, "bold"),
    fg=CYAN,
    bg=BG
)
title.pack(pady=(10, 0))

status = tk.Label(
    root,
    text="Connecting...",
    font=("Helvetica", 11),
    fg=MUTED,
    bg=BG
)
status.pack(pady=(0, 5))


# ============================================================
# SCAN BUTTON
# ============================================================

def open_scanner():
    win = tk.Toplevel(root)
    win.title("📡 Wi-Fi Environment Scanner")
    win.geometry("1050x700")
    win.configure(bg=BG)

    tk.Label(
        win,
        text="📡 WI-FI ENVIRONMENT",
        font=("Helvetica", 22, "bold"),
        fg=CYAN,
        bg=BG
    ).pack(pady=(15, 5))

    container = tk.Frame(win, bg=PANEL)
    container.pack(fill="both", expand=True, padx=20, pady=10)

    columns = ("ssid", "channel", "band", "signal", "security")

    tree = ttk.Treeview(
        container,
        columns=columns,
        show="headings"
    )

    headings = {
        "ssid": "SSID",
        "channel": "CHANNEL",
        "band": "BAND",
        "signal": "SIGNAL",
        "security": "SECURITY"
    }

    widths = {
        "ssid": 300,
        "channel": 120,
        "band": 150,
        "signal": 140,
        "security": 220
    }

    for c in columns:
        tree.heading(c, text=headings[c])
        tree.column(c, width=widths[c], anchor="center")

    tree.pack(fill="both", expand=True, padx=10, pady=10)

    result_label = tk.Label(
        win,
        text="",
        font=("Helvetica", 11, "bold"),
        fg=CYAN,
        bg=BG
    )
    result_label.pack(pady=(0, 5))

    def do_scan():
        result_label.config(text="🔄 Scanning...")
        win.update_idletasks()

        for item in tree.get_children():
            tree.delete(item)

        nets = scan_networks()

        for n in nets:
            tree.insert(
                "",
                "end",
                values=(
                    n["ssid"],
                    n["channel"],
                    n["band"],
                    f"{n['signal']} dBm",
                    n["security"]
                )
            )

        rec = recommended_channel(nets)

        if rec is not None:
            channels = channel_analysis(nets)
            count = len(channels[rec])

            if count <= 1:
                state = "🟢 LOW"
            elif count == 2:
                state = "🟡 MEDIUM"
            else:
                state = "🔴 HIGH"

            result_label.config(
                text=(
                    f"💡 Recommended Channel: {rec}   •   "
                    f"{state} congestion   •   "
                    f"{len(nets)} networks found"
                )
            )
        else:
            result_label.config(
                text=f"{len(nets)} networks found"
            )

    tk.Button(
        win,
        text="🔍 SCAN NETWORKS",
        command=do_scan,
        font=("Helvetica", 11, "bold"),
        fg="white",
        bg="#1c6575",
        activebackground="#24859a",
        relief="flat",
        padx=20,
        pady=7
    ).pack(pady=(0, 12))

    do_scan()


scan_button = tk.Button(
    root,
    text="🔍 SCAN NEARBY NETWORKS",
    command=open_scanner,
    font=("Helvetica", 11, "bold"),
    fg="white",
    bg="#1c6575",
    activebackground="#24859a",
    relief="flat",
    padx=18,
    pady=6
)
scan_button.pack(pady=(3, 8))


# ============================================================
# TOP CONTENT: LEFT INFO + GRAPH
# ============================================================

top = tk.Frame(root, bg=BG)
top.pack(fill="both", expand=True, padx=15, pady=(0, 8))

left = tk.Frame(
    top,
    bg=PANEL,
    width=285
)
left.pack(side="left", fill="y", padx=(0, 12))
left.pack_propagate(False)

right = tk.Frame(
    top,
    bg=PANEL
)
right.pack(side="left", fill="both", expand=True)


# ============================================================
# COMPACT INFO PANEL
# ============================================================

tk.Label(
    left,
    text="CONNECTION",
    font=("Helvetica", 13, "bold"),
    fg=CYAN,
    bg=PANEL
).pack(anchor="w", padx=15, pady=(10, 5))


info_values = {}


def add_info(name):
    f = tk.Frame(left, bg=PANEL)
    f.pack(fill="x", padx=15, pady=1)

    tk.Label(
        f,
        text=name,
        font=("Helvetica", 9),
        fg=MUTED,
        bg=PANEL
    ).pack(anchor="w")

    v = tk.Label(
        f,
        text="--",
        font=("Helvetica", 12, "bold"),
        fg=WHITE,
        bg=PANEL
    )
    v.pack(anchor="w")

    info_values[name] = v


for name in (
    "Signal",
    "Noise",
    "SNR",
    "Channel",
    "Band",
    "Tx Rate",
    "Wi-Fi Standard"
):
    add_info(name)


tk.Label(
    left,
    text="SIGNAL QUALITY",
    font=("Helvetica", 13, "bold"),
    fg=CYAN,
    bg=PANEL
).pack(anchor="w", padx=15, pady=(8, 2))


quality_label = tk.Label(
    left,
    text="--",
    font=("Helvetica", 16, "bold"),
    fg=WHITE,
    bg=PANEL
)
quality_label.pack(anchor="w", padx=15)


snr_quality_label = tk.Label(
    left,
    text="SNR: --",
    font=("Helvetica", 9, "bold"),
    fg=CYAN,
    bg=PANEL
)
snr_quality_label.pack(anchor="w", padx=15, pady=(1, 5))


# ============================================================
# GRAPH
# ============================================================

figure = Figure(figsize=(8, 4.7), dpi=100)
figure.patch.set_facecolor(PANEL)

ax = figure.add_subplot(111)
ax.set_facecolor(PANEL)
ax.set_ylim(-100, -30)
ax.set_xlabel("Elapsed Time", color=WHITE)
ax.set_ylabel("dBm", color=WHITE)
ax.tick_params(colors=WHITE)
ax.grid(True, alpha=0.18)

for spine in ax.spines.values():
    spine.set_color("#444444")

signal_line, = ax.plot(
    [], [],
    marker="o",
    linewidth=2,
    label="Signal"
)

noise_line, = ax.plot(
    [], [],
    marker="o",
    linewidth=2,
    label="Noise"
)

legend = ax.legend()
legend.get_frame().set_facecolor(PANEL)

for t in legend.get_texts():
    t.set_color(WHITE)

canvas = FigureCanvasTkAgg(
    figure,
    master=right
)
canvas.get_tk_widget().pack(
    fill="both",
    expand=True,
    padx=5,
    pady=5
)


# ============================================================
# BOTTOM COMPACT PANELS
# ============================================================

bottom = tk.Frame(root, bg=BG)
bottom.pack(fill="x", padx=15, pady=(0, 10))


performance = tk.Frame(bottom, bg=PANEL)
performance.pack(
    side="left",
    fill="both",
    expand=True,
    padx=(0, 6)
)

analysis = tk.Frame(bottom, bg=PANEL)
analysis.pack(
    side="left",
    fill="both",
    expand=True,
    padx=(6, 0)
)


# Performance
tk.Label(
    performance,
    text="NETWORK PERFORMANCE",
    font=("Helvetica", 11, "bold"),
    fg=CYAN,
    bg=PANEL
).pack(anchor="w", padx=12, pady=(7, 2))

performance_label = tk.Label(
    performance,
    text="🔄 Testing...",
    font=("Helvetica", 11, "bold"),
    fg=WHITE,
    bg=PANEL
)
performance_label.pack(anchor="w", padx=12, pady=(0, 7))


# Analysis
tk.Label(
    analysis,
    text="CONNECTION ANALYSIS",
    font=("Helvetica", 11, "bold"),
    fg=CYAN,
    bg=PANEL
).pack(anchor="w", padx=12, pady=(7, 2))

health_label = tk.Label(
    analysis,
    text="Health Score: --",
    font=("Helvetica", 13, "bold"),
    fg=CYAN,
    bg=PANEL
)
health_label.pack(anchor="w", padx=12)

details_label = tk.Label(
    analysis,
    text="Signal --%  •  Noise --%  •  SNR --%  •  Stability --%",
    font=("Helvetica", 9),
    fg=MUTED,
    bg=PANEL
)
details_label.pack(anchor="w", padx=12, pady=(1, 2))

recommendation_label = tk.Label(
    analysis,
    text="Analyzing...",
    font=("Helvetica", 9, "bold"),
    fg=WHITE,
    bg=PANEL
)
recommendation_label.pack(anchor="w", padx=12, pady=(0, 7))


# ============================================================
# LIVE UPDATE
# ============================================================

def update_dashboard():
    data = get_wifi_info()

    if not data:
        status.config(text="⚠️ Unable to read Wi-Fi information")
        root.after(UPDATE_MS, update_dashboard)
        return

    try:
        rssi = int(re.findall(r"-?\d+", data["rssi"])[0])
    except Exception:
        rssi = -100

    try:
        noise = int(re.findall(r"-?\d+", data["noise"])[0])
    except Exception:
        noise = -90

    snr = rssi - noise

    elapsed = int(time.time() - start_time)

    signal_history.append(rssi)
    noise_history.append(noise)
    elapsed_history.append(elapsed)

    sp = max(0, min(100, signal_percent(rssi)))
    np = max(0, min(100, noise_percent(noise)))
    snrp = snr_percent(snr)

    if len(signal_history) >= 2:
        variation = max(signal_history) - min(signal_history)
        stability = max(0, min(100, 100 - variation * 6))
    else:
        stability = 100

    health = int(
        sp * 0.35 +
        np * 0.20 +
        snrp * 0.30 +
        stability * 0.15
    )

    q, icon = quality(rssi)
    sq, sicon = snr_quality(snr)

    info_values["Signal"].config(text=f"{rssi} dBm")
    info_values["Noise"].config(text=f"{noise} dBm")
    info_values["SNR"].config(text=f"{snr} dB")
    info_values["Channel"].config(text=data["channel"])
    info_values["Band"].config(
        text=band_from_channel(data["channel"])
    )
    info_values["Tx Rate"].config(text=data["tx"])
    info_values["Wi-Fi Standard"].config(text=data["phy"])

    quality_label.config(
        text=f"{icon} {q}"
    )

    snr_quality_label.config(
        text=f"{sicon} SNR: {snr} dB — {sq}"
    )

    # Graph
    x = list(range(len(signal_history)))

    signal_line.set_data(
        x,
        list(signal_history)
    )
    noise_line.set_data(
        x,
        list(noise_history)
    )

    ax.set_xlim(
        0,
        max(10, len(signal_history) - 1)
    )

    if len(elapsed_history) > 1:
        positions = list(range(
            0,
            len(elapsed_history),
            max(1, len(elapsed_history) // 5)
        ))

        if positions[-1] != len(elapsed_history) - 1:
            positions.append(
                len(elapsed_history) - 1
            )

        ax.set_xticks(positions)
        ax.set_xticklabels(
            [
                f"{list(elapsed_history)[i]}s"
                for i in positions
            ],
            color=WHITE
        )

    canvas.draw_idle()

    # Ping
    if ping_history:
        current = ping_history[-1]
        average = sum(ping_history) / len(ping_history)

        if len(ping_history) >= 2:
            jitter = sum(
                abs(
                    ping_history[i] -
                    ping_history[i - 1]
                )
                for i in range(1, len(ping_history))
            ) / (len(ping_history) - 1)
        else:
            jitter = 0

        loss = (
            max(
                0,
                1 - ping_success / ping_total
            ) * 100
            if ping_total else 0
        )

        if current < 30:
            ping_icon = "🟢"
        elif current < 70:
            ping_icon = "🟡"
        else:
            ping_icon = "🔴"

        performance_label.config(
            text=(
                f"{ping_icon} Ping {current:.1f} ms  •  "
                f"Avg {average:.1f} ms  •  "
                f"Jitter {jitter:.1f} ms  •  "
                f"Loss {loss:.1f}%"
            )
        )

    else:
        performance_label.config(
            text="🔄 Testing internet latency..."
        )

    # Health
    if health >= 75:
        health_icon = "🟢"
        msg = "Good wireless conditions"
    elif health >= 50:
        health_icon = "🟡"
        msg = "Connection is acceptable"
    else:
        health_icon = "🔴"
        msg = "Poor wireless conditions"

    health_label.config(
        text=f"{health_icon} Health Score: {health}/100  |  {health}%"
    )

    details_label.config(
        text=(
            f"Signal {sp}%  •  "
            f"Noise {np}%  •  "
            f"SNR {snrp}%  •  "
            f"Stability {stability}%"
        )
    )

    recommendation_label.config(
        text=f"{health_icon} {msg}"
    )

    status.config(text="Connected")

    root.after(
        UPDATE_MS,
        update_dashboard
    )


# ============================================================
# START
# ============================================================

update_dashboard()
root.mainloop()
